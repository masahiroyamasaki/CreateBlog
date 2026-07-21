"""scheduled_publisher.py — 予約済み Instagram 投稿の自動実行

VPS の cron から毎分呼び出す:
  * * * * * cd /var/www/blog-app && /var/www/blog-app/venv/bin/flask publish-scheduled >> /var/log/blog-scheduled.log 2>&1
"""
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))


def _now_jst() -> datetime:
    """現在の JST 時刻をタイムゾーンなし（naive）で返す。
    ブラウザ入力値（JST naive）と比較するために使用する。"""
    return datetime.now(JST).replace(tzinfo=None)


def publish_due_posts(app, db) -> int:
    """status=scheduled かつ scheduled_at が過去の投稿を platform_type に応じて実行する。
    Returns: 投稿成功件数
    """
    with app.app_context():
        from models import Post
        import instagram as ig_client
        import wp_client as wp_mod
        from config import decrypt_field

        now = _now_jst()
        due_posts = Post.query.filter(
            Post.status == "draft",
            Post.scheduled_at.isnot(None),
            Post.scheduled_at <= now,
        ).all()

        if not due_posts:
            return 0

        published = 0
        for post in due_posts:
            client = post.client
            pt = client.platform_type or "wordpress"
            logger.info(f"Processing post {post.id} ({pt}) scheduled_at={post.scheduled_at}")

            try:
                # WordPress は wp-cron が自動処理するためスキップ
                if pt in ("wordpress", "wordpress_instagram"):
                    continue

                elif pt == "instagram":
                    result = _do_instagram(client, post, ig_client, decrypt_field)
                    if result.get("success"):
                        post.ig_media_id   = result.get("media_id", "")
                        post.status        = "posted"
                        post.posted_at     = now
                        post.error_message = ""
                        published += 1
                    else:
                        post.status        = "failed"
                        post.error_message = result.get("reason", "Instagram 投稿失敗")

                elif pt == "custom_hp":
                    post.status        = "posted"
                    post.posted_at     = now
                    post.error_message = ""
                    published += 1

                elif pt == "email_only":
                    from mailer import send_article_email
                    result = send_article_email(
                        to_email=client.client_email or "",
                        company_name=client.name,
                        title=post.title,
                        body_html=post.body_html or "",
                        email_format=client.email_format or "html",
                        plain_body=post.ig_caption or "",
                    )
                    if result.get("success"):
                        post.status        = "posted"
                        post.posted_at     = now
                        post.error_message = ""
                        published += 1
                    else:
                        post.status        = "failed"
                        post.error_message = result.get("reason", "メール送信失敗")

                db.session.commit()

            except Exception as e:
                logger.error(f"Post {post.id} publish error: {e}")
                try:
                    post.status        = "failed"
                    post.error_message = str(e)
                    db.session.commit()
                except Exception:
                    db.session.rollback()

        return published


def _do_instagram(client, post, ig_client, decrypt_field) -> dict:
    ig_id = client.ig_business_account_id
    token = decrypt_field(client.ig_access_token)
    if not ig_id or not token:
        return {"success": False, "reason": "Instagram 認証情報が設定されていません"}

    images = post.image_list
    if not images:
        return {"success": False, "reason": "投稿画像がありません"}

    from caption_utils import strip_account_prefix
    caption  = strip_account_prefix(post.ig_caption or "", client.name or "")
    hashtags = (post.ig_hashtags_post or "").strip()
    if hashtags:
        caption = caption.rstrip() + "\n\n" + hashtags

    if len(images) == 1:
        return ig_client.post_single_image(
            ig_user_id=ig_id,
            access_token=token,
            image_url=images[0].image_url,
            caption=caption,
        )

    # カルーセル
    from models import db
    container_ids = []
    for img in images:
        r = ig_client.create_carousel_item(ig_id, token, img.image_url)
        if not r.get("success"):
            return r
        img.ig_container_id = r["container_id"]
        container_ids.append(r["container_id"])
    db.session.commit()
    return ig_client.post_carousel(ig_id, token, container_ids, caption)
