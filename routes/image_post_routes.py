"""routes/image_post_routes.py — 画像起点記事生成フロー"""
import os
import uuid
import threading
import logging
from flask import render_template, request, jsonify, redirect, url_for, abort, current_app
from flask_login import login_required, current_user
from models import db, Client, Post, PostImage
from routes import designer_bp

logger = logging.getLogger(__name__)

# バックグラウンドジョブのステート管理（in-memory）
_image_post_jobs: dict[str, dict] = {}

_ALLOWED_EXTS = {"jpg", "jpeg", "png"}  # Instagram API は WebP/GIF 非対応のため除外
_MAX_IMAGES = 5


def _allowed(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in _ALLOWED_EXTS


def _assert_access(client: Client):
    if not current_user.can_access_client(client.id):
        abort(403)


# ─── Step 1: アップロード画面 ─────────────────────────────────────────────────

@designer_bp.route("/clients/<int:client_id>/image-post/new")
@login_required
def image_post_new(client_id: int):
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    from stripe_utils import is_client_operational
    if not is_client_operational(client, current_user):
        from flask import flash
        flash("このクライアントは現在利用停止中です", "error")
        return redirect(url_for("designer.client_detail", client_id=client_id))
    return render_template(
        "designer/image_post/step1_upload.html",
        client=client,
        max_images=_MAX_IMAGES,
    )


# ─── 画像アップロード（AJAX） ────────────────────────────────────────────────

@designer_bp.route("/clients/<int:client_id>/image-post/generate", methods=["POST"])
@login_required
def image_post_generate(client_id: int):
    """画像を受け取り、バックグラウンドで Stage0 → 記事生成 を実行する。"""
    try:
        return _image_post_generate_impl(client_id)
    except Exception as e:
        logger.error(f"[image_post_generate] Unexpected error: {e}", exc_info=True)
        try:
            db.session.rollback()
        except Exception:
            pass
        return jsonify({"error": f"サーバーエラー: {type(e).__name__}: {e}"}), 500


def _image_post_generate_impl(client_id: int):
    client = Client.query.get_or_404(client_id)
    _assert_access(client)

    files = request.files.getlist("images")
    post_type = request.form.get("post_type", "feed")  # "feed" | "reel"

    if not files or all(f.filename == "" for f in files):
        return jsonify({"error": "画像を選択してください"}), 400

    valid_files = [f for f in files if f and _allowed(f.filename)]
    if not valid_files:
        return jsonify({"error": "対応していないファイル形式です（jpg/png/webp/gif）"}), 400
    if len(valid_files) > _MAX_IMAGES:
        return jsonify({"error": f"画像は最大{_MAX_IMAGES}枚までです"}), 400

    # 画像を一時保存
    base_dir = current_app.root_path
    save_dir = os.path.join(base_dir, "static", "uploads", "companies",
                            str(client_id), "image_posts", "tmp")
    os.makedirs(save_dir, exist_ok=True)

    saved_paths = []
    for f in valid_files:
        ext = f.filename.rsplit(".", 1)[1].lower()
        fname = f"{uuid.uuid4().hex}.{ext}"
        path = os.path.join(save_dir, fname)
        f.save(path)
        saved_paths.append(path)

    # Post プレースホルダー作成
    placeholder = Post(
        client_id=client_id,
        created_by_designer_id=current_user.id,
        title="（画像から生成中...）",
        status="creating",
    )
    db.session.add(placeholder)
    db.session.flush()
    post_id = placeholder.id
    db.session.commit()

    # ジョブ登録
    job_id = str(uuid.uuid4())
    _image_post_jobs[job_id] = {
        "status": "running",
        "step": "init",
        "step_label": "準備中...",
        "post_id": post_id,
        "error": None,
    }

    # バックグラウンド実行用パラメータを先取り
    app = current_app._get_current_object()
    client_id_val       = client.id
    client_name         = client.name
    business_desc       = client.business_description or ""
    target_word_count   = client.target_word_count or 0
    article_taste       = client.article_taste or "standard"
    target_audience     = client.target_audience or ""
    character_prompt    = client.character_prompt or ""
    wp_sample_posts     = []
    try:
        import json as _json
        wp_sample_posts = _json.loads(client.wp_sample_posts_json or "[]")
    except Exception:
        pass
    hp_design_prompt    = client.hp_design_prompt or ""
    platform_type       = client.platform_type or "wordpress"
    threads_limit       = 400 if (client.threads_user_id or "").strip() else 0
    image_gen_enabled   = bool(getattr(client, "image_gen_enabled", False))
    image_taste         = getattr(client, "image_taste", "business_clean") or "business_clean"
    image_balance       = getattr(client, "image_balance", "balanced") or "balanced"
    image_aspect_ratio  = getattr(client, "image_aspect_ratio", "1:1") or "1:1"
    image_base_prompt   = getattr(client, "image_base_prompt", "") or ""
    image_sample_paths  = [p for p in [
        getattr(client, "sample_image_1_path", "") or "",
        getattr(client, "sample_image_2_path", "") or "",
        getattr(client, "sample_image_3_path", "") or "",
    ] if p]
    image_count         = int(getattr(client, "image_count_per_post", 1) or 1)

    def _run():
        job = _image_post_jobs[job_id]
        try:
            from image_post_gen import extract_text_from_images, build_topic_from_images
            from agents.blog_creator import BlogCreatorAgent
            from agents.seo_checker import SeoCheckerAgent
            from agents.fact_checker import FactCheckerAgent
            from agents.legal_checker import LegalCheckerAgent
            from agents.final_creator import FinalCreatorAgent
            from agents.ig_formatter import IgFormatterAgent

            # ── Stage 0-a: 画像テキスト抽出 ────────────────────────────────
            job.update(step="extract", step_label=f"画像を分析中... (0/{len(saved_paths)})")
            results = []
            for i, path in enumerate(saved_paths, start=1):
                from image_post_gen import extract_text_from_image
                r = extract_text_from_image(path)
                r["index"] = i
                r["file"] = os.path.basename(path)
                results.append(r)
                job["step_label"] = f"画像を分析中... ({i}/{len(saved_paths)})"

            # ── Stage 0-b: トピック・アウトライン生成 ───────────────────────
            job.update(step="build_topic", step_label="記事の構成を考えています...")
            topic_data = build_topic_from_images(
                extracted_results=results,
                client_name=client_name,
                business_description=business_desc,
                target_word_count=target_word_count,
                target_audience=target_audience,
                character_prompt=character_prompt,
                article_taste=article_taste,
            )
            topic_title   = topic_data.get("title") or "（タイトル未生成）"
            topic_outline = topic_data.get("outline") or ""

            # ── Stage 1: 下書き作成 ─────────────────────────────────────────
            job.update(step="blog_creator", step_label="記事の下書きを作成中...")
            draft = BlogCreatorAgent().run({
                "topic": topic_title,
                "keywords": topic_outline,
                "tone": "標準",
                "word_count": target_word_count,
                "existing_posts": wp_sample_posts,
                "design_prompt": hp_design_prompt,
                "taste": article_taste,
                "target_audience": target_audience,
                "character_prompt": character_prompt,
                "business_description": business_desc,
            })

            # ── Stage 2: SEO最適化 ──────────────────────────────────────────
            job.update(step="seo_checker", step_label="SEO最適化中...")
            seo_draft = SeoCheckerAgent().run({
                "draft": draft,
                "topic": topic_title,
                "keywords": topic_outline,
            })

            # ── Stage 3: ファクトチェック ───────────────────────────────────
            job.update(step="fact_checker", step_label="ファクトチェック中...")
            fact_check = FactCheckerAgent().run({"draft": seo_draft})

            # ── Stage 4: リーガルチェック ───────────────────────────────────
            job.update(step="legal_checker", step_label="リーガルチェック中...")
            legal_check = LegalCheckerAgent().run({"draft": seo_draft})

            # ── Stage 5: 最終記事生成 ───────────────────────────────────────
            job.update(step="final_creator", step_label="最終記事を仕上げています...")
            final_content = FinalCreatorAgent().run({
                "draft": seo_draft,
                "content_check": fact_check,
                "legal_check": legal_check,
                "topic": topic_title,
                "keywords": topic_outline,
                "tone": "標準",
                "word_count": target_word_count,
            })

            # ── Stage 6: IGキャプション生成 ────────────────────────────────
            job.update(step="ig_formatter", step_label="Instagramキャプション生成中...")
            ig_caption_raw = IgFormatterAgent().run({
                "blog_content": final_content,
                "topic": topic_title,
                "client_name": client_name,
                "threads_limit": threads_limit,
                "word_count": target_word_count,
            })

            # ── Stage 7: 保存 ───────────────────────────────────────────────
            job.update(step="saving", step_label="保存中...")
            import re as _re, markdown as _md
            from caption_utils import strip_account_prefix

            def _clean_caption(cap: str) -> str:
                cap = strip_account_prefix(cap, client_name)
                lines = cap.splitlines()
                cleaned = [
                    _re.sub(r'\s*#\S+', '', l).strip()
                    for l in lines
                    if _re.sub(r'#\S+', '', l).strip()
                ]
                return _re.sub(r'\n{3,}', '\n\n', '\n'.join(cleaned)).strip()

            def _normalize_md(text: str) -> str:
                """段落間・見出し前後に空行を保証してMarkdown → HTML変換を正確にする。"""
                text = _re.sub(r'([^\n])\n(#{1,6} )', r'\1\n\n\2', text)
                text = _re.sub(r'\n{3,}', '\n\n', text)
                return text

            if platform_type == "instagram":
                body_html = ""
            else:
                body_html = _md.markdown(_normalize_md(final_content), extensions=["extra", "toc", "nl2br"])

            with app.app_context():
                from models import Post as _Post, PostImage as _PI, Client as _Client, db as _db
                from schedule_utils import next_scheduled_at
                post = _Post.query.get(post_id)
                client_obj = _Client.query.get(client_id_val)
                if post:
                    post.title      = topic_title
                    post.outline    = topic_outline
                    post.body_html  = body_html
                    post.ig_caption = _clean_caption(ig_caption_raw)
                    post.status     = "draft"

                    # 自動予約（トピック起点と同じロジック）
                    if client_obj and getattr(client_obj, "schedule_type", None):
                        existing = {p.scheduled_at.date() for p in _Post.query.filter(
                            _Post.client_id == client_id_val,
                            _Post.scheduled_at.isnot(None),
                        ).all() if p.scheduled_at}
                        post.scheduled_at = next_scheduled_at(client_obj, existing)

                    # アップロード画像を PostImage として紐付け（絶対URLで保存）
                    _base_url = os.getenv("BASE_URL", "").rstrip("/")
                    rel_base = "uploads/companies/{}/image_posts/tmp".format(client_id_val)
                    for idx, path in enumerate(saved_paths, start=1):
                        fname = os.path.basename(path)
                        img_url = (
                            f"{_base_url}/static/{rel_base}/{fname}"
                            if _base_url else
                            f"/static/{rel_base}/{fname}"
                        )
                        pi = _PI(post_id=post.id, image_url=img_url, sort_order=idx)
                        _db.session.add(pi)

                    _db.session.commit()
                    job["post_id"] = post.id

                # AI 画像生成（オプション）
                if image_gen_enabled and post:
                    job.update(step="ai_image", step_label="AI画像を生成中...")
                    try:
                        from ai_image_gen import generate_images_for_post as _gen_imgs
                        img_urls = _gen_imgs(
                            title=topic_title,
                            body_html=body_html,
                            taste=image_taste,
                            aspect_ratio=image_aspect_ratio,
                            client_id=client_id_val,
                            balance=image_balance,
                            client_name=client_name,
                            base_prompt=image_base_prompt,
                            sample_image_paths=image_sample_paths,
                            count=image_count,
                        )
                        with app.app_context():
                            from models import Post as _P2, PostImage as _PI2, db as _db2
                            p2 = _P2.query.get(post_id)
                            if p2:
                                for i_url, url in enumerate(img_urls, start=1):
                                    _db2.session.add(_PI2(
                                        post_id=p2.id, image_url=url,
                                        sort_order=len(saved_paths) + i_url,
                                    ))
                                _db2.session.commit()
                    except Exception as e:
                        logger.warning(f"[image_post] AI画像生成失敗: {e}")

            job.update(status="done", step="done", step_label="完了")

        except Exception as e:
            logger.error(f"[image_post] 生成エラー: {e}", exc_info=True)
            job.update(status="error", step="error", step_label="エラーが発生しました", error=str(e))
            with app.app_context():
                try:
                    from models import Post as _Post, db as _db
                    p = _Post.query.get(post_id)
                    if p:
                        p.status = "failed"
                        _db.session.commit()
                except Exception:
                    pass

    t = threading.Thread(target=_run, daemon=False)
    t.start()

    return jsonify({"job_id": job_id, "post_id": post_id})


# ─── ジョブ状態ポーリング ────────────────────────────────────────────────────

@designer_bp.route("/clients/<int:client_id>/image-post/status/<job_id>")
@login_required
def image_post_status(client_id: int, job_id: str):
    job = _image_post_jobs.get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    return jsonify({
        "status":     job.get("status"),
        "step":       job.get("step"),
        "step_label": job.get("step_label"),
        "post_id":    job.get("post_id"),
        "error":      job.get("error"),
    })
