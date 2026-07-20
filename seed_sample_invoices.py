"""seed_sample_invoices.py — yamasaki デザイナーの先月・先々月サンプル請求書を作成

実行:
  python seed_sample_invoices.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask
from config import Config
from models import db, Designer, Client, Invoice, InvoiceItem, DesignerClient


def main():
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)

    with app.app_context():
        # ── yamasaki デザイナーを検索 ────────────────────────────────────────
        designer = (
            Designer.query
            .filter(
                db.or_(
                    Designer.name.ilike("%yamasaki%"),
                    Designer.email.ilike("%yamasaki%"),
                )
            )
            .first()
        )
        if not designer:
            print("❌ yamasaki に該当するデザイナーが見つかりません")
            designers = Designer.query.all()
            print("登録中のデザイナー一覧:")
            for d in designers:
                print(f"  id={d.id}  name={d.name}  email={d.email}")
            return

        print(f"✓ デザイナー確認: {designer.name} ({designer.email})  id={designer.id}")

        # ── 担当企業を取得 ────────────────────────────────────────────────────
        client_ids = [a.client_id for a in designer.assignments]
        clients = Client.query.filter(Client.id.in_(client_ids)).order_by(Client.id).all() if client_ids else []
        print(f"  担当企業: {len(clients)} 件")
        for c in clients:
            print(f"    - {c.name}  月額={c.monthly_fee:,}円  type={c.platform_type}")

        if not clients:
            print("⚠️  担当企業が0件のため、ダミー明細でサンプルを作成します")

        # ── 先月・先々月の請求書を作成 ────────────────────────────────────────
        # 今日は 2026-07-20 → 先月=6月, 先々月=5月
        target_months = [
            (2026, 6, "先月"),
            (2026, 5, "先々月"),
        ]

        for year, month, label in target_months:
            existing = Invoice.query.filter_by(
                designer_id=designer.id, year=year, month=month
            ).first()
            if existing:
                print(f"⚠️  {label}({year}/{month}) の請求書はすでに存在します (id={existing.id}) — スキップ")
                continue

            # 明細を組み立て
            if clients:
                items_data = [
                    {
                        "client_id": c.id,
                        "client_name": c.name,
                        "description": _plan_label(c),
                        "amount": c.monthly_fee or 0,
                    }
                    for c in clients
                ]
            else:
                # 担当企業なし → ダミー明細
                items_data = [
                    {"client_id": None, "client_name": "サンプル企業A", "description": "Instagram 運用代行 4件/月", "amount": 30000},
                    {"client_id": None, "client_name": "サンプル企業B", "description": "WordPress 記事制作 4件/月", "amount": 25000},
                ]

            total_amount = sum(i["amount"] for i in items_data)

            invoice = Invoice(
                designer_id=designer.id,
                year=year,
                month=month,
                total_amount=total_amount,
                status="issued",
            )
            db.session.add(invoice)
            db.session.flush()

            for item_data in items_data:
                db.session.add(InvoiceItem(
                    invoice_id=invoice.id,
                    client_id=item_data["client_id"],
                    client_name=item_data["client_name"],
                    description=item_data["description"],
                    amount=item_data["amount"],
                ))

            db.session.commit()
            print(f"✓ {label}({year}年{month}月) 請求書作成  合計={total_amount:,}円  明細={len(items_data)}件  id={invoice.id}")

            # PDF 生成
            try:
                from billing import generate_invoice_pdf
                items_list = InvoiceItem.query.filter_by(invoice_id=invoice.id).all()
                pdf_path = generate_invoice_pdf(invoice, items_list)
                invoice.pdf_path = pdf_path
                db.session.commit()
                print(f"  PDF: {pdf_path}")
            except Exception as e:
                print(f"  PDF生成スキップ: {e}")

        print("\n完了!")


def _plan_label(client) -> str:
    count = client.monthly_post_count or 4
    labels = {
        "instagram": f"Instagram 運用代行 {count}件/月",
        "wordpress":  f"WordPress 記事制作 {count}件/月",
        "custom_hp":  f"独自HP 記事制作 {count}件/月",
    }
    return labels.get(client.platform_type or "", f"ブログ運用代行 {count}件/月")


if __name__ == "__main__":
    main()
