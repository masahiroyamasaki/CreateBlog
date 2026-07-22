"""batch_monthly.py — 月次自動生成バッチ

VPS cron 設定:
  # 毎月1日9時: ネタ生成
  0 9 1 * * cd /var/www/blog-app && /var/www/blog-app/venv/bin/flask run-monthly-ideas >> /var/log/blog-monthly.log 2>&1
"""
import re
import json
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)
_JST = timezone(timedelta(hours=9))


def _plan_description(client) -> str:
    """企業の契約プランをもとに請求明細の内容文を生成する。"""
    count = client.monthly_post_count or 4
    _labels = {
        "instagram": f"Instagram 運用代行 {count}件/月",
        "wordpress":  f"WordPress 記事制作 {count}件/月",
        "custom_hp":  f"独自HP 記事制作 {count}件/月",
    }
    return _labels.get(client.platform_type or "", f"ブログ運用代行 {count}件/月")


def run_monthly_ideas_batch(app, db) -> dict:
    """毎月1日: 全稼働企業に対して月間投稿数分のネタを生成する。"""
    result = {"clients": 0, "topics": 0, "errors": []}

    with app.app_context():
        from models import Client
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

            try:
                new_topics = _generate_ideas(client, ai, count, db)
                result["topics"] += len(new_topics)
                logger.info(f"[{client.name}] ネタ {len(new_topics)} 件追加")
            except Exception as e:
                msg = f"[{client.name}] ネタ生成エラー: {e}"
                logger.error(msg)
                result["errors"].append(msg)

    return result



# ── 請求書バッチ ──────────────────────────────────────────────────────────────

def run_monthly_billing_batch(app, db) -> dict:
    """毎月1日: ClientSubscription テーブルをもとに請求書を自動作成・送付する（先払い制）。
    is_trial=False かつ billing_date が当月以前のもののみ対象。
    """
    result = {"invoices": 0, "errors": []}

    with app.app_context():
        from models import ClientSubscription, Client, Designer, Invoice, InvoiceItem
        from billing import generate_invoice_pdf
        from mailer import send_invoice_email

        now = datetime.now(_JST)
        year, month = now.year, now.month
        month_start = datetime(year, month, 1)

        # 請求対象: 無料期間終了済み かつ 請求開始日が今月以前
        subs = ClientSubscription.query.filter(
            ClientSubscription.is_trial == False,  # noqa: E712
            ClientSubscription.billing_date <= month_start,
        ).all()

        # デザイナーごとに集計
        designer_subs: dict[int, list] = {}
        for sub in subs:
            client = Client.query.get(sub.client_id)
            if client and client.client_status in ("active", "test"):
                designer_subs.setdefault(sub.designer_id, []).append(sub)

        for designer_id, sub_list in designer_subs.items():
            try:
                # 同月重複防止
                if Invoice.query.filter_by(designer_id=designer_id, year=year, month=month).first():
                    logger.info(f"[billing] Designer {designer_id}: {year}/{month} 請求書作成済み、スキップ")
                    continue

                # テスト企業(amount=0)を含む合計
                total = sum(s.amount for s in sub_list)
                invoice = Invoice(
                    designer_id=designer_id,
                    year=year, month=month,
                    total_amount=total, status="issued",
                )
                db.session.add(invoice)
                db.session.flush()

                for sub in sub_list:
                    client = Client.query.get(sub.client_id)
                    db.session.add(InvoiceItem(
                        invoice_id=invoice.id,
                        client_id=sub.client_id,
                        client_name=client.name if client else sub.plan_name,
                        description=sub.plan_name,
                        amount=sub.amount,
                    ))
                db.session.commit()

                # PDF生成
                items_list = InvoiceItem.query.filter_by(invoice_id=invoice.id).all()
                pdf_path = generate_invoice_pdf(invoice, items_list)
                invoice.pdf_path = pdf_path
                db.session.commit()

                # メール送付
                designer = Designer.query.get(designer_id)
                if designer and designer.email:
                    r = send_invoice_email(designer.email, designer.name, invoice, pdf_path)
                    if r.get("success"):
                        invoice.status = "sent"
                        invoice.sent_at = datetime.now(_JST).replace(tzinfo=None)
                        db.session.commit()
                        logger.info(f"[billing] Designer {designer_id} ({designer.name}): 送付完了")
                    else:
                        logger.warning(f"[billing] メール送付失敗: {r.get('reason')}")

                result["invoices"] += 1
            except Exception as e:
                db.session.rollback()
                msg = f"[billing] Designer {designer_id} エラー: {e}"
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

    theme_list = [t.strip() for t in themes.splitlines() if t.strip()]
    theme_count = len(theme_list)
    theme_note = (
        f"テーマは {theme_count} 種類あります。{count}件の中でできるだけ均等に各テーマを使ってください。"
        if theme_count > 1 else ""
    )

    business_description = (client.business_description or "").strip()
    business_note = f"\n事業内容: {business_description}" if business_description else ""

    target_audience = (client.target_audience or "").strip()
    audience_note = f"\n想定読者: {target_audience}" if target_audience else ""

    prompt = f"""あなたはコンテンツプランナーです。
以下のテーマをもとに、投稿ネタを{count}件考えてください。

企業名: {client.name}{business_note}
テーマ:
{themes}{audience_note}
{avoid}

【重要な条件】
1. テーマの分散: {theme_note}1つのテーマに偏らないこと。
2. タイトル表現の多様化: 同じ書き出し・言い回しを2件以上使わないこと。
   以下の形式をバランスよく混在させること:
   - How-to型    : 「〇〇する3つの方法」「〇〇のコツ」
   - 疑問型      : 「〇〇できていますか？」「なぜ〇〇なのか」
   - リスト型    : 「〇〇な人の特徴5選」「〇〇に必要なもの」
   - 比較型      : 「〇〇vs〇〇」「〇〇と〇〇の違い」
   - ストーリー型: 「〇〇を変えたら〇〇になった」「〇〇してわかったこと」
   - 断言型      : 「〇〇はもう古い」「実は〇〇が重要だった」
   - 共感型      : 「〇〇で悩んでいる方へ」「〇〇あるある」
3. 切り口の多様化: 初心者向け・上級者向け・季節トレンド・よくある失敗・プロの視点など角度を変えること。
4. 読者の悩みや関心に刺さるタイトルにすること。
5. 大枠は投稿の方向性を2〜3文で簡潔に記載すること。

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


