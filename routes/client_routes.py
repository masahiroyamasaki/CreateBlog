"""routes/client_routes.py — 契約企業の管理"""
from flask import render_template, request, redirect, url_for, flash, abort, send_file
from flask_login import login_required, current_user
from models import db, Client, DesignerClient, Post, TopicQueue, Designer, Invoice, InvoiceItem
from config import encrypt_field, decrypt_field
from routes import designer_bp


def _get_accessible_clients():
    if current_user.role == "admin":
        return Client.query.order_by(Client.name).all()
    ids = [a.client_id for a in current_user.assignments]
    return Client.query.filter(Client.id.in_(ids)).order_by(Client.name).all()


def _assert_access(client: Client):
    if not current_user.can_access_client(client.id):
        abort(403)


@designer_bp.route("/profile", methods=["GET", "POST"])
@login_required
def my_profile():
    """デザイナー自身のプロフィール編集"""
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("氏名は必須です", "error")
            return redirect(url_for("designer.my_profile"))
        current_user.name = name
        current_user.business_name = request.form.get("business_name", "").strip()
        current_user.region = request.form.get("region", "").strip()
        current_user.job_type = request.form.get("job_type", "").strip()
        new_password = request.form.get("new_password", "").strip()
        if new_password:
            if len(new_password) < 8:
                flash("パスワードは8文字以上で入力してください", "error")
                return redirect(url_for("designer.my_profile"))
            current_user.set_password(new_password)
        db.session.commit()
        flash("プロフィールを更新しました", "success")
        return redirect(url_for("designer.my_profile"))
    return render_template("designer/profile.html")


@designer_bp.route("/my-invoices")
@login_required
def my_invoices():
    """デザイナー自身の請求書一覧を表示する。"""
    invoices = (
        Invoice.query
        .filter_by(designer_id=current_user.id)
        .order_by(Invoice.year.desc(), Invoice.month.desc())
        .all()
    )
    return render_template("designer/my_invoices.html", invoices=invoices)


@designer_bp.route("/my-invoices/<int:invoice_id>/pdf")
@login_required
def my_invoice_pdf(invoice_id: int):
    """デザイナー自身の請求書PDFをダウンロードする。"""
    import os
    invoice = Invoice.query.get_or_404(invoice_id)
    if invoice.designer_id != current_user.id:
        abort(403)
    if not invoice.pdf_path or not os.path.exists(invoice.pdf_path):
        items = InvoiceItem.query.filter_by(invoice_id=invoice_id).all()
        try:
            from billing import generate_invoice_pdf
            pdf_path = generate_invoice_pdf(invoice, items)
            invoice.pdf_path = pdf_path
            db.session.commit()
        except Exception as e:
            flash(f"PDF生成エラー: {e}", "error")
            return redirect(url_for("designer.my_invoices"))
    return send_file(
        invoice.pdf_path,
        as_attachment=True,
        download_name=f"invoice_{invoice.year}{invoice.month:02d}.pdf",
        mimetype="application/pdf",
    )


@designer_bp.route("/clients")
@login_required
def clients():
    client_list = _get_accessible_clients()
    return render_template("designer/clients/list.html", clients=client_list)


@designer_bp.route("/clients/<int:client_id>")
@login_required
def client_detail(client_id: int):
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    recent_posts = (
        Post.query.filter_by(client_id=client_id)
        .order_by(Post.created_at.desc())
        .limit(10)
        .all()
    )
    pending_count = TopicQueue.query.filter_by(
        client_id=client_id, status="pending"
    ).count()
    return render_template(
        "designer/clients/detail.html",
        client=client,
        posts=recent_posts,
        pending_count=pending_count,
    )


@designer_bp.route("/clients/new", methods=["GET", "POST"])
@login_required
def client_new():
    if current_user.role != "admin":
        abort(403)
    designers = Designer.query.order_by(Designer.name).all()
    if request.method == "POST":
        stype = request.form.get("schedule_type", "weekly")
        client = Client(
            name=request.form["name"],
            platform_type=request.form.get("platform_type", "wordpress"),
            client_status=request.form.get("client_status", "active"),
            monthly_post_count=int(request.form.get("monthly_post_count", 4) or 4),
            monthly_fee=int(request.form.get("monthly_fee", 0) or 0),
            wp_endpoint=request.form.get("wp_endpoint", ""),
            wp_username=request.form.get("wp_username", ""),
            wp_app_password=encrypt_field(request.form.get("wp_app_password", "")),
            ig_business_account_id=request.form.get("ig_business_account_id", ""),
            ig_access_token=encrypt_field(request.form.get("ig_access_token", "")),
            ig_hashtags=request.form.get("ig_hashtags", ""),
            themes=request.form.get("themes", ""),
            custom_url=request.form.get("custom_url", ""),
            schedule_type=stype,
            schedule_days_of_week=",".join(request.form.getlist("schedule_days_of_week")) or "0",
            schedule_days_of_month=",".join(request.form.getlist("schedule_days_of_month")) or "1",
            default_post_time=request.form.get("default_post_time") or None,
        )
        db.session.add(client)
        db.session.flush()

        # 選択されたデザイナーをアサイン（未選択の場合は作成者）
        raw_id = request.form.get("designer_id", "").strip()
        assign_id = int(raw_id) if raw_id else current_user.id
        db.session.add(DesignerClient(designer_id=assign_id, client_id=client.id))
        db.session.commit()
        flash(f"「{client.name}」を追加しました", "success")
        return redirect(url_for("designer.client_detail", client_id=client.id))
    return render_template("designer/clients/form.html", client=None, designers=designers)


@designer_bp.route("/clients/<int:client_id>/edit", methods=["GET", "POST"])
@login_required
def client_edit(client_id: int):
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    if request.method == "POST":
        client.name = request.form["name"]
        client.platform_type = request.form.get("platform_type", "wordpress_instagram")
        client.client_status = request.form.get("client_status", "active")
        client.monthly_post_count = int(request.form.get("monthly_post_count", 4) or 4)
        client.monthly_fee = int(request.form.get("monthly_fee", 0) or 0)
        client.schedule_type = request.form.get("schedule_type", "weekly")
        client.schedule_days_of_week = ",".join(request.form.getlist("schedule_days_of_week")) or "0"
        client.schedule_days_of_month = ",".join(request.form.getlist("schedule_days_of_month")) or "1"
        client.wp_endpoint = request.form.get("wp_endpoint", "")
        client.wp_username = request.form.get("wp_username", "")
        new_wp_pass = request.form.get("wp_app_password", "")
        if new_wp_pass:
            client.wp_app_password = encrypt_field(new_wp_pass)
        client.ig_business_account_id = request.form.get("ig_business_account_id", "")
        new_ig_token = request.form.get("ig_access_token", "")
        if new_ig_token:
            client.ig_access_token = encrypt_field(new_ig_token)
        client.ig_hashtags = request.form.get("ig_hashtags", "")
        client.themes = request.form.get("themes", "")
        client.custom_url = request.form.get("custom_url", "")
        client.default_post_time = request.form.get("default_post_time") or None
        db.session.commit()
        flash("変更を保存しました", "success")
        return redirect(url_for("designer.client_detail", client_id=client_id))
    # フォームには復号して渡す
    form_data = {
        "wp_app_password": decrypt_field(client.wp_app_password),
        "ig_access_token": decrypt_field(client.ig_access_token),
    }
    return render_template("designer/clients/form.html", client=client, form_data=form_data)


@designer_bp.route("/clients/<int:client_id>/assign", methods=["POST"])
@login_required
def client_assign(client_id: int):
    if current_user.role != "admin":
        abort(403)
    client = Client.query.get_or_404(client_id)
    designer_id = int(request.form["designer_id"])
    exists = DesignerClient.query.filter_by(
        designer_id=designer_id, client_id=client_id
    ).first()
    if not exists:
        db.session.add(DesignerClient(designer_id=designer_id, client_id=client_id))
        db.session.commit()
    return redirect(url_for("designer.client_detail", client_id=client_id))
