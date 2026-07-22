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
    from stripe_utils import can_add_client
    if not can_add_client(current_user):
        flash("企業を追加するにはプランへの登録が必要です。", "error")
        return redirect(url_for("designer.billing"))

    designers = Designer.query.order_by(Designer.name).all() if current_user.role == "admin" else None
    if request.method == "POST":
        stype = request.form.get("schedule_type", "weekly")
        client = Client(
            name=request.form["name"],
            platform_type=request.form.get("platform_type", "wordpress"),
            client_email=request.form.get("client_email", ""),
            article_taste=request.form.get("article_taste", "standard"),
            target_word_count=int(request.form.get("target_word_count", 0) or 0),
            business_description=request.form.get("business_description", ""),
            target_audience=request.form.get("target_audience", ""),
            character_prompt=request.form.get("character_prompt", ""),
            email_format=request.form.get("email_format", "html"),
            client_status=request.form.get("client_status", "setting"),
            monthly_post_count=int(request.form.get("monthly_post_count", 4) or 4),
            monthly_fee=int(request.form.get("monthly_fee", 0) or 0),
            wp_endpoint=request.form.get("wp_endpoint", ""),
            wp_username=request.form.get("wp_username", ""),
            wp_app_password=encrypt_field(request.form.get("wp_app_password", "")),
            ig_business_account_id=request.form.get("ig_business_account_id", ""),
            ig_access_token=encrypt_field(request.form.get("ig_access_token", "")),
            ig_hashtags=request.form.get("ig_hashtags", ""),
            threads_user_id=request.form.get("threads_user_id", ""),
            threads_access_token=encrypt_field(request.form.get("threads_access_token", "")),
            threads_fixed_url=request.form.get("threads_fixed_url", ""),
            themes=request.form.get("themes", ""),
            custom_url=request.form.get("custom_url", ""),
            schedule_type=stype,
            schedule_days_of_week=",".join(request.form.getlist("schedule_days_of_week")) or "0",
            schedule_days_of_month=",".join(request.form.getlist("schedule_days_of_month")) or "1",
            default_post_time=request.form.get("default_post_time") or None,
        )
        db.session.add(client)
        db.session.flush()

        # 管理者は担当デザイナーを選択可、デザイナーは自分に自動アサイン
        if current_user.role == "admin":
            raw_id = request.form.get("designer_id", "").strip()
            assign_id = int(raw_id) if raw_id else current_user.id
        else:
            assign_id = current_user.id
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
        client.client_email       = request.form.get("client_email", "")
        client.business_description = request.form.get("business_description", "")
        client.article_taste      = request.form.get("article_taste", "standard")
        client.target_word_count  = int(request.form.get("target_word_count", 0) or 0)
        client.target_audience    = request.form.get("target_audience", "")
        client.character_prompt   = request.form.get("character_prompt", "")
        client.email_format       = request.form.get("email_format", "html")
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
        client.threads_user_id = request.form.get("threads_user_id", "")
        new_threads_token = request.form.get("threads_access_token", "")
        if new_threads_token:
            client.threads_access_token = encrypt_field(new_threads_token)
        client.threads_fixed_url = request.form.get("threads_fixed_url", "")
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
        "threads_access_token": decrypt_field(client.threads_access_token or ""),
    }
    return render_template("designer/clients/form.html", client=client, form_data=form_data)


@designer_bp.route("/clients/<int:client_id>/fetch-wp-posts", methods=["POST"])
@login_required
def client_fetch_wp_posts(client_id: int):
    """WordPressから既存記事を取得してキャッシュする（文体参照用）。"""
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    import json, requests as _req
    from wp_client import _auth_header
    endpoint = (client.wp_endpoint or "").rstrip("/")
    if not endpoint:
        flash("WordPressエンドポイントが設定されていません", "error")
        return redirect(url_for("designer.client_edit", client_id=client_id))
    try:
        username = client.wp_username or ""
        password = decrypt_field(client.wp_app_password)
        res = _req.get(
            f"{endpoint}/wp-json/wp/v2/posts",
            headers=_auth_header(username, password),
            params={"per_page": 5, "orderby": "date", "order": "desc",
                    "_fields": "id,title,content,date"},
            timeout=15,
        )
        if res.status_code != 200:
            flash(f"WP記事取得失敗 (HTTP {res.status_code})", "error")
            return redirect(url_for("designer.client_edit", client_id=client_id))
        posts = []
        for p in res.json():
            import re as _re
            content_text = _re.sub(r"<[^>]+>", "", p.get("content", {}).get("rendered", ""))[:800]
            posts.append({
                "title": p.get("title", {}).get("rendered", ""),
                "content": content_text,
            })
        client.wp_sample_posts_json = json.dumps(posts, ensure_ascii=False)
        db.session.commit()
        flash(f"WordPress記事を {len(posts)} 件取得しました", "success")
    except Exception as e:
        flash(f"記事取得エラー: {e}", "error")
    return redirect(url_for("designer.client_edit", client_id=client_id))


@designer_bp.route("/clients/<int:client_id>/upload-hp-template", methods=["POST"])
@login_required
def client_upload_hp_template(client_id: int):
    """独自HPのデザインテンプレートファイルをアップロードしてAIが設計指示を抽出する。"""
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    import os
    file = request.files.get("template_file")
    if not file or file.filename == "":
        flash("ファイルが選択されていません", "error")
        return redirect(url_for("designer.client_edit", client_id=client_id))
    allowed_exts = {".html", ".htm", ".css"}
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed_exts:
        flash("HTML または CSS ファイルのみアップロードできます", "error")
        return redirect(url_for("designer.client_edit", client_id=client_id))
    upload_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "uploads", "hp_templates")
    os.makedirs(upload_dir, exist_ok=True)
    save_path = os.path.join(upload_dir, f"client_{client_id}_template{ext}")
    file.save(save_path)
    client.hp_template_path = save_path
    # AI でデザイン指示を抽出
    try:
        with open(save_path, "r", encoding="utf-8", errors="ignore") as f:
            template_content = f.read()[:8000]
        import anthropic as _anthropic
        from config import Config
        ai = _anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
        msg = ai.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            messages=[{
                "role": "user",
                "content": f"""以下のHTMLまたはCSSを解析し、このサイトのデザイン・文体・構成に合わせた記事生成のための指示を日本語で200字以内にまとめてください。

コードの説明ではなく「記事ライターへの指示」として書いてください。
色調・フォント・レイアウトの印象、想定読者層、推奨される文体・トーン、見出し構造などを含めること。

```
{template_content}
```""",
            }],
        )
        client.hp_design_prompt = msg.content[0].text.strip()
        flash("テンプレートを解析してデザイン指示を生成しました", "success")
    except Exception as e:
        flash(f"テンプレートを保存しました（AI解析エラー: {e}）", "warning")
    db.session.commit()
    return redirect(url_for("designer.client_edit", client_id=client_id))


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
