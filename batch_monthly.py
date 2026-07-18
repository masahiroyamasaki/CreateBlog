"""batch_monthly.py — 毎月10日の投稿ネタ＋記事自動生成バッチ

VPS cron 設定:
  0 9 10 * * cd /var/www/blog-app && /var/www/blog-app/venv/bin/flask run-monthly-batch >> /var/log/blog-monthly.log 2>&1
"""
import re
import json
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)
_JST = timezone(timedelta(hours=9))


def run_monthly_batch(app, db) -> dict:
    """全稼働企業に対して月間投稿数分のネタ生成 → 記事生成を実行する。"""
    result = {"clients": 0, "topics": 0, "posts": 0, "errors": []}

    with app.app_context():
        from models import Client, TopicQueue, Post
        from config import Config
        import anthropic as _anthropic

        ai = _anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
        clients = Client.query.filter_by(client_status="active").all()

        for client in clients:
            count = client.monthly_post_count or 4
            if count <= 0:
                continue

            logger.info(f"[{client.name}] ネタ {count} 件生成開始")
            result["clients"] += 1

            # ── Step 1: ネタを count 件生成 ────────────────────────────────
            try:
                new_topics = _generate_ideas(client, ai, count, db)
                result["topics"] += len(new_topics)
                logger.info(f"[{client.name}] ネタ {len(new_topics)} 件追加")
            except Exception as e:
                msg = f"[{client.name}] ネタ生成エラー: {e}"
                logger.error(msg)
                result["errors"].append(msg)
                continue

            # ── Step 2: 各ネタから記事を生成 ──────────────────────────────
            pt = client.platform_type or "wordpress"
            for topic in new_topics:
                try:
                    _generate_post(client, topic, pt, ai, app, db)
                    result["posts"] += 1
                    logger.info(f"[{client.name}] 記事生成完了: {topic.title}")
                except Exception as e:
                    msg = f"[{client.name}] 記事生成エラー ({topic.title}): {e}"
                    logger.error(msg)
                    result["errors"].append(msg)

    return result


# ── ネタ生成 ─────────────────────────────────────────────────────────────────

def _generate_ideas(client, ai, count: int, db) -> list:
    """AIでネタを count 件生成してキューに追加し、TopicQueue リストを返す。"""
    from models import TopicQueue, Post

    themes = (client.themes or "").strip()
    if not themes:
        raise ValueError("企業テーマが設定されていません")

    existing_titles = (
        [t.title for t in TopicQueue.query.filter_by(client_id=client.id).all()]
        + [p.title for p in Post.query.filter_by(client_id=client.id).all()]
    )
    avoid = "\n\n【重複禁止】\n" + "\n".join(f"- {t}" for t in existing_titles[:30]) if existing_titles else ""

    prompt = f"""あなたはコンテンツプランナーです。
以下のテーマをもとに、投稿ネタを{count}件考えてください。

企業名: {client.name}
テーマ:
{themes}
{avoid}

条件:
- {count}件すべて異なる切り口にすること
- 読者の悩みや関心に刺さるタイトルにすること
- 大枠は投稿の方向性を2〜3文で簡潔に記載すること

以下のJSON形式のみで出力してください。他のテキストは一切含めないこと:
[
  {{"title": "タイトル", "outline": "大枠・方向性"}},
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
        raise ValueError("AIレスポンスからJSONが見つかりませんでした")
    ideas = json.loads(m.group())

    last = (
        TopicQueue.query.filter_by(client_id=client.id, status="pending")
        .order_by(TopicQueue.sort_order.desc()).first()
    )
    next_order = (last.sort_order + 1) if last else 1
    added = []
    for i, idea in enumerate(ideas[:count]):
        title = (idea.get("title") or "").strip()
        if not title:
            continue
        topic = TopicQueue(
            client_id=client.id,
            title=title,
            outline=(idea.get("outline") or "").strip(),
            sort_order=next_order + len(added),
            created_by="ai_auto",
        )
        db.session.add(topic)
        added.append(topic)

    db.session.commit()
    return added


# ── 記事生成 ─────────────────────────────────────────────────────────────────

def _generate_post(client, topic, platform_type: str, ai, app, db):
    """1件のネタから記事を生成して Post に保存する（同期実行）。"""
    from models import TopicQueue, Post
    from schedule_utils import next_scheduled_at

    # プレースホルダー投稿作成
    topic.status = "processing"
    placeholder = Post(
        client_id=client.id,
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

    try:
        from agents.blog_creator import BlogCreatorAgent
        from agents.content_checker import ContentCheckerAgent
        from agents.legal_checker import LegalCheckerAgent
        from agents.final_creator import FinalCreatorAgent
        from agents.ig_formatter import IgFormatterAgent
        import markdown as _md

        draft         = BlogCreatorAgent().run({
            "topic": topic.title, "keywords": topic.outline or "",
            "tone": "標準", "existing_posts": [],
        })
        content_check = ContentCheckerAgent().run({"draft": draft})
        legal_check   = LegalCheckerAgent().run({"draft": draft})
        final_content = FinalCreatorAgent().run({
            "draft": draft, "content_check": content_check, "legal_check": legal_check,
            "topic": topic.title, "keywords": topic.outline or "", "tone": "標準",
        })
        ig_caption    = IgFormatterAgent().run({
            "blog_content": final_content,
            "topic": topic.title,
            "client_name": client.name,
        })

        if platform_type == "instagram":
            body_html = ""
        else:
            body_html = _md.markdown(final_content, extensions=["extra", "toc"])

        # スケジュール自動設定
        existing_dates = {
            p.scheduled_at.date()
            for p in Post.query.filter(
                Post.client_id == client.id,
                Post.scheduled_at.isnot(None),
            ).all() if p.scheduled_at
        }
        scheduled_at = next_scheduled_at(client, existing_dates) if client.schedule_type else None

        post = Post.query.get(post_id)
        if post:
            post.body_html    = body_html
            post.ig_caption   = ig_caption.strip()
            post.status       = "draft"
            post.scheduled_at = scheduled_at

        topic.status = "generated"
        db.session.commit()

    except Exception as e:
        post = Post.query.get(post_id)
        if post:
            post.status = "failed"
            post.error_message = str(e)
        topic.status = "pending"
        topic.generated_post_id = None
        db.session.commit()
        raise
