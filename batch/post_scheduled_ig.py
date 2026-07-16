"""batch/post_scheduled_ig.py
scheduled_at が到来した posts_ig (status='scheduled') に対して
Instagram 投稿のみを実行する（WordPress は予約時に設定済みのため不要）。

GitHub Actions (5〜15分おき) で実行:
  python -m batch.post_scheduled_ig
"""
import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flask import Flask
from config import Config, decrypt_field
from models import db, Post, PostImage
import instagram as ig_client


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)
    return app


def publish_post_to_instagram(post: Post) -> dict:
    """Post オブジェクトを受け取って Instagram に投稿する共通関数"""
    client = post.client
    ig_id = client.ig_business_account_id
    token = decrypt_field(client.ig_access_token)

    if not ig_id or not token:
        return {"success": False, "reason": "Instagram 認証情報が未設定です"}

    images = post.image_list
    if not images:
        return {"success": False, "reason": "投稿画像がありません"}

    if len(images) == 1:
        return ig_client.post_single_image(ig_id, token, images[0].image_url, post.ig_caption)

    # カルーセル
    container_ids = []
    for img in images:
        r = ig_client.create_carousel_item(ig_id, token, img.image_url)
        if not r.get("success"):
            return r
        img.ig_container_id = r["container_id"]
        container_ids.append(r["container_id"])
    db.session.commit()
    return ig_client.post_carousel(ig_id, token, container_ids, post.ig_caption)


def main():
    app = create_app()
    with app.app_context():
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        posts = (
            Post.query.filter(
                Post.status == "scheduled",
                Post.scheduled_at <= now,
            )
            .all()
        )

        if not posts:
            print("投稿対象なし")
            return

        for post in posts:
            # 実行直前に再確認（予約取り消し対策）
            db.session.refresh(post)
            if post.status != "scheduled":
                print(f"  スキップ (status変更済み): post_id={post.id}")
                continue

            print(f"  IG 投稿中: post_id={post.id} [{post.title}]")
            result = publish_post_to_instagram(post)

            if result.get("success"):
                post.ig_media_id = result.get("media_id", "")
                post.status = "posted"
                post.posted_at = datetime.utcnow()
                post.error_message = ""
                print(f"  完了: media_id={post.ig_media_id}")
            else:
                post.status = "failed"
                post.error_message = result.get("reason", "不明なエラー")
                print(f"  失敗: {post.error_message}")

            db.session.commit()


if __name__ == "__main__":
    main()
