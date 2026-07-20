"""routes/topic_routes.py — 記事ネタキュー管理"""
import uuid
import re
import json
import threading
from flask import render_template, request, redirect, url_for, flash, jsonify, abort, current_app
from flask_login import login_required, current_user
from models import db, Client, TopicQueue, Post
from routes import designer_bp

# バックグラウンド生成ジョブのステート管理（in-memory）
_generation_runs = {}
# topic_id → run_id の逆引きマップ（進捗確認ページの再接続用）
_topic_to_run: dict[int, str] = {}


def _clean_ig_caption(caption: str, client_name: str = "") -> str:
    from caption_utils import strip_account_prefix
    return strip_account_prefix(caption, client_name)


def _assert_access(client: Client):
    if not current_user.can_access_client(client.id):
        abort(403)


@designer_bp.route("/clients/<int:client_id>/topics")
@login_required
def topic_list(client_id: int):
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    topics = (
        TopicQueue.query.filter_by(client_id=client_id)
        .filter(TopicQueue.status.in_(["pending", "processing"]))
        .order_by(TopicQueue.sort_order, TopicQueue.id)
        .all()
    )
    pending_count = sum(1 for t in topics if t.status == "pending")
    processing_count = sum(1 for t in topics if t.status == "processing")
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    _now = _dt.now(_tz(_td(hours=9)))
    _month_start = _dt(_now.year, _now.month, 1)
    draft_count = Post.query.filter(
        Post.client_id == client_id,
        Post.status.in_(["creating", "draft", "approved", "scheduled"]),
        Post.created_at >= _month_start,
    ).count()
    monthly_limit = client.monthly_post_count or 4
    can_generate = draft_count < monthly_limit
    remaining_count = max(0, monthly_limit - draft_count)
    return render_template(
        "designer/topics/list.html",
        client=client,
        topics=topics,
        pending_count=pending_count,
        processing_count=processing_count,
        draft_count=draft_count,
        monthly_limit=monthly_limit,
        can_generate=can_generate,
        remaining_count=remaining_count,
    )


@designer_bp.route("/clients/<int:client_id>/topics/add", methods=["POST"])
@login_required
def topic_add(client_id: int):
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    title = request.form.get("title", "").strip()
    outline = request.form.get("outline", "").strip()
    if not title:
        flash("タイトルは必須です", "error")
        return redirect(url_for("designer.topic_list", client_id=client_id))

    last = (
        TopicQueue.query.filter_by(client_id=client_id, status="pending")
        .order_by(TopicQueue.sort_order.desc())
        .first()
    )
    next_order = (last.sort_order + 1) if last else 1

    topic = TopicQueue(
        client_id=client_id,
        title=title,
        outline=outline,
        sort_order=next_order,
        created_by="designer",
        created_by_designer_id=current_user.id,
    )
    db.session.add(topic)
    db.session.commit()
    flash(f"「{title}」を追加しました", "success")
    return redirect(url_for("designer.topic_list", client_id=client_id))


@designer_bp.route("/clients/<int:client_id>/topics/<int:topic_id>/edit", methods=["POST"])
@login_required
def topic_edit(client_id: int, topic_id: int):
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    topic = TopicQueue.query.get_or_404(topic_id)
    if topic.client_id != client_id:
        abort(403)

    topic.title = request.form.get("title", topic.title).strip()
    topic.outline = request.form.get("outline", topic.outline).strip()
    db.session.commit()
    return jsonify({"success": True})


@designer_bp.route("/clients/<int:client_id>/topics/<int:topic_id>/delete", methods=["POST"])
@login_required
def topic_delete(client_id: int, topic_id: int):
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    topic = TopicQueue.query.get_or_404(topic_id)
    if topic.client_id != client_id:
        abort(403)
    db.session.delete(topic)
    db.session.commit()
    flash("削除しました", "success")
    return redirect(url_for("designer.topic_list", client_id=client_id))


@designer_bp.route("/clients/<int:client_id>/topics/reorder", methods=["POST"])
@login_required
def topic_reorder(client_id: int):
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    order = request.json.get("order", [])
    for i, topic_id in enumerate(order, 1):
        TopicQueue.query.filter_by(id=topic_id, client_id=client_id).update(
            {"sort_order": i}
        )
    db.session.commit()
    return jsonify({"success": True})


# ─── AI生成 ────────────────────────────────────────────────────────────────

@designer_bp.route("/clients/<int:client_id>/topics/<int:topic_id>/generate-ui")
@login_required
def topic_generate_ui(client_id: int, topic_id: int):
    """生成進捗ページを表示する"""
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    topic = TopicQueue.query.get_or_404(topic_id)
    if topic.client_id != client_id:
        abort(403)
    if topic.status == "generated":
        flash("このネタはすでに生成済みです", "warning")
        return redirect(url_for("designer.topic_list", client_id=client_id))
    return render_template("designer/topics/generate.html", client=client, topic=topic)


@designer_bp.route("/clients/<int:client_id>/topics/<int:topic_id>/generate", methods=["POST"])
@login_required
def topic_generate(client_id: int, topic_id: int):
    """4エージェントパイプラインをバックグラウンドで起動し run_id を返す"""
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    topic = TopicQueue.query.get_or_404(topic_id)
    if topic.client_id != client_id:
        abort(403)
    if topic.status != "pending":
        return jsonify({"success": False, "reason": "このネタはすでに生成済みか処理中です"})
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    _now = _dt.now(_tz(_td(hours=9)))
    _month_start = _dt(_now.year, _now.month, 1)
    draft_count = Post.query.filter(
        Post.client_id == client_id,
        Post.status.in_(["creating", "draft", "approved", "scheduled"]),
        Post.created_at >= _month_start,
    ).count()
    monthly_limit = client.monthly_post_count or 4
    if draft_count >= monthly_limit:
        return jsonify({"success": False, "reason": f"今月の生成数が月間契約数({monthly_limit}件)に達しています"})

    # 即座にDBへ処理中マーク＋プレースホルダー投稿を作成（ページ離脱後も状態が見える）
    topic.status = "processing"
    placeholder = Post(
        client_id=client_id,
        created_by_designer_id=current_user.id,
        title=topic.title,
        outline=topic.outline or "",
        body_html="",
        ig_caption="",
        status="creating",
    )
    db.session.add(placeholder)
    db.session.flush()
    post_id = placeholder.id
    topic.generated_post_id = post_id
    db.session.commit()

    run_id = str(uuid.uuid4())
    _generation_runs[run_id] = {
        "status": "running",
        "step": "init",
        "step_num": 0,
        "post_id": post_id,
        "error": None,
        "cancel_requested": False,
    }
    _topic_to_run[topic.id] = run_id

    # バックグラウンドスレッドに渡す値を先に取り出す
    import json as _json_mod
    app = current_app._get_current_object()
    client_id_val = client.id
    topic_id_val = topic.id
    topic_title = topic.title
    topic_outline = topic.outline or ""
    designer_id = current_user.id
    platform_type = client.platform_type or "wordpress"
    client_name = client.name
    wp_sample_posts   = _json_mod.loads(client.wp_sample_posts_json or "[]") if client.wp_sample_posts_json else []
    hp_design_prompt  = client.hp_design_prompt or ""
    article_taste     = client.article_taste or "standard"
    target_word_count = client.target_word_count or 0

    def _run():
        run = _generation_runs[run_id]

        def _cancel_and_cleanup():
            run.update(status="cancelled", step="cancelled")
            _topic_to_run.pop(topic_id_val, None)
            with app.app_context():
                try:
                    from models import Post as _Post, TopicQueue as _TQ, db as _db
                    p = _Post.query.get(post_id)
                    if p:
                        _db.session.delete(p)
                    tq = _TQ.query.get(topic_id_val)
                    if tq:
                        tq.status = "pending"
                        tq.generated_post_id = None
                    _db.session.commit()
                except Exception:
                    try:
                        from models import db as _db
                        _db.session.rollback()
                    except Exception:
                        pass

        try:
            # ── Instagram: 4エージェント + IG フォーマッター ──────────────────
            if platform_type == "instagram":
                from agents.blog_creator import BlogCreatorAgent
                from agents.content_checker import ContentCheckerAgent
                from agents.legal_checker import LegalCheckerAgent
                from agents.final_creator import FinalCreatorAgent
                from agents.ig_formatter import IgFormatterAgent

                # Step 1: 下書き作成
                run.update(step="blog_creator", step_num=1)
                draft = BlogCreatorAgent().run({
                    "topic": topic_title,
                    "keywords": topic_outline,
                    "tone": "標準",
                    "word_count": target_word_count,
                    "existing_posts": wp_sample_posts,
                    "design_prompt": hp_design_prompt,
                    "taste": article_taste,
                })
                if run.get("cancel_requested"):
                    _cancel_and_cleanup(); return

                # Step 2: コンテンツチェック
                run.update(step="content_checker", step_num=2)
                content_check = ContentCheckerAgent().run({"draft": draft})
                if run.get("cancel_requested"):
                    _cancel_and_cleanup(); return

                # Step 3: リーガルチェック
                run.update(step="legal_checker", step_num=3)
                legal_check = LegalCheckerAgent().run({"draft": draft})
                if run.get("cancel_requested"):
                    _cancel_and_cleanup(); return

                # Step 4: 最終記事生成
                run.update(step="final_creator", step_num=4)
                final_content = FinalCreatorAgent().run({
                    "draft": draft,
                    "content_check": content_check,
                    "legal_check": legal_check,
                    "topic": topic_title,
                    "keywords": topic_outline,
                    "tone": "標準",
                })
                if run.get("cancel_requested"):
                    _cancel_and_cleanup(); return

                # Step 5: IG フォーマッター（プレーンテキスト 1000文字 + ハッシュタグ）
                run.update(step="ig_formatter", step_num=5)
                ig_caption = IgFormatterAgent().run({
                    "blog_content": final_content,
                    "topic": topic_title,
                    "client_name": client_name,
                })
                if run.get("cancel_requested"):
                    _cancel_and_cleanup(); return

                # Step 6: 保存 + スケジュール自動設定
                run.update(step="saving", step_num=6)
                with app.app_context():
                    from schedule_utils import next_scheduled_at
                    from models import Post as _Post, Client as _Client
                    post = _Post.query.get(post_id)
                    client_obj = _Client.query.get(client_id_val)
                    if post:
                        post.body_html  = ""
                        post.ig_caption = _clean_ig_caption(ig_caption, client_name)
                        post.status     = "draft"
                        if client_obj and client_obj.schedule_type:
                            existing = {
                                p.scheduled_at.date()
                                for p in _Post.query.filter(
                                    _Post.client_id == client_id_val,
                                    _Post.scheduled_at.isnot(None),
                                ).all() if p.scheduled_at
                            }
                            post.scheduled_at = next_scheduled_at(client_obj, existing)
                    topic_obj = TopicQueue.query.get(topic_id_val)
                    if topic_obj:
                        topic_obj.status = "generated"
                    db.session.commit()
                    run["post_id"] = post_id

                run.update(status="done", step="done", step_num=6)
                _topic_to_run.pop(topic_id_val, None)

            # ── WordPress / その他: 4エージェント + IGフォーマッター ──────────
            else:
                from agents.blog_creator import BlogCreatorAgent
                from agents.content_checker import ContentCheckerAgent
                from agents.legal_checker import LegalCheckerAgent
                from agents.final_creator import FinalCreatorAgent
                from agents.ig_formatter import IgFormatterAgent
                import markdown as _md

                # Step 1: 下書き作成
                run.update(step="blog_creator", step_num=1)
                draft = BlogCreatorAgent().run({
                    "topic": topic_title,
                    "keywords": topic_outline,
                    "tone": "標準",
                    "word_count": target_word_count,
                    "existing_posts": wp_sample_posts,
                    "design_prompt": hp_design_prompt,
                    "taste": article_taste,
                })
                if run.get("cancel_requested"):
                    _cancel_and_cleanup(); return

                # Step 2: コンテンツチェック
                run.update(step="content_checker", step_num=2)
                content_check = ContentCheckerAgent().run({"draft": draft})
                if run.get("cancel_requested"):
                    _cancel_and_cleanup(); return

                # Step 3: リーガルチェック
                run.update(step="legal_checker", step_num=3)
                legal_check = LegalCheckerAgent().run({"draft": draft})
                if run.get("cancel_requested"):
                    _cancel_and_cleanup(); return

                # Step 4: 最終記事生成
                run.update(step="final_creator", step_num=4)
                final_content = FinalCreatorAgent().run({
                    "draft": draft,
                    "content_check": content_check,
                    "legal_check": legal_check,
                    "topic": topic_title,
                    "keywords": topic_outline,
                    "tone": "標準",
                })
                if run.get("cancel_requested"):
                    _cancel_and_cleanup(); return

                # Step 5: IGキャプション生成（1000文字 + ハッシュタグ）
                run.update(step="ig_caption", step_num=5)
                ig_caption = IgFormatterAgent().run({
                    "blog_content": final_content,
                    "topic": topic_title,
                    "client_name": client_name,
                })
                if run.get("cancel_requested"):
                    _cancel_and_cleanup(); return

                # Step 6: プレースホルダーを更新して完成 + スケジュール自動設定
                run.update(step="saving", step_num=6)
                body_html = _md.markdown(final_content, extensions=["extra", "toc"])

                with app.app_context():
                    from schedule_utils import next_scheduled_at
                    from models import Post as _Post, Client as _Client
                    post = _Post.query.get(post_id)
                    client_obj = _Client.query.get(client_id_val)
                    if post:
                        post.body_html  = body_html
                        post.ig_caption = _clean_ig_caption(ig_caption, client_name)
                        post.status     = "draft"
                        if client_obj and client_obj.schedule_type:
                            existing = {
                                p.scheduled_at.date()
                                for p in _Post.query.filter(
                                    _Post.client_id == client_id_val,
                                    _Post.scheduled_at.isnot(None),
                                ).all() if p.scheduled_at
                            }
                            post.scheduled_at = next_scheduled_at(client_obj, existing)
                    topic_obj = TopicQueue.query.get(topic_id_val)
                    if topic_obj:
                        topic_obj.status = "generated"
                    db.session.commit()
                    run["post_id"] = post_id

                run.update(status="done", step="done", step_num=6)
                _topic_to_run.pop(topic_id_val, None)

        except Exception as e:
            run.update(status="error", error=str(e))
            _topic_to_run.pop(topic_id_val, None)
            with app.app_context():
                try:
                    post = Post.query.get(post_id)
                    if post:
                        post.status = "failed"
                        post.error_message = str(e)
                    topic_obj = TopicQueue.query.get(topic_id_val)
                    if topic_obj:
                        topic_obj.status = "pending"
                        topic_obj.generated_post_id = None
                    db.session.commit()
                except Exception:
                    db.session.rollback()

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"success": True, "run_id": run_id})


@designer_bp.route("/generate-status/<run_id>")
@login_required
def generate_status(run_id: str):
    """生成ジョブのステータスをポーリングで返す"""
    run = _generation_runs.get(run_id)
    if not run:
        return jsonify({"error": "not found"}), 404
    return jsonify(run)


@designer_bp.route("/clients/<int:client_id>/topics/<int:topic_id>/run-id")
@login_required
def topic_run_id(client_id: int, topic_id: int):
    """topic_id に対応する実行中の run_id を返す（ページ再接続用）"""
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    run_id = _topic_to_run.get(topic_id)
    if run_id and run_id in _generation_runs:
        return jsonify({"run_id": run_id, "run": _generation_runs[run_id]})
    return jsonify({"run_id": None})


@designer_bp.route("/generate-cancel/<run_id>", methods=["POST"])
@login_required
def generate_cancel(run_id: str):
    """生成ジョブにキャンセルフラグを立てる（次のステップ間で停止）"""
    run = _generation_runs.get(run_id)
    if not run:
        return jsonify({"error": "not found"}), 404
    run["cancel_requested"] = True
    return jsonify({"success": True})


@designer_bp.route("/clients/<int:client_id>/topics/bulk-generate", methods=["POST"])
@login_required
def topic_bulk_generate(client_id: int):
    """未生成の記事ネタを一括で記事に生成する（月間契約数まで）"""
    client = Client.query.get_or_404(client_id)
    _assert_access(client)

    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    _now = _dt.now(_tz(_td(hours=9)))
    _month_start = _dt(_now.year, _now.month, 1)
    draft_count = Post.query.filter(
        Post.client_id == client_id,
        Post.status.in_(["creating", "draft", "approved", "scheduled"]),
        Post.created_at >= _month_start,
    ).count()
    monthly_limit = client.monthly_post_count or 4
    remaining = monthly_limit - draft_count
    if remaining <= 0:
        return jsonify({"success": False, "reason": f"下書き記事数が月間契約数({monthly_limit}件)に達しています"})

    pending_topics = (
        TopicQueue.query.filter_by(client_id=client_id, status="pending")
        .order_by(TopicQueue.sort_order)
        .limit(remaining)
        .all()
    )
    if not pending_topics:
        return jsonify({"success": False, "reason": "生成対象のネタがありません"})

    app = current_app._get_current_object()
    platform_type = client.platform_type or "wordpress"
    client_name = client.name
    client_id_val = client.id
    designer_id = current_user.id
    import json as _json_mod
    wp_sample_posts = _json_mod.loads(client.wp_sample_posts_json or "[]") if client.wp_sample_posts_json else []
    hp_design_prompt = client.hp_design_prompt or ""

    run_ids = []

    for topic in pending_topics:
        topic.status = "processing"
        placeholder = Post(
            client_id=client_id_val,
            created_by_designer_id=designer_id,
            title=topic.title,
            outline=topic.outline or "",
            body_html="", ig_caption="", status="creating",
        )
        db.session.add(placeholder)
        db.session.flush()
        post_id = placeholder.id
        topic.generated_post_id = post_id
        db.session.commit()

        run_id = str(uuid.uuid4())
        _generation_runs[run_id] = {
            "status": "running", "step": "init",
            "step_num": 0, "post_id": post_id, "error": None, "cancel_requested": False,
        }
        _topic_to_run[topic.id] = run_id
        run_ids.append(run_id)

        def _run(run_id=run_id, topic_title=topic.title, topic_outline=topic.outline or "",
                 topic_id_val=topic.id, post_id=post_id,
                 platform_type=platform_type, client_id_val=client_id_val, client_name=client_name,
                 wp_sample_posts=wp_sample_posts, hp_design_prompt=hp_design_prompt):
            run = _generation_runs[run_id]

            def _cancel_and_cleanup():
                run.update(status="cancelled", step="cancelled")
                _topic_to_run.pop(topic_id_val, None)
                with app.app_context():
                    try:
                        from models import Post as _Post, TopicQueue as _TQ, db as _db
                        p = _Post.query.get(post_id)
                        if p:
                            _db.session.delete(p)
                        tq = _TQ.query.get(topic_id_val)
                        if tq:
                            tq.status = "pending"
                            tq.generated_post_id = None
                        _db.session.commit()
                    except Exception:
                        try:
                            from models import db as _db
                            _db.session.rollback()
                        except Exception:
                            pass

            try:
                from agents.blog_creator import BlogCreatorAgent
                from agents.content_checker import ContentCheckerAgent
                from agents.legal_checker import LegalCheckerAgent
                from agents.final_creator import FinalCreatorAgent
                from agents.ig_formatter import IgFormatterAgent
                import markdown as _md

                run.update(step="blog_creator", step_num=1)
                draft = BlogCreatorAgent().run({"topic": topic_title, "keywords": topic_outline, "tone": "標準", "existing_posts": wp_sample_posts, "design_prompt": hp_design_prompt})
                if run.get("cancel_requested"):
                    _cancel_and_cleanup(); return
                run.update(step="content_checker", step_num=2)
                content_check = ContentCheckerAgent().run({"draft": draft})
                if run.get("cancel_requested"):
                    _cancel_and_cleanup(); return
                run.update(step="legal_checker", step_num=3)
                legal_check = LegalCheckerAgent().run({"draft": draft})
                if run.get("cancel_requested"):
                    _cancel_and_cleanup(); return
                run.update(step="final_creator", step_num=4)
                final_content = FinalCreatorAgent().run({"draft": draft, "content_check": content_check, "legal_check": legal_check, "topic": topic_title, "keywords": topic_outline, "tone": "標準"})
                if run.get("cancel_requested"):
                    _cancel_and_cleanup(); return
                run.update(step="ig_caption", step_num=5)
                ig_caption = IgFormatterAgent().run({"blog_content": final_content, "topic": topic_title, "client_name": client_name})
                if run.get("cancel_requested"):
                    _cancel_and_cleanup(); return
                run.update(step="saving", step_num=6)

                body_html = "" if platform_type == "instagram" else _md.markdown(final_content, extensions=["extra", "toc"])

                with app.app_context():
                    from schedule_utils import next_scheduled_at
                    from models import Post as _Post, Client as _Client, TopicQueue as _TQ
                    post = _Post.query.get(post_id)
                    client_obj = _Client.query.get(client_id_val)
                    if post:
                        post.body_html = body_html
                        post.ig_caption = _clean_ig_caption(ig_caption, client_name)
                        post.status = "draft"
                        if client_obj and client_obj.schedule_type:
                            existing = {p.scheduled_at.date() for p in _Post.query.filter(_Post.client_id == client_id_val, _Post.scheduled_at.isnot(None)).all() if p.scheduled_at}
                            post.scheduled_at = next_scheduled_at(client_obj, existing)
                    tq = _TQ.query.get(topic_id_val)
                    if tq:
                        tq.status = "generated"
                    from models import db as _db
                    _db.session.commit()
                    run["post_id"] = post_id

                run.update(status="done", step="done", step_num=6)
                _topic_to_run.pop(topic_id_val, None)
            except Exception as e:
                run.update(status="error", error=str(e))
                _topic_to_run.pop(topic_id_val, None)
                with app.app_context():
                    try:
                        from models import Post as _Post, TopicQueue as _TQ, db as _db
                        post = _Post.query.get(post_id)
                        if post:
                            post.status = "failed"
                            post.error_message = str(e)
                        tq = _TQ.query.get(topic_id_val)
                        if tq:
                            tq.status = "pending"
                            tq.generated_post_id = None
                        _db.session.commit()
                    except Exception:
                        from models import db as _db
                        _db.session.rollback()

        threading.Thread(target=_run, daemon=True).start()

    return jsonify({"success": True, "run_ids": run_ids, "count": len(run_ids)})


@designer_bp.route("/clients/<int:client_id>/topics/<int:topic_id>/force-reset", methods=["POST"])
@login_required
def topic_force_reset(client_id: int, topic_id: int):
    """生成中でスタックしたネタを強制的に pending に戻す。"""
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    topic = TopicQueue.query.get_or_404(topic_id)
    if topic.client_id != client_id:
        abort(403)
    if topic.status != "processing":
        flash("このネタは生成中ではありません", "warning")
        return redirect(url_for("designer.topic_list", client_id=client_id))
    if topic.generated_post_id:
        stuck_post = Post.query.get(topic.generated_post_id)
        if stuck_post and stuck_post.status == "creating":
            db.session.delete(stuck_post)
    topic.status = "pending"
    topic.generated_post_id = None
    db.session.commit()
    flash("生成を強制終了し、ネタをキューに戻しました", "success")
    return redirect(url_for("designer.topic_list", client_id=client_id))


@designer_bp.route("/clients/<int:client_id>/topics/ai-idea", methods=["POST"])
@login_required
def topic_ai_idea(client_id: int):
    """管理者のみ: AIで記事ネタを1件生成してキューに追加する"""
    if current_user.role != "admin":
        abort(403)
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    try:
        from batch_monthly import _generate_ideas
        from config import Config
        import anthropic as _anthropic
        ai = _anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
        added = _generate_ideas(client, ai, 1, db)
        if added:
            t = added[0]
            return jsonify({"success": True, "topic": {"id": t.id, "title": t.title, "outline": t.outline or ""}})
        return jsonify({"success": False, "reason": "生成に失敗しました"})
    except ValueError as e:
        return jsonify({"success": False, "reason": str(e)})
    except Exception as e:
        return jsonify({"success": False, "reason": f"エラー: {str(e)}"})
