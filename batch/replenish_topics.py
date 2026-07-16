"""batch/replenish_topics.py
topic_queue の pending 件数が 5 件未満の企業に 10 件補充する週次バッチ。

GitHub Actions (週1回 or 日次バッチから呼び出し) で実行:
  python -m batch.replenish_topics [--client-id=N]
"""
import sys
import os
import argparse
import json
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flask import Flask
from config import Config
from models import db, Client, TopicQueue, Post
import anthropic

REPLENISH_THRESHOLD = 5
REPLENISH_COUNT = 10


def create_app():
    from flask import Flask
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)
    return app


def _generate_topics(client: Client, count: int) -> list[dict]:
    """Claude で記事ネタをまとめて生成する"""
    existing_titles = [
        r[0] for r in
        db.session.query(Post.title).filter_by(client_id=client.id).all()
    ] + [
        r[0] for r in
        db.session.query(TopicQueue.title).filter_by(client_id=client.id).all()
    ]
    existing_str = "\n".join(f"- {t}" for t in existing_titles[:50]) or "（まだ記事はありません）"

    ai = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
    prompt = f"""あなたはコンテンツ戦略家です。

契約企業: {client.name}

すでに作成済みの記事タイトル（重複を避けてください）:
{existing_str}

上記と重複しない、{client.name} のブログ向け記事ネタを {count} 件、
以下の JSON 配列形式で出力してください。他のテキストは含めないでください。

[
  {{"title": "記事タイトル1", "outline": "大枠・キーポイントのメモ（200文字以内）"}},
  ...
]"""

    message = ai.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    text = message.content[0].text
    m = re.search(r'\[[\s\S]*\]', text)
    if not m:
        return []
    return json.loads(m.group())


def replenish_client(app, client_id: int):
    with app.app_context():
        client = Client.query.get(client_id)
        if not client:
            return

        pending = TopicQueue.query.filter_by(client_id=client_id, status="pending").count()
        if pending >= REPLENISH_THRESHOLD:
            print(f"  [{client.name}] pending={pending}件。補充不要")
            return

        print(f"  [{client.name}] pending={pending}件 → {REPLENISH_COUNT}件補充します")
        try:
            topics = _generate_topics(client, REPLENISH_COUNT)
        except Exception as e:
            print(f"  生成エラー: {e}")
            return

        # sort_order の末尾から連番
        last = (
            TopicQueue.query.filter_by(client_id=client_id)
            .order_by(TopicQueue.sort_order.desc())
            .first()
        )
        next_order = (last.sort_order + 1) if last else 1

        for i, t in enumerate(topics):
            topic = TopicQueue(
                client_id=client_id,
                title=t.get("title", ""),
                outline=t.get("outline", ""),
                sort_order=next_order + i,
                status="pending",
                created_by="ai_auto",
            )
            db.session.add(topic)
        db.session.commit()
        print(f"  {len(topics)} 件追加しました")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-id", type=int, help="対象クライアント ID (省略で全社)")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        if args.client_id:
            clients = [Client.query.get(args.client_id)]
        else:
            clients = Client.query.all()

        for client in clients:
            if client:
                replenish_client(app, client.id)


if __name__ == "__main__":
    main()
