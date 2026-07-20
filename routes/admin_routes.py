"""routes/admin_routes.py — 管理者専用画面"""
import os
from flask import render_template, request, redirect, url_for, flash, abort, send_file
from flask_login import login_required, current_user
from models import db, Designer, Client, Post, DesignerClient, PricingPlan, Invoice, InvoiceItem
from routes import designer_bp


def _admin_only():
    if current_user.role != "admin":
        abort(403)


@designer_bp.route("/admin/designers")
@login_required
def admin_designers():
    _admin_only()
    designers = Designer.query.order_by(Designer.name).all()

    stats = []
    for d in designers:
        clients = [a.client for a in d.assignments if a.client]

        ig_count  = sum(1 for c in clients if c.platform_type == "instagram")
        wp_count  = sum(1 for c in clients if c.platform_type == "wordpress")
        hp_count  = sum(1 for c in clients if c.platform_type == "custom_hp")

        total_posts    = sum(c.monthly_post_count or 0 for c in clients)
        total_clients  = len(clients)
        total_revenue  = sum(c.monthly_fee or 0 for c in clients)

        stats.append({
            "designer":       d,
            "ig_count":       ig_count,
            "wp_count":       wp_count,
            "hp_count":       hp_count,
            "total_posts":    total_posts,
            "total_clients":  total_clients,
            "total_revenue":  total_revenue,
        })

    return render_template("designer/admin/designers.html", stats=stats)


@designer_bp.route("/admin/designers/<int:designer_id>")
@login_required
def admin_designer_detail(designer_id: int):
    _admin_only()
    designer = Designer.query.get_or_404(designer_id)
    clients = [a.client for a in designer.assignments if a.client]
    total_revenue = sum(c.monthly_fee or 0 for c in clients)
    return render_template(
        "designer/admin/designer_detail.html",
        designer=designer,
        clients=clients,
        total_revenue=total_revenue,
    )


@designer_bp.route("/admin/designers/<int:designer_id>/edit", methods=["GET", "POST"])
@login_required
def admin_designer_edit(designer_id: int):
    _admin_only()
    designer = Designer.query.get_or_404(designer_id)

    if request.method == "POST":
        designer.name          = request.form.get("name", designer.name).strip()
        designer.business_name = request.form.get("business_name", "").strip()
        designer.region        = request.form.get("region", "").strip()
        designer.job_type      = request.form.get("job_type", "").strip()
        designer.bank_account  = request.form.get("bank_account", "").strip()
        if current_user.role == "admin":
            new_role = request.form.get("role", "designer")
            if new_role in ("designer", "admin"):
                designer.role = new_role
        new_pass = request.form.get("new_password", "").strip()
        if new_pass:
            if len(new_pass) < 8:
                flash("パスワードは8文字以上にしてください", "error")
                return render_template("designer/admin/designer_edit.html", designer=designer)
            designer.set_password(new_pass)
        db.session.commit()
        flash("デザイナー情報を更新しました", "success")
        return redirect(url_for("designer.admin_designer_detail", designer_id=designer_id))

    return render_template("designer/admin/designer_edit.html", designer=designer)


# ─── 料金プラン管理 ────────────────────────────────────────────────────────────

_PLATFORM_LABELS = {
    "instagram": "Instagram",
    "wordpress": "WordPress",
    "custom_hp": "独自HP",
}


@designer_bp.route("/admin/pricing")
@login_required
def admin_pricing():
    _admin_only()
    plans = PricingPlan.query.order_by(PricingPlan.platform_type, PricingPlan.sort_order).all()
    return render_template("designer/admin/pricing.html", plans=plans, platform_labels=_PLATFORM_LABELS)


@designer_bp.route("/admin/pricing/new", methods=["GET", "POST"])
@login_required
def admin_pricing_new():
    _admin_only()
    if request.method == "POST":
        pt = request.form.get("platform_type", "instagram")
        posts = int(request.form.get("monthly_posts", 4) or 4)
        fee = int(request.form.get("monthly_fee", 0) or 0)
        last = PricingPlan.query.filter_by(platform_type=pt).order_by(PricingPlan.sort_order.desc()).first()
        sort_order = (last.sort_order + 1) if last else 0
        db.session.add(PricingPlan(platform_type=pt, monthly_posts=posts, monthly_fee=fee, sort_order=sort_order))
        db.session.commit()
        flash("料金プランを追加しました", "success")
        return redirect(url_for("designer.admin_pricing"))
    return render_template("designer/admin/pricing_edit.html", plan=None, platform_labels=_PLATFORM_LABELS)


@designer_bp.route("/admin/pricing/<int:plan_id>/edit", methods=["GET", "POST"])
@login_required
def admin_pricing_edit(plan_id: int):
    _admin_only()
    plan = PricingPlan.query.get_or_404(plan_id)
    if request.method == "POST":
        plan.platform_type = request.form.get("platform_type", plan.platform_type)
        plan.monthly_posts = int(request.form.get("monthly_posts", plan.monthly_posts) or plan.monthly_posts)
        plan.monthly_fee = int(request.form.get("monthly_fee", plan.monthly_fee) or 0)
        db.session.commit()
        flash("料金プランを更新しました", "success")
        return redirect(url_for("designer.admin_pricing"))
    return render_template("designer/admin/pricing_edit.html", plan=plan, platform_labels=_PLATFORM_LABELS)


@designer_bp.route("/admin/pricing/<int:plan_id>/delete", methods=["POST"])
@login_required
def admin_pricing_delete(plan_id: int):
    _admin_only()
    plan = PricingPlan.query.get_or_404(plan_id)
    db.session.delete(plan)
    db.session.commit()
    flash("削除しました", "success")
    return redirect(url_for("designer.admin_pricing"))


# ─── 請求書管理 ────────────────────────────────────────────────────────────────

@designer_bp.route("/admin/invoices")
@login_required
def admin_invoices():
    _admin_only()
    invoices = (
        Invoice.query
        .join(Designer)
        .order_by(Invoice.year.desc(), Invoice.month.desc(), Designer.name)
        .all()
    )
    return render_template("designer/admin/invoices.html", invoices=invoices)


@designer_bp.route("/admin/invoices/<int:invoice_id>")
@login_required
def admin_invoice_detail(invoice_id: int):
    _admin_only()
    invoice = Invoice.query.get_or_404(invoice_id)
    items = InvoiceItem.query.filter_by(invoice_id=invoice_id).all()
    return render_template("designer/admin/invoice_detail.html",
                           invoice=invoice, items=items)


@designer_bp.route("/admin/invoices/<int:invoice_id>/pdf")
@login_required
def admin_invoice_pdf(invoice_id: int):
    _admin_only()
    invoice = Invoice.query.get_or_404(invoice_id)
    if not invoice.pdf_path or not os.path.exists(invoice.pdf_path):
        # PDF が存在しなければ再生成
        items = InvoiceItem.query.filter_by(invoice_id=invoice_id).all()
        try:
            from billing import generate_invoice_pdf
            pdf_path = generate_invoice_pdf(invoice, items)
            invoice.pdf_path = pdf_path
            db.session.commit()
        except Exception as e:
            flash(f"PDF生成エラー: {e}", "error")
            return redirect(url_for("designer.admin_invoice_detail", invoice_id=invoice_id))
    return send_file(
        invoice.pdf_path,
        as_attachment=True,
        download_name=f"invoice_{invoice.year}{invoice.month:02d}_{invoice.designer.name}.pdf",
        mimetype="application/pdf",
    )


@designer_bp.route("/admin/invoices/generate", methods=["POST"])
@login_required
def admin_invoice_generate():
    """手動で当月分の請求書を一括生成する。"""
    _admin_only()
    try:
        from batch_monthly import run_monthly_billing_batch
        from models import db as _db
        from flask import current_app
        result = run_monthly_billing_batch(current_app._get_current_object(), _db)
        if result["errors"]:
            flash(f"一部エラー: {'; '.join(result['errors'][:3])}", "warning")
        flash(f"{result['invoices']} 件の請求書を生成しました", "success")
    except Exception as e:
        flash(f"エラー: {e}", "error")
    return redirect(url_for("designer.admin_invoices"))


@designer_bp.route("/admin/invoices/<int:invoice_id>/status", methods=["POST"])
@login_required
def admin_invoice_status(invoice_id: int):
    """管理者のみ: 請求書ステータスを更新する（入金確認用）。"""
    _admin_only()
    invoice = Invoice.query.get_or_404(invoice_id)
    new_status = request.form.get("status", "")
    if new_status in ("issued", "paid", "draft"):
        invoice.status = new_status
        db.session.commit()
        flash(f"ステータスを「{invoice.status_label}」に変更しました", "success")
    else:
        flash("無効なステータスです", "error")
    return redirect(url_for("designer.admin_invoice_detail", invoice_id=invoice_id))


@designer_bp.route("/admin/invoices/<int:invoice_id>/edit", methods=["GET", "POST"])
@login_required
def admin_invoice_edit(invoice_id: int):
    _admin_only()
    invoice = Invoice.query.get_or_404(invoice_id)
    items = InvoiceItem.query.filter_by(invoice_id=invoice_id).order_by(InvoiceItem.id).all()
    if request.method == "POST":
        for item in items:
            item.description = request.form.get(f"desc_{item.id}", item.description).strip()
            try:
                item.amount = int(request.form.get(f"amount_{item.id}", item.amount) or 0)
            except (ValueError, TypeError):
                pass
        invoice.total_amount = sum(i.amount for i in items)
        invoice.discount_type = request.form.get("discount_type", "")
        try:
            invoice.discount_value = float(request.form.get("discount_value", 0) or 0)
        except (ValueError, TypeError):
            invoice.discount_value = 0.0
        invoice.discount_target = request.form.get("discount_target", "pretax")
        db.session.commit()
        flash("請求書を更新しました", "success")
        return redirect(url_for("designer.admin_invoice_edit", invoice_id=invoice_id))
    return render_template("designer/admin/invoice_edit.html", invoice=invoice, items=items)


@designer_bp.route("/admin/invoices/<int:invoice_id>/items/<int:item_id>/delete", methods=["POST"])
@login_required
def admin_invoice_item_delete(invoice_id: int, item_id: int):
    _admin_only()
    item = InvoiceItem.query.get_or_404(item_id)
    if item.invoice_id != invoice_id:
        abort(403)
    invoice = Invoice.query.get_or_404(invoice_id)
    invoice.total_amount = max(0, (invoice.total_amount or 0) - (item.amount or 0))
    db.session.delete(item)
    db.session.commit()
    flash("明細を削除しました", "success")
    return redirect(url_for("designer.admin_invoice_edit", invoice_id=invoice_id))


@designer_bp.route("/admin/invoices/<int:invoice_id>/items/add", methods=["POST"])
@login_required
def admin_invoice_item_add(invoice_id: int):
    _admin_only()
    invoice = Invoice.query.get_or_404(invoice_id)
    client_name = request.form.get("client_name", "").strip()
    description = request.form.get("description", "").strip()
    try:
        amount = int(request.form.get("amount", 0) or 0)
    except (ValueError, TypeError):
        amount = 0
    if client_name or description:
        db.session.add(InvoiceItem(
            invoice_id=invoice_id,
            client_name=client_name,
            description=description,
            amount=amount,
        ))
        invoice.total_amount = (invoice.total_amount or 0) + amount
        db.session.commit()
        flash("明細を追加しました", "success")
    return redirect(url_for("designer.admin_invoice_edit", invoice_id=invoice_id))


@designer_bp.route("/admin/invoices/<int:invoice_id>/reissue", methods=["POST"])
@login_required
def admin_invoice_reissue(invoice_id: int):
    _admin_only()
    invoice = Invoice.query.get_or_404(invoice_id)
    items = InvoiceItem.query.filter_by(invoice_id=invoice_id).all()
    try:
        from billing import generate_invoice_pdf
        from mailer import send_invoice_email
        from datetime import datetime as _dt
        pdf_path = generate_invoice_pdf(invoice, items)
        invoice.pdf_path = pdf_path
        designer = invoice.designer
        if designer and designer.email:
            r = send_invoice_email(designer.email, designer.name, invoice, pdf_path)
            if r.get("success"):
                invoice.status = "sent"
                invoice.sent_at = _dt.utcnow()
                db.session.commit()
                flash("請求書を再発行・送付しました", "success")
            else:
                db.session.commit()
                flash(f"PDF再生成済み（メール送付失敗: {r.get('reason')}）", "warning")
        else:
            db.session.commit()
            flash("PDFを再生成しました", "success")
    except Exception as e:
        flash(f"再発行エラー: {e}", "error")
    return redirect(url_for("designer.admin_invoices"))


@designer_bp.route("/admin/invoices/<int:invoice_id>/delete", methods=["POST"])
@login_required
def admin_invoice_delete(invoice_id: int):
    _admin_only()
    invoice = Invoice.query.get_or_404(invoice_id)
    if invoice.pdf_path and os.path.exists(invoice.pdf_path):
        try:
            os.remove(invoice.pdf_path)
        except OSError:
            pass
    db.session.delete(invoice)
    db.session.commit()
    flash("請求書を削除しました", "success")
    return redirect(url_for("designer.admin_invoices"))
