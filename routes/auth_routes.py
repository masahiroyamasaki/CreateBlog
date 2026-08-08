"""routes/auth_routes.py — ログイン / 新規登録（契約確認 → 情報入力）/ ログアウト"""
import hashlib
import json
import os
from datetime import datetime, timezone, timedelta
from flask import render_template, request, redirect, url_for, flash, session, send_file, abort
from flask_login import login_user, logout_user, login_required, current_user
from models import db, Designer
from routes import designer_bp

_JST = timezone(timedelta(hours=9))


def _contract_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _get_or_seed_contract():
    """ContractTemplate v1.0 を返す。未作成なら自動作成。本文が変更されていれば更新する。"""
    from models import ContractTemplate
    from contract_text import CONTRACT_VERSION, CONTRACT_BODY, CONTRACT_EFFECTIVE_DATE
    from datetime import date

    tmpl = ContractTemplate.query.filter_by(version=CONTRACT_VERSION).first()
    if not tmpl:
        tmpl = ContractTemplate(
            version=CONTRACT_VERSION,
            body_text=CONTRACT_BODY,
            effective_date=date(2026, 8, 1),
            is_current=True,
        )
        db.session.add(tmpl)
        db.session.commit()
    elif tmpl.body_text != CONTRACT_BODY:
        tmpl.body_text = CONTRACT_BODY
        db.session.commit()
    return tmpl


# ──────────────────────────────────────── ログイン ────────────────────────────

@designer_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("designer.clients"))

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        remember = bool(request.form.get("remember"))

        designer = Designer.query.filter_by(email=email).first()
        if designer and designer.check_password(password):
            designer.last_login_at = datetime.utcnow()
            db.session.commit()
            login_user(designer, remember=remember)
            next_page = request.args.get("next")
            return redirect(next_page or url_for("designer.clients"))

        flash("メールアドレスまたはパスワードが正しくありません", "error")

    return render_template("designer/login.html")


# ─────────────────────────────── 画面A：契約書確認 ───────────────────────────

@designer_bp.route("/register/contract", methods=["GET", "POST"])
def register_contract():
    if current_user.is_authenticated:
        return redirect(url_for("designer.clients"))

    from contract_text import CONTRACT_CHECKBOX_TEXT

    tmpl = _get_or_seed_contract()

    if request.method == "POST":
        agreed = request.form.get("agreed") == "1"
        if not agreed:
            flash("契約書に同意してください", "error")
            return redirect(url_for("designer.register_contract"))

        now_utc = datetime.utcnow()
        session["agreement"] = {
            "version": tmpl.version,
            "contract_hash": _contract_hash(tmpl.body_text),
            "agreed_at": now_utc.isoformat(),
            "checkbox_text": CONTRACT_CHECKBOX_TEXT,
            "ip": request.remote_addr or "",
            "user_agent": request.user_agent.string or "",
        }
        return redirect(url_for("designer.register"))

    return render_template(
        "designer/contract_confirm.html",
        contract=tmpl,
        checkbox_text=CONTRACT_CHECKBOX_TEXT,
    )


# ─────────────────────── テンプレートPDFダウンロード（登録前） ────────────────

@designer_bp.route("/register/contract/template-pdf")
def register_contract_template_pdf():
    """登録前に契約書本文のみのPDFをダウンロードできる。"""
    import io
    tmpl = _get_or_seed_contract()

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.lib.enums import TA_CENTER
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.lib import colors
    except ImportError:
        abort(500, "PDF生成ライブラリが利用できません")

    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
    FONT = "HeiseiKakuGo-W5"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20*mm, rightMargin=20*mm, topMargin=20*mm, bottomMargin=20*mm)

    s_title = ParagraphStyle("t", fontName=FONT, fontSize=16, leading=22, alignment=TA_CENTER, spaceAfter=6)
    s_sub = ParagraphStyle("s", fontName=FONT, fontSize=9, leading=13, alignment=TA_CENTER, textColor=colors.HexColor("#666666"), spaceAfter=16)
    s_sec = ParagraphStyle("sec", fontName=FONT, fontSize=11, leading=16, spaceBefore=12, spaceAfter=4)
    s_body = ParagraphStyle("b", fontName=FONT, fontSize=9, leading=15, spaceAfter=3)

    story = [
        Paragraph("Artivo AIパートナー利用契約書（参考版）", s_title),
        Paragraph(f"バージョン：{tmpl.version}　／　制定日：2026年8月1日", s_sub),
        HRFlowable(width="100%", thickness=1, color=colors.HexColor("#6366f1"), spaceAfter=12),
    ]

    for line in tmpl.body_text.strip().split("\n"):
        line = line.rstrip()
        if not line:
            story.append(Spacer(1, 4))
        elif line.startswith("━"):
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc"), spaceBefore=6, spaceAfter=2))
        elif line.startswith("第") and "条" in line:
            story.append(Paragraph(line, s_sec))
        else:
            story.append(Paragraph(line, s_body))

    doc.build(story)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="artivo_ai_partner_agreement.pdf", mimetype="application/pdf")


# ─────────────────────────────── 画面B：情報入力 + 登録処理 ──────────────────

@designer_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("designer.clients"))

    # 契約同意がセッションにない場合は画面Aへ
    if "agreement" not in session:
        flash("はじめに利用契約書をご確認ください", "info")
        return redirect(url_for("designer.register_contract"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")
        business_name = request.form.get("business_name", "").strip()
        postal_code = request.form.get("postal_code", "").strip()
        address = request.form.get("address", "").strip()
        phone = request.form.get("phone", "").strip()
        region = request.form.get("region", "").strip()
        job_type = request.form.get("job_type", "").strip()
        bank_account = request.form.get("bank_account", "").strip()
        invoice_number = request.form.get("invoice_number", "").strip()

        error = None
        if not email or not name or not password:
            error = "メールアドレス・氏名・パスワードは必須です"
        elif password != password2:
            error = "パスワードが一致しません"
        elif len(password) < 8:
            error = "パスワードは8文字以上にしてください"
        elif Designer.query.filter_by(email=email).first():
            error = "このメールアドレスはすでに登録されています"

        if error:
            flash(error, "error")
        else:
            agr_info = session.get("agreement", {})

            # ① アカウント作成
            designer = Designer(
                name=name,
                email=email,
                business_name=business_name,
                postal_code=postal_code,
                address=address,
                phone=phone,
                region=region,
                job_type=job_type,
                bank_account=bank_account,
                invoice_number=invoice_number,
                role="designer",
            )
            designer.set_password(password)
            db.session.add(designer)
            db.session.flush()  # ID を確定

            # ② 同意ログ保存
            from models import DesignerAgreement, AgreementPdf
            snapshot = {
                "name": name, "email": email,
                "business_name": business_name, "postal_code": postal_code,
                "address": address, "phone": phone, "region": region,
                "job_type": job_type, "invoice_number": invoice_number,
            }
            agreement = DesignerAgreement(
                designer_id=designer.id,
                contract_version=agr_info.get("version", "1.0"),
                contract_hash=agr_info.get("contract_hash", ""),
                agreed_at=datetime.fromisoformat(agr_info["agreed_at"]),
                checkbox_text=agr_info.get("checkbox_text", ""),
                ip_address=agr_info.get("ip", request.remote_addr or ""),
                user_agent=agr_info.get("user_agent", ""),
                snapshot_json=json.dumps(snapshot, ensure_ascii=False),
            )
            db.session.add(agreement)
            db.session.flush()

            # ③ PDF 生成
            pdf_path = ""
            try:
                from agreement_pdf import generate_agreement_pdf
                tmpl = _get_or_seed_contract()
                pdf_path = generate_agreement_pdf(designer, agreement, tmpl.body_text)
                agr_pdf = AgreementPdf(
                    designer_agreement_id=agreement.id,
                    pdf_path=pdf_path,
                )
                db.session.add(agr_pdf)
            except Exception as e:
                # PDF 生成失敗でも登録は続行
                import logging
                logging.getLogger(__name__).error(f"契約書PDF生成エラー: {e}")

            db.session.commit()

            # ④ セッションの同意情報を削除
            session.pop("agreement", None)

            # ⑤ 登録完了メール送信
            try:
                from mailer import send_registration_email
                send_registration_email(designer, pdf_path or None)
            except Exception:
                pass

            flash("登録が完了しました。ログインしてください。", "success")
            return redirect(url_for("designer.login"))

    return render_template("designer/register.html")


# ──────────────────────────────────────── ログアウト ──────────────────────────

@designer_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("designer.login"))


# ───────────────────── ダッシュボード：契約書閲覧 ────────────────────────────

@designer_bp.route("/my-contract")
@login_required
def my_contract():
    from models import DesignerAgreement
    agreements = (
        DesignerAgreement.query
        .filter_by(designer_id=current_user.id)
        .order_by(DesignerAgreement.agreed_at.desc())
        .all()
    )
    return render_template("designer/my_contract.html", agreements=agreements)


@designer_bp.route("/my-contract/<int:agreement_id>/download")
@login_required
def my_contract_download(agreement_id: int):
    from models import DesignerAgreement
    agreement = DesignerAgreement.query.get_or_404(agreement_id)
    if agreement.designer_id != current_user.id and current_user.role != "admin":
        abort(403)

    if not agreement.pdf or not os.path.exists(agreement.pdf.pdf_path):
        # PDF が存在しない場合は再生成
        try:
            from agreement_pdf import generate_agreement_pdf
            from models import AgreementPdf
            tmpl = _get_or_seed_contract()
            pdf_path = generate_agreement_pdf(current_user, agreement, tmpl.body_text)
            if agreement.pdf:
                agreement.pdf.pdf_path = pdf_path
                agreement.pdf.generated_at = datetime.utcnow()
            else:
                agr_pdf = AgreementPdf(designer_agreement_id=agreement.id, pdf_path=pdf_path)
                db.session.add(agr_pdf)
            db.session.commit()
        except Exception as e:
            flash(f"PDF生成に失敗しました: {e}", "error")
            return redirect(url_for("designer.my_contract"))

    import os as _os
    pdf_path = agreement.pdf.pdf_path
    filename = f"artivo_contract_v{agreement.contract_version}.pdf"
    return send_file(pdf_path, as_attachment=True, download_name=filename, mimetype="application/pdf")
