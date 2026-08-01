"""batch_monthly.py — 月次自動生成バッチ

VPS cron 設定:
  # 毎月1日 0時: ご利用明細書発行
  0 0 1 * * cd /var/www/blog-app && /var/www/blog-app/venv/bin/flask run-monthly-billing >> /var/log/blog-monthly-billing.log 2>&1
  # 毎月1日 9時: 記事ネタ生成
  0 9 1 * * cd /var/www/blog-app && /var/www/blog-app/venv/bin/flask run-monthly-ideas >> /var/log/blog-monthly-ideas.log 2>&1
  # 毎月10日 9時: 記事自動生成
  0 9 10 * * cd /var/www/blog-app && /var/www/blog-app/venv/bin/flask run-monthly-articles >> /var/log/blog-monthly-articles.log 2>&1
"""
import re
import json
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)
_JST = timezone(timedelta(hours=9))


def _extract_json_array(text: str) -> list:
    """AIレスポンスから最初の完全なJSONアレイを抽出する。複数の方法を順に試みる。"""
    # コードブロック記法（開き・閉じ両方）を除去
    cleaned = re.sub(r'```[\w]*', '', text)
    cleaned = cleaned.replace('```', '').strip()

    # 方法1: テキスト全体をそのままパース
    try:
        result = json.loads(cleaned)
        if isinstance(result, list):
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    # 方法2: 最初の [ から最後の ] までを切り出してパース
    start = cleaned.find('[')
    if start >= 0:
        end = cleaned.rfind(']')
        if end > start:
            try:
                result = json.loads(cleaned[start:end + 1])
                if isinstance(result, list):
                    return result
            except (json.JSONDecodeError, ValueError):
                pass

        # 方法3: balanced-bracket で正確に切り出してパース
        depth = 0
        in_str = False
        esc = False
        for i, ch in enumerate(cleaned[start:], start):
            if esc:
                esc = False
                continue
            if ch == '\\' and in_str:
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == '[':
                depth += 1
            elif ch == ']':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(cleaned[start:i + 1])
                    except (json.JSONDecodeError, ValueError):
                        pass

    raise ValueError("AIレスポンスからJSONを抽出できませんでした")


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
        clients = Client.query.filter(
            Client.client_status.in_(["active", "test"])
        ).all()

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

    トライアル自動解除:
      - is_trial=True の企業でも、前月以前にトライアル請求書（Invoice.is_trial=True）が
        存在する場合は自動的に is_trial=False に切り替えて当月から請求する。
    """
    result = {"invoices": 0, "errors": []}

    with app.app_context():
        from models import ClientSubscription, Client, Designer, Invoice, InvoiceItem
        from sqlalchemy import or_, and_
        from billing import generate_invoice_pdf
        from mailer import send_invoice_email

        now = datetime.now(_JST)
        year, month = now.year, now.month

        # ── トライアル自動解除 ─────────────────────────────────────────────────
        # is_trial=True の subscription に前月以前のトライアル請求書があれば請求切替
        trial_subs = ClientSubscription.query.filter_by(is_trial=True).all()
        for sub in trial_subs:
            has_prev_trial_invoice = (
                db.session.query(InvoiceItem.id)
                .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
                .filter(
                    InvoiceItem.client_id == sub.client_id,
                    Invoice.is_trial == True,  # noqa: E712
                    or_(
                        Invoice.year < year,
                        and_(Invoice.year == year, Invoice.month < month),
                    ),
                )
                .first()
            ) is not None

            if has_prev_trial_invoice:
                sub.is_trial = False
                logger.info(f"[billing] Client {sub.client_id}: トライアル終了 → 請求開始")

        db.session.commit()

        # ── 請求対象の確定: is_trial=False の全 subscription ─────────────────
        subs = ClientSubscription.query.filter_by(is_trial=False).all()

        # デザイナーごとに集計（稼働中・テスト・設定中企業を対象）
        designer_subs: dict[int, list] = {}
        for sub in subs:
            client = Client.query.get(sub.client_id)
            if client and client.client_status in ("active", "test", "setting"):
                designer_subs.setdefault(sub.designer_id, []).append(sub)

        for designer_id, sub_list in designer_subs.items():
            try:
                # 同月の通常請求書（is_trial=False）が既にあればスキップ
                if Invoice.query.filter_by(
                    designer_id=designer_id, year=year, month=month, is_trial=False
                ).first():
                    logger.info(f"[billing] Designer {designer_id}: {year}/{month} 請求書作成済み、スキップ")
                    continue

                # 基本プラン合計 + AI画像生成オプション料金
                total = sum(s.amount for s in sub_list)
                for sub in sub_list:
                    c = Client.query.get(sub.client_id)
                    if c and getattr(c, "image_gen_enabled", False) and c.client_status in ("active", "test", "setting"):
                        count = c.monthly_post_count or 4
                        total += (count // 4) * 2000

                invoice = Invoice(
                    designer_id=designer_id,
                    year=year, month=month,
                    total_amount=total, status="issued",
                    is_trial=False,
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
                    # AI画像生成オプション明細（個別行）
                    if client and getattr(client, "image_gen_enabled", False):
                        count = client.monthly_post_count or 4
                        img_fee = (count // 4) * 2000
                        if img_fee > 0:
                            db.session.add(InvoiceItem(
                                invoice_id=invoice.id,
                                client_id=client.id,
                                client_name=client.name,
                                description=f"AI画像生成オプション {count}件/月",
                                amount=img_fee,
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

    # テーマ回転: 前回の続きから割り当て
    if theme_count > 1:
        start_idx = (client.last_idea_theme_idx or 0) % theme_count
        assigned = [theme_list[(start_idx + i) % theme_count] for i in range(count)]
        client.last_idea_theme_idx = (start_idx + count) % theme_count
        db.session.flush()
        theme_assignment = "\n".join(f"- ネタ{i+1}: テーマ「{t}」" for i, t in enumerate(assigned))
        theme_note = f"各ネタのテーマ割り当て（必ず従うこと）:\n{theme_assignment}"
    else:
        theme_note = "テーマは1種類です。切り口・対象読者・難易度・形式を毎回変えて多様なネタを生成してください。"

    business_description = (client.business_description or "").strip()
    business_note = f"\n事業内容: {business_description}" if business_description else ""

    target_audience = (client.target_audience or "").strip()
    audience_note = f"\n想定読者: {target_audience}" if target_audience else ""

    prompt = f"""あなたはコンテンツプランナーです。
以下のテーマをもとに、投稿ネタを{count}件考えてください。

企業名: {client.name}{business_note}
登録テーマ一覧:
{themes}{audience_note}
{avoid}

【重要な条件】
1. テーマ割り当て: {theme_note}
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
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    text = message.content[0].text
    if message.stop_reason == "max_tokens":
        logger.warning(f"[_generate_ideas] max_tokens に達しました。レスポンスが切れている可能性があります。")
    ideas = _extract_json_array(text)

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


