"""agreement_pdf.py — 契約書 PDF 生成（reportlab 使用）"""
import os
from datetime import datetime, timezone, timedelta

_JST = timezone(timedelta(hours=9))

AGREEMENT_DIR = os.path.join(os.path.dirname(__file__), "uploads", "agreements")

ISSUER = {
    "name": "RKパートナーズ",
    "rep": "代表　山﨑粛福",
    "address": "大阪府羽曳野市碓井4-22-5",
    "email": "info@rk-rpa.com",
}


def _ensure_dir(designer_id: int) -> str:
    path = os.path.join(AGREEMENT_DIR, str(designer_id))
    os.makedirs(path, exist_ok=True)
    return path


def generate_agreement_pdf(designer, agreement, contract_text: str) -> str:
    """確定版契約書 PDF を生成してファイルパスを返す。"""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.lib.enums import TA_LEFT, TA_CENTER
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    except ImportError:
        raise ImportError("reportlab がインストールされていません。pip install reportlab を実行してください。")

    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
    FONT = "HeiseiKakuGo-W5"

    out_dir = _ensure_dir(designer.id)
    ts = agreement.agreed_at.strftime("%Y%m%d%H%M%S")
    filename = f"agreement_v{agreement.contract_version}_{ts}.pdf"
    filepath = os.path.join(out_dir, filename)

    doc = SimpleDocTemplate(
        filepath,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )

    # スタイル定義
    s_title = ParagraphStyle("title", fontName=FONT, fontSize=16, leading=22, alignment=TA_CENTER, spaceAfter=6)
    s_subtitle = ParagraphStyle("subtitle", fontName=FONT, fontSize=9, leading=13, alignment=TA_CENTER, textColor=colors.HexColor("#666666"), spaceAfter=16)
    s_section = ParagraphStyle("section", fontName=FONT, fontSize=11, leading=16, spaceBefore=14, spaceAfter=4, textColor=colors.HexColor("#1a1a2e"))
    s_body = ParagraphStyle("body", fontName=FONT, fontSize=9, leading=15, spaceAfter=4, textColor=colors.HexColor("#222222"))
    s_info = ParagraphStyle("info", fontName=FONT, fontSize=9, leading=15, spaceAfter=3, textColor=colors.HexColor("#333333"))
    s_label = ParagraphStyle("label", fontName=FONT, fontSize=8, leading=12, textColor=colors.HexColor("#666666"))
    s_important = ParagraphStyle("important", fontName=FONT, fontSize=9, leading=15, spaceAfter=4,
                                  textColor=colors.HexColor("#dc2626"), backColor=colors.HexColor("#fff5f5"),
                                  borderPad=4)
    s_footer = ParagraphStyle("footer", fontName=FONT, fontSize=7, leading=11, alignment=TA_CENTER,
                               textColor=colors.HexColor("#999999"))
    s_sign = ParagraphStyle("sign", fontName=FONT, fontSize=9, leading=15, spaceAfter=3)

    story = []

    # ─── ヘッダー ───────────────────────────────────────────────────────────
    story.append(Paragraph("Artivo AIパートナー利用契約書", s_title))
    story.append(Paragraph(f"バージョン：{agreement.contract_version}　／　制定日：2026年8月1日", s_subtitle))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#6366f1"), spaceAfter=12))

    # ─── 契約当事者情報 ─────────────────────────────────────────────────────
    story.append(Paragraph("■ 契約当事者", s_section))
    agreed_jst = agreement.agreed_at + timedelta(hours=9)
    story.append(Paragraph(f"同意日時：{agreed_jst.strftime('%Y年%m月%d日 %H:%M:%S')}（JST）", s_info))
    story.append(Paragraph(f"契約書バージョン：{agreement.contract_version}", s_info))
    story.append(Paragraph(f"契約書識別ID：AGR-{agreement.id:08d}", s_info))
    story.append(Paragraph(f"SHA-256：{agreement.contract_hash}", s_label))
    story.append(Spacer(1, 8))

    story.append(Paragraph("【パートナー（甲）】", s_body))
    story.append(Paragraph(f"会社名・屋号：{designer.business_name or '　'}", s_info))
    story.append(Paragraph(f"氏名：{designer.name}", s_info))
    story.append(Paragraph(f"郵便番号：{designer.postal_code or '　'}", s_info))
    story.append(Paragraph(f"住所：{designer.address or '　'}", s_info))
    story.append(Paragraph(f"電話番号：{designer.phone or '　'}", s_info))
    story.append(Paragraph(f"メールアドレス：{designer.email}", s_info))
    if designer.invoice_number:
        story.append(Paragraph(f"インボイス登録番号：{designer.invoice_number}", s_info))
    story.append(Spacer(1, 6))

    story.append(Paragraph("【運営（乙）】", s_body))
    story.append(Paragraph(f"社名：{ISSUER['name']}", s_info))
    story.append(Paragraph(f"代表者：{ISSUER['rep']}", s_info))
    story.append(Paragraph(f"所在地：{ISSUER['address']}", s_info))
    story.append(Paragraph(f"連絡先：{ISSUER['email']}", s_info))

    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc"), spaceBefore=12, spaceAfter=12))

    # ─── 契約本文 ────────────────────────────────────────────────────────────
    story.append(Paragraph("■ 契約内容", s_section))

    for line in contract_text.strip().split("\n"):
        line = line.rstrip()
        if not line:
            story.append(Spacer(1, 4))
        elif line.startswith("━"):
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc"), spaceBefore=6, spaceAfter=2))
        elif line.startswith("第") and "条" in line and "【最重要条項】" in line:
            story.append(Paragraph(line, s_section))
            story.append(Paragraph("※ この条項はパートナーにとって特に重要です。必ずお読みください。", s_important))
        elif line.startswith("第") and "条" in line:
            story.append(Paragraph(line, s_section))
        elif line.startswith("【") or line.startswith("Artivo"):
            story.append(Paragraph(line, s_section))
        else:
            story.append(Paragraph(line, s_body))

    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#6366f1"), spaceBefore=16, spaceAfter=12))

    # ─── 署名欄 ──────────────────────────────────────────────────────────────
    story.append(Paragraph("■ 電子同意記録", s_section))
    story.append(Paragraph(f"本契約書は、パートナーが Artivo AI 登録フローにおいてチェックボックスへの同意操作を行うことにより締結されました。", s_body))
    story.append(Spacer(1, 4))
    story.append(Paragraph(f"同意文言：「{agreement.checkbox_text}」", s_body))
    story.append(Paragraph(f"同意日時：{agreed_jst.strftime('%Y年%m月%d日 %H:%M:%S')}（JST）", s_body))
    story.append(Paragraph(f"IPアドレス：{agreement.ip_address}", s_body))
    story.append(Spacer(1, 16))

    # ─── フッター ────────────────────────────────────────────────────────────
    story.append(Paragraph(f"AGR-{agreement.id:08d}　／　契約書 v{agreement.contract_version}　／　{ISSUER['name']}　／　{ISSUER['email']}", s_footer))

    doc.build(story)
    return filepath
