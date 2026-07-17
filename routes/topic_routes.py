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


def _assert_access(client: Client):
    if not current_user.can_access_client(client.id):
        abort(403)


@designer_bp.route("/clients/<int:client_id>/topics")
@login_required
def topic_list(client_id: int):
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    topics = (
        TopicQueue.query.filter_by(client_id=client_id, status="pending")
        .order_by(TopicQueue.sort_order, TopicQueue.id)
        .all()
    )
    pending_count = len(topics)
    return render_template(
        "designer/topics/list.html",
        client=client,
        topics=topics,
        pending_count=pending_count,
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


# ─── ネタ一括生成（管理者のみ）────────────────────────────────────────────────

@designer_bp.route("/clients/<int:client_id>/topics/generate-ideas", methods=["POST"])
@login_required
def generate_ideas(client_id: int):
    """テーマをもとにAIが記事ネタを10件生成してキューに追加する（管理者のみ）"""
    if current_user.role != "admin":
        abort(403)
    client = Client.query.get_or_404(client_id)
    _assert_access(client)

    themes = (client.themes or "").strip()
    if not themes:
        return jsonify({"success": False, "reason": "企業設定にテーマが登録されていません。先に設定画面でテーマを入力してください。"})

    # 重複回避のため既存のタイトルを収集
    existing_titles = [t.title for t in TopicQueue.query.filter_by(client_id=client_id).all()]
    existing_posts  = [p.title for p in Post.query.filter_by(client_id=client_id).all()]
    avoid_titles = existing_titles + existing_posts

    try:
        import anthropic as _anthropic
        from config import Config

        avoid_section = ""
        if avoid_titles:
            avoid_section = "\n\n【重複禁止】以下のタイトルと内容が被らないようにすること：\n" + "\n".join(f"- {t}" for t in avoid_titles[:30])

        prompt = f"""あなたはコンテンツプランナーです。
以下のテーマをもとに、ブログ記事ネタを10件考えてください。

企業名: {client.name}
テーマ:
{themes}
{avoid_section}

条件:
- 10件すべて異なる切り口にすること
- 読者の悩みや関心に刺さるタイトルにすること
- 大枠は記事の方向性を2〜3文で簡潔に記載すること

以下のJSON形式のみで出力してください。他のテキストは一切含めないこと:
[
  {{"title": "記事タイトル", "outline": "記事の大枠・方向性"}},
  ...
]"""

        ai = _anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
        message = ai.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )

        text = message.content[0].text
        m = re.search(r'\[[\s\S]*\]', text)
        if not m:
            raise ValueError("AIの応答からJSONが見つかりませんでした")
        ideas = json.loads(m.group())

        # 末尾のsort_orderを取得
        last = (
            TopicQueue.query.filter_by(client_id=client_id, status="pending")
            .order_by(TopicQueue.sort_order.desc())
            .first()
        )
        next_order = (last.sort_order + 1) if last else 1

        added = 0
        for idea in ideas:
            title = (idea.get("title") or "").strip()
            outline = (idea.get("outline") or "").strip()
            if not title:
                continue
            db.session.add(TopicQueue(
                client_id=client_id,
                title=title,
                outline=outline,
                sort_order=next_order + added,
                created_by="ai_auto",
                created_by_designer_id=current_user.id,
            ))
            added += 1

        db.session.commit()
        return jsonify({"success": True, "added": added})

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "reason": str(e)}), 500


# ─── AI生成（管理者のみ）──────────────────────────────────────────────────────

@designer_bp.route("/clients/<int:client_id>/topics/<int:topic_id>/generate-ui")
@login_required
def topic_generate_ui(client_id: int, topic_id: int):
    """生成進捗ページを表示する（管理者のみ）"""
    if current_user.role != "admin":
        abort(403)
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    topic = TopicQueue.query.get_or_404(topic_id)
    if topic.client_id != client_id:
        abort(403)
    if topic.status != "pending":
        flash("このネタはすでに生成済みです", "warning")
        return redirect(url_for("designer.topic_list", client_id=client_id))
    return render_template("designer/topics/generate.html", client=client, topic=topic)


@designer_bp.route("/clients/<int:client_id>/topics/<int:topic_id>/generate", methods=["POST"])
@login_required
def topic_generate(client_id: int, topic_id: int):
    """4エージェントパイプラインをバックグラウンドで起動し run_id を返す（管理者のみ）"""
    if current_user.role != "admin":
        abort(403)
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    topic = TopicQueue.query.get_or_404(topic_id)
    if topic.client_id != client_id:
        abort(403)
    if topic.status != "pending":
        return jsonify({"success": False, "reason": "このネタはすでに生成済みです"})

    run_id = str(uuid.uuid4())
    _generation_runs[run_id] = {
        "status": "running",
        "step": "init",
        "step_num": 0,
        "post_id": None,
        "error": None,
    }

    # バックグラウンドスレッドに渡す値を先に取り出す
    app = current_app._get_current_object()
    client_id_val = client.id
    client_name = client.name
    topic_id_val = topic.id
    topic_title = topic.title
    topic_outline = topic.outline or ""
    designer_id = current_user.id

    def _run():
        run = _generation_runs[run_id]
        try:
            from agents.blog_creator import BlogCreatorAgent
            from agents.content_checker import ContentCheckerAgent
            from agents.legal_checker import LegalCheckerAgent
            from agents.final_creator import FinalCreatorAgent
            import anthropic as _anthropic
            import markdown as _md
            from config import Config

            # Step 1: 下書き作成
            run.update(step="blog_creator", step_num=1)
            draft = "".join(BlogCreatorAgent().stream({
                "topic": topic_title,
                "keywords": topic_outline,
                "tone": "標準",
                "word_count": "1200",
                "existing_posts": [],
            }))

            # Step 2: コンテンツチェック
            run.update(step="content_checker", step_num=2)
            content_check = "".join(ContentCheckerAgent().stream({"draft": draft}))

            # Step 3: リーガルチェック
            run.update(step="legal_checker", step_num=3)
            legal_check = "".join(LegalCheckerAgent().stream({"draft": draft}))

            # Step 4: 最終記事生成
            run.update(step="final_creator", step_num=4)
            final_content = "".join(FinalCreatorAgent().stream({
                "draft": draft,
                "content_check": content_check,
                "legal_check": legal_check,
                "topic": topic_title,
                "keywords": topic_outline,
                "tone": "標準",
                "word_count": "1200",
            }))

            # Step 5: IGキャプション生成
            run.update(step="ig_caption", step_num=5)
            ai = _anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
            ig_resp = ai.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                messages=[{"role": "user", "content": f"""以下のブログ記事をもとに、Instagramキャプションを作成してください。
- 日本語・2200文字以内
- 記事の要点を魅力的にまとめる
- ハッシュタグは含めない（別途自動追加されます）
- キャプション本文のみ出力

---
{final_content[:3000]}
---"""}],
            )
            ig_caption = ig_resp.content[0].text.strip()

            # Step 6: 保存
            run.update(step="saving", step_num=6)
            body_html = _md.markdown(final_content, extensions=["extra", "toc"])

            with app.app_context():
                post = Post(
                    client_id=client_id_val,
                    created_by_designer_id=designer_id,
                    title=topic_title,
                    outline=topic_outline,
                    body_html=body_html,
                    ig_caption=ig_caption,
                    status="draft",
                )
                db.session.add(post)
                db.session.flush()

                topic_obj = TopicQueue.query.get(topic_id_val)
                if topic_obj:
                    topic_obj.status = "generated"
                    topic_obj.generated_post_id = post.id
                db.session.commit()
                run["post_id"] = post.id

            run.update(status="done", step="done", step_num=6)

        except Exception as e:
            run.update(status="error", error=str(e))

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
