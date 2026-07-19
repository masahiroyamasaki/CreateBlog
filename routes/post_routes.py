"""routes/post_routes.py — 投稿コンテンツの管理・公開"""
import os
import uuid as _uuid_mod
from datetime import datetime, timezone, timedelta

_JST = timezone(timedelta(hours=9))

def _now_jst() -> datetime:
    return datetime.now(_JST).replace(tzinfo=None)
from flask import render_template, request, redirect, url_for, flash, jsonify, abort, current_app
from flask_login import login_required, current_user
from models import db, Client, Post, PostImage
from routes import designer_bp
import instagram as ig_client
import wp_client

_ALLOWED_EXTS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

def _upload_base():
    return os.path.join(current_app.root_path, 'static', 'uploads')

def _allowed_image(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in _ALLOWED_EXTS


def _assert_access(client: Client):
    if not current_user.can_access_client(client.id):
        abort(403)


# ──────────────────────────── 投稿一覧 ──────────────────────────────────────

@designer_bp.route("/clients/<int:client_id>/posts/auto-schedule", methods=["POST"])
@login_required
def post_auto_schedule(client_id: int):
    """下書き投稿に予約日時を一括自動設定する"""
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    from schedule_utils import bulk_auto_schedule
    drafts = Post.query.filter(
        Post.client_id == client_id,
        Post.status.in_(["draft", "approved"]),
        Post.scheduled_at.is_(None),
    ).order_by(Post.created_at.asc()).all()
    count = bulk_auto_schedule(client, drafts)
    db.session.commit()
    flash(f"{count} 件の投稿に予約日時を自動設定しました", "success")
    return redirect(url_for("designer.post_list", client_id=client_id))


@designer_bp.route("/clients/<int:client_id>/posts")
@login_required
def post_list(client_id: int):
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    status_filter = request.args.get("status", "")
    q = Post.query.filter_by(client_id=client_id)
    if status_filter:
        q = q.filter_by(status=status_filter)
    posts = q.order_by(Post.created_at.desc()).all()
    creating_count = Post.query.filter_by(client_id=client_id, status="creating").count()
    return render_template(
        "designer/posts/list.html",
        client=client,
        posts=posts,
        status_filter=status_filter,
        creating_count=creating_count,
    )


# ──────────────────────────── 投稿詳細・編集 ────────────────────────────────

@designer_bp.route("/clients/<int:client_id>/posts/<int:post_id>")
@login_required
def post_detail(client_id: int, post_id: int):
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    post = Post.query.get_or_404(post_id)
    if post.client_id != client_id:
        abort(403)
    return render_template("designer/posts/detail.html", client=client, post=post)


@designer_bp.route("/clients/<int:client_id>/posts/<int:post_id>/save", methods=["POST"])
@login_required
def post_save(client_id: int, post_id: int):
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    post = Post.query.get_or_404(post_id)
    if post.client_id != client_id:
        abort(403)

    post.title = request.form.get("title", post.title)
    post.body_html = request.form.get("body_html", post.body_html)
    post.ig_caption = request.form.get("ig_caption", post.ig_caption)
    post.ig_hashtags_post = request.form.get("ig_hashtags_post", post.ig_hashtags_post or "")
    post.created_by_designer_id = current_user.id
    post.updated_at = _now_jst()
    db.session.commit()
    flash("変更を保存しました", "success")
    return redirect(url_for("designer.post_detail", client_id=client_id, post_id=post_id))


# ──────────────────────────── 画像管理 ──────────────────────────────────────

@designer_bp.route("/clients/<int:client_id>/posts/<int:post_id>/images/add", methods=["POST"])
@login_required
def post_image_add(client_id: int, post_id: int):
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    post = Post.query.get_or_404(post_id)
    if post.client_id != client_id:
        abort(403)
    image_url = request.form.get("image_url", "").strip()
    if not image_url:
        return jsonify({"success": False, "reason": "URL が空です"})
    last = post.images.order_by(PostImage.sort_order.desc()).first()
    sort_order = (last.sort_order + 1) if last else 1
    if sort_order > 10:
        return jsonify({"success": False, "reason": "カルーセルは最大10枚までです"})
    img = PostImage(post_id=post_id, image_url=image_url, sort_order=sort_order)
    db.session.add(img)
    db.session.commit()
    return jsonify({"success": True, "id": img.id})


@designer_bp.route("/clients/<int:client_id>/posts/<int:post_id>/images/upload", methods=["POST"])
@login_required
def post_image_upload(client_id: int, post_id: int):
    """ファイルアップロード → static/uploads/clients/{id}/posts/{id}/ に保存"""
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    post = Post.query.get_or_404(post_id)
    if post.client_id != client_id:
        abort(403)

    files = request.files.getlist('images')
    if not files or all(f.filename == '' for f in files):
        return jsonify({"success": False, "reason": "ファイルが選択されていません"})

    current_count = post.images.count()
    if current_count >= 10:
        return jsonify({"success": False, "reason": "カルーセルは最大10枚までです"})

    save_dir = os.path.join(_upload_base(), 'clients', str(client_id), 'posts', str(post_id))
    os.makedirs(save_dir, exist_ok=True)

    last = post.images.order_by(PostImage.sort_order.desc()).first()
    next_order = (last.sort_order + 1) if last else 1
    uploaded = []

    for f in files:
        if f.filename == '' or not _allowed_image(f.filename):
            continue
        if current_count + len(uploaded) >= 10:
            break
        ext = f.filename.rsplit('.', 1)[1].lower()
        filename = f"{_uuid_mod.uuid4().hex}.{ext}"
        f.save(os.path.join(save_dir, filename))
        base_url = os.getenv("BASE_URL", "").rstrip("/")
        image_url = f"{base_url}/static/uploads/clients/{client_id}/posts/{post_id}/{filename}"
        img = PostImage(post_id=post_id, image_url=image_url, sort_order=next_order + len(uploaded))
        db.session.add(img)
        db.session.flush()
        uploaded.append({"id": img.id, "url": image_url})

    db.session.commit()
    return jsonify({"success": True, "uploaded": uploaded})


@designer_bp.route("/clients/<int:client_id>/posts/<int:post_id>/images/<int:img_id>/delete", methods=["POST"])
@login_required
def post_image_delete(client_id: int, post_id: int, img_id: int):
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    img = PostImage.query.get_or_404(img_id)
    if img.post_id != post_id:
        abort(403)
    # ローカルアップロード画像はファイルも削除
    if img.image_url.startswith('/static/uploads/'):
        file_path = os.path.join(current_app.root_path, img.image_url.lstrip('/'))
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass
    db.session.delete(img)
    db.session.commit()
    return jsonify({"success": True})


@designer_bp.route("/clients/<int:client_id>/posts/<int:post_id>/images/reorder", methods=["POST"])
@login_required
def post_image_reorder(client_id: int, post_id: int):
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    order = request.json.get("order", [])
    for i, img_id in enumerate(order, 1):
        PostImage.query.filter_by(id=img_id, post_id=post_id).update({"sort_order": i})
    db.session.commit()
    return jsonify({"success": True})


# ──────────────────────────── 投稿タイミング設定 ────────────────────────────

@designer_bp.route("/clients/<int:client_id>/posts/<int:post_id>/schedule", methods=["POST"])
@login_required
def post_schedule(client_id: int, post_id: int):
    """日時指定投稿の予約 or 取り消し"""
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    post = Post.query.get_or_404(post_id)
    if post.client_id != client_id:
        abort(403)

    action = request.form.get("action", "set")  # set or cancel
    if action == "cancel":
        post.status = "draft"
        post.publish_mode = None
        post.scheduled_at = None
        post.wp_post_id = ""
        post.wp_post_url = ""
        db.session.commit()
        flash("予約をキャンセルしました", "success")
        return redirect(url_for("designer.post_detail", client_id=client_id, post_id=post_id))

    # 日時を取得
    scheduled_at_str = request.form.get("scheduled_at", "").strip()
    if not scheduled_at_str:
        flash("投稿日時を入力してください", "error")
        return redirect(url_for("designer.post_detail", client_id=client_id, post_id=post_id))
    try:
        scheduled_at = datetime.fromisoformat(scheduled_at_str)
    except ValueError:
        flash("日時の形式が正しくありません", "error")
        return redirect(url_for("designer.post_detail", client_id=client_id, post_id=post_id))

    pt = client.platform_type or "wordpress"
    post.publish_mode = "scheduled"
    post.scheduled_at = scheduled_at
    post.status = "scheduled"
    db.session.commit()

    if pt in ("wordpress", "wordpress_instagram"):
        from config import decrypt_field
        result = wp_client.post_article(
            endpoint=client.wp_endpoint,
            username=client.wp_username,
            app_password=decrypt_field(client.wp_app_password),
            title=post.title,
            body_html=post.body_html,
            publish_mode="scheduled",
            scheduled_at=scheduled_at,
        )
        if result.get("success"):
            post.wp_post_id = str(result.get("wp_post_id", ""))
            post.wp_post_url = result.get("wp_post_url", "")
            db.session.commit()
            flash(f"{scheduled_at.strftime('%Y/%m/%d %H:%M')} に予約しました", "success")
        else:
            flash(f"WordPress 予約に失敗: {result.get('reason')}", "error")
    else:
        flash(f"{scheduled_at.strftime('%Y/%m/%d %H:%M')} に予約しました", "success")

    return redirect(url_for("designer.post_detail", client_id=client_id, post_id=post_id))


# ──────────────────────────── 今すぐ投稿 ────────────────────────────────────

@designer_bp.route("/clients/<int:client_id>/posts/<int:post_id>/publish", methods=["POST"])
@login_required
def post_publish(client_id: int, post_id: int):
    """platform_type に応じた投稿先に即時投稿する"""
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    post = Post.query.get_or_404(post_id)
    if post.client_id != client_id:
        abort(403)

    from config import decrypt_field
    pt = client.platform_type or "wordpress"

    # ── WordPress ─────────────────────────────────────────
    if pt == "wordpress":
        result = wp_client.post_article(
            endpoint=client.wp_endpoint,
            username=client.wp_username,
            app_password=decrypt_field(client.wp_app_password),
            title=post.title,
            body_html=post.body_html,
            publish_mode="immediate",
        )
        if result.get("success"):
            post.wp_post_id    = str(result.get("wp_post_id", ""))
            post.wp_post_url   = result.get("wp_post_url", "")
            post.status        = "posted"
            post.posted_at     = _now_jst()
            post.error_message = ""
            db.session.commit()
            return jsonify({"success": True, "wp_url": post.wp_post_url})
        else:
            post.status = "failed"
            post.error_message = result.get("reason", "WordPress 投稿失敗")
            db.session.commit()
            return jsonify({"success": False, "reason": post.error_message})

    # ── Instagram ─────────────────────────────────────────
    elif pt == "instagram":
        result = _publish_to_instagram(client, post)
        if result.get("success"):
            post.ig_media_id   = result.get("media_id", "")
            post.status        = "posted"
            post.posted_at     = _now_jst()
            post.error_message = ""
            db.session.commit()
            return jsonify({"success": True})
        else:
            post.status = "failed"
            post.error_message = result.get("reason", "Instagram 投稿失敗")
            db.session.commit()
            return jsonify({"success": False, "reason": post.error_message})

    # ── 独自HP（投稿済みマーク）──────────────────────────
    elif pt == "custom_hp":
        post.status        = "posted"
        post.posted_at     = _now_jst()
        post.error_message = ""
        db.session.commit()
        return jsonify({"success": True})

    # ── 旧: wordpress_instagram（後方互換）───────────────
    else:
        errors = []
        wp_result = wp_client.post_article(
            endpoint=client.wp_endpoint,
            username=client.wp_username,
            app_password=decrypt_field(client.wp_app_password),
            title=post.title,
            body_html=post.body_html,
            publish_mode="immediate",
        )
        if wp_result.get("success"):
            post.wp_post_id  = str(wp_result.get("wp_post_id", ""))
            post.wp_post_url = wp_result.get("wp_post_url", "")
        else:
            errors.append(f"WordPress: {wp_result.get('reason')}")
        ig_result = _publish_to_instagram(client, post)
        if ig_result.get("success"):
            post.ig_media_id = ig_result.get("media_id", "")
        else:
            errors.append(f"Instagram: {ig_result.get('reason')}")
        if errors:
            post.status = "failed"
            post.error_message = "\n".join(errors)
            db.session.commit()
            return jsonify({"success": False, "reason": "\n".join(errors)})
        post.status        = "posted"
        post.posted_at     = _now_jst()
        post.error_message = ""
        db.session.commit()
        return jsonify({"success": True, "wp_url": post.wp_post_url})


def _strip_account_prefix(caption: str, client_name: str) -> str:
    """冒頭に混入した企業名・@メンション・空行を除去する。"""
    lines = caption.strip().splitlines()
    cleaned = []
    for line in lines:
        s = line.strip()
        if not cleaned:
            if not s:
                continue
            if s.startswith("@"):
                continue
            if client_name and s == client_name:
                continue
            if client_name and s.startswith(client_name):
                line = s[len(client_name):].lstrip(" 　・／/")
                if not line:
                    continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def _build_caption(post: Post, client: Client) -> str:
    """キャプション + ハッシュタグを結合して返す"""
    caption = _strip_account_prefix(post.ig_caption or "", client.name)
    hashtags = (post.ig_hashtags_post or "").strip()
    if hashtags:
        caption = caption.rstrip() + "\n\n" + hashtags
    return caption


def _publish_to_instagram(client: Client, post: Post) -> dict:
    """Instagram 投稿処理（単一画像 or カルーセル）"""
    from config import decrypt_field
    ig_id = client.ig_business_account_id
    token = decrypt_field(client.ig_access_token)
    if not ig_id or not token:
        return {"success": False, "reason": "Instagram の認証情報が設定されていません"}

    images = post.image_list
    if not images:
        return {"success": False, "reason": "投稿画像がありません"}

    caption = _build_caption(post, client)

    if len(images) == 1:
        return ig_client.post_single_image(
            ig_user_id=ig_id,
            access_token=token,
            image_url=images[0].image_url,
            caption=caption,
        )
    else:
        # カルーセル投稿
        container_ids = []
        for img in images:
            r = ig_client.create_carousel_item(ig_id, token, img.image_url)
            if not r.get("success"):
                return r
            img.ig_container_id = r["container_id"]
            container_ids.append(r["container_id"])
        db.session.commit()
        return ig_client.post_carousel(ig_id, token, container_ids, caption)
