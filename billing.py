"""billing.py — 請求書PDF生成

使用フォント: reportlab 組み込みの CID フォント (HeiseiKakuGo-W5) を使用。
システムフォントのインストール不要。
"""
import os
from datetime import datetime, timezone, timedelta

_JST = timezone(timedelta(hours=9))

ISSUER = {
    "name": "RKパートナーズ",
    "rep": "代表　山﨑粛福",
    "address": "大阪府羽曳野市碓井4-22-5",
    "email": "info@rk-rpa.com",
}

INVOICE_DIR = os.path.join(os.path.dirname(__file__), "invoices")


def _ensure_dir():
    os.makedirs(INVOICE_DIR, exist_ok=True)


def generate_invoice_pdf(invoice, items_list) -> str:
    """Invoice と InvoiceItem のリストを受け取りPDFを生成してパスを返す。"""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    except ImportError:
        raise ImportError("reportlab がインストールされていません。pip install reportlab を実行してください。")

    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
    FONT = "HeiseiKakuGo-W5"

    _ensure_dir()
    filename = f"invoice_{invoice.year}{invoice.month:02d}_{invoice.designer_id:04d}.pdf"
    filepath = os.path.join(INVOICE_DIR, filename)

    W, H = A4
    margin = 20 * mm
    content_w = W - 2 * margin

    doc = SimpleDocTemplate(
        filepath, pagesize=A4,
        rightMargin=margin, leftMargin=margin,
        topMargin=margin, bottomMargin=margin,
    )

    def ps(size=10, align=TA_LEFT, color=colors.HexColor("#1e293b")):
        return ParagraphStyle("p", fontName=FONT, fontSize=size,
                              textColor=color, alignment=align, leading=size * 1.6)

    elements = []

    # ── タイトル ──
    elements.append(Paragraph("請　求　書", ps(22, TA_CENTER)))
    elements.append(Spacer(1, 6 * mm))

    # ── 請求番号・発行日 ──
    issue_date = datetime.now(_JST).strftime("%Y年%m月%d日")
    meta = Table(
        [["請求番号", invoice.invoice_number, "発行日", issue_date],
         ["対象月", f"{invoice.year}年{invoice.month}月分", "", ""]],
        colWidths=[28*mm, 62*mm, 22*mm, 58*mm],
    )
    meta.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#64748b")),
        ("TEXTCOLOR", (2, 0), (2, -1), colors.HexColor("#64748b")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    elements.append(meta)
    elements.append(Spacer(1, 8 * mm))

    # ── 請求先・請求元 ──
    designer = invoice.designer
    to_lines = [
        designer.name + "　様",
        designer.business_name or "",
        designer.email,
    ]
    from_lines = [
        ISSUER["name"],
        ISSUER["rep"],
        ISSUER["address"],
        ISSUER["email"],
    ]
    max_r = max(len(to_lines), len(from_lines))
    party_data = [["【請求先】", "【請求元】"]]
    for i in range(max_r):
        party_data.append([
            to_lines[i] if i < len(to_lines) else "",
            from_lines[i] if i < len(from_lines) else "",
        ])

    half = content_w / 2
    party = Table(party_data, colWidths=[half, half])
    party.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("FONTSIZE", (0, 1), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#64748b")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("BOX", (0, 0), (0, -1), 0.5, colors.HexColor("#e2e8f0")),
        ("BOX", (1, 0), (1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ("LINEAFTER", (0, 0), (0, -1), 0.5, colors.HexColor("#e2e8f0")),
    ]))
    elements.append(party)
    elements.append(Spacer(1, 10 * mm))

    # ── 合計金額ハイライト ──
    total_fmt = f"¥{invoice.total_amount:,}"
    total_tbl = Table(
        [["ご請求金額（税込）", total_fmt]],
        colWidths=[content_w * 0.65, content_w * 0.35],
    )
    total_tbl.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("FONTSIZE", (0, 0), (0, 0), 12),
        ("FONTSIZE", (1, 0), (1, 0), 16),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#6366f1")),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ]))
    elements.append(total_tbl)
    elements.append(Spacer(1, 8 * mm))

    # ── 明細テーブル ──
    col_no = 10 * mm
    col_name = 52 * mm
    col_desc = content_w - col_no - col_name - 32 * mm
    col_amt = 32 * mm

    item_data = [["No.", "企業名", "内容", "金額"]]
    for i, item in enumerate(items_list, 1):
        item_data.append([
            str(i),
            item.client_name,
            item.description,
            f"¥{item.amount:,}",
        ])
    # 合計行
    item_data.append(["", "", "合　計", f"¥{invoice.total_amount:,}"])

    item_tbl = Table(item_data, colWidths=[col_no, col_name, col_desc, col_amt])
    last = len(item_data) - 1
    item_tbl.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        # ヘッダー
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        # 行背景
        ("ROWBACKGROUNDS", (0, 1), (-1, last - 1), [colors.white, colors.HexColor("#f8fafc")]),
        # 合計行
        ("BACKGROUND", (0, last), (-1, last), colors.HexColor("#f1f5f9")),
        ("FONTSIZE", (0, last), (-1, last), 11),
        # 数値右揃え
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (3, 0), (3, -1), "RIGHT"),
        ("ALIGN", (2, last), (2, last), "RIGHT"),
        # グリッド
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(item_tbl)
    elements.append(Spacer(1, 10 * mm))

    # ── 備考 ──
    elements.append(Paragraph(
        "※ お支払い期限：翌月末日",
        ps(9, color=colors.HexColor("#64748b")),
    ))

    doc.build(elements)
    return filepath
