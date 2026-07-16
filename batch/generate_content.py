"""batch/generate_content.py
topic_queue から pending のネタを取得し、AI で本文・IG キャプション・画像を生成して
posts_ig に保存する。消化後に pending が 0 件なら自動補充も実施する。

GitHub Actions または手動で実行:
  python -m batch.generate_content [--client-id=N] [--limit=3]
"""
import sys
import os
import argparse
from datetime import datetime

# プロジェクトルートをパスに追加
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flask import Flask
from config import Config
from models import db, Client, TopicQueue, Post, PostImage
import anthropic
import image_generator


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)
    return app


def _generate_blog_content(client: Client, topic: TopicQueue) -> dict:
    """Claude で本文と IG キャプションを生成する"""
    ai = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
    prompt = f"""あなたはプロのブログライターです。

契約企業: {client.name}
記事タイトル: {topic.title}
大枠・メモ: {topic.outline or '（指定なし）'}

以下の JSON 形式で出力してください。他のテキストは一切含めないでください。

{{
  "body_html": "<article> ... </article>（日本語、1200文字以上のHTML記事本文）",
  "ig_caption": "Instagram キャプション（日本語、2200文字以内、ハッシュタグ含む）"
}}"""

    message = ai.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    import json, re
    text = message.content[0].text
    m = re.search(r'\{[\s\S]*\}', text)
    if not m:
        raise ValueError(f"JSON が見つかりません: {text[:200]}")
    return json.loads(m.group())


def process_topic(app, topic_id: int, gcp_project: str = "", gcp_location: str = "us-central1"):
    with app.app_context():
        topic = TopicQueue.query.get(topic_id)
        if not topic or topic.status != "pending":
            print(f"  スキップ: topic_id={topic_id}")
            return False

        client = Client.query.get(topic.client_id)
        print(f"  生成中: [{client.name}] {topic.title}")

        try:
            content = _generate_blog_content(client, topic)
        except Exception as e:
            print(f"  AI 生成エラー: {e}")
            return False

        # Post 作成
        post = Post(
            client_id=client.id,
            title=topic.title,
            outline=topic.outline,
            body_html=content.get("body_html", ""),
            ig_caption=content.get("ig_caption", ""),
            status="draft",
        )
        db.session.add(post)
        db.session.flush()

        # 画像生成
        if gcp_project and image_generator.is_configured(gcp_project):
            try:
                img_prompt = image_generator.build_prompt(topic.title, topic.outline or "")
                img_path = f"generated_images/post_{post.id}.png"
                img_result = image_generator.generate_image(img_prompt, img_path, gcp_project, gcp_location)
                if img_result.get("success"):
                    # 画像URL は WordPress アップロード後に設定。ここでは local path を仮置き
                    db.session.add(PostImage(
                        post_id=post.id,
                        image_url=f"local:{img_path}",
                        sort_order=1,
                    ))
            except Exception as e:
                print(f"  画像生成スキップ: {e}")

        # TopicQueue を更新
        topic.status = "generated"
        topic.generated_post_id = post.id
        db.session.commit()
        print(f"  完了: post_id={post.id}")
        return True


def replenish_if_needed(app, client: Client):
    """pending が 0 件なら自動補充する（日次生成バッチ側の安全策）"""
    from batch.replenish_topics import replenish_client
    with app.app_context():
        pending = TopicQueue.query.filter_by(client_id=client.id, status="pending").count()
        if pending == 0:
            print(f"  [{client.name}] ネタが枯渇したため自動補充します")
            replenish_client(app, client.id)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-id", type=int, help="対象クライアント ID (省略で全社)")
    parser.add_argument("--limit", type=int, default=3, help="1回の実行で処理する最大件数")
    parser.add_argument("--gcp-project", default=os.getenv("GCP_PROJECT_ID", ""))
    parser.add_argument("--gcp-location", default=os.getenv("GCP_LOCATION", "us-central1"))
    args = parser.parse_args()

    app = create_app()

    with app.app_context():
        q = TopicQueue.query.filter_by(status="pending")
        if args.client_id:
            q = q.filter_by(client_id=args.client_id)
        topics = q.order_by(TopicQueue.sort_order, TopicQueue.id).limit(args.limit).all()

        if not topics:
            print("処理対象のネタがありません")
            return

        processed_clients = set()
        for topic in topics:
            process_topic(app, topic.id, args.gcp_project, args.gcp_location)
            processed_clients.add(topic.client_id)

        # 消化後の自動補充チェック
        for cid in processed_clients:
            c = Client.query.get(cid)
            replenish_if_needed(app, c)


if __name__ == "__main__":
    main()
