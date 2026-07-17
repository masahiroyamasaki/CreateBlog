"""routes/client_routes.py — 契約企業の管理"""
from flask import render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from models import db, Client, DesignerClient, Post, TopicQueue
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


@designer_bp.route("/clients")
@login_required
def clients():
    client_list = _get_accessible_clients()
    # 1社のみ担当の場合は自動で詳細へ
    if len(client_list) == 1:
        return redirect(url_for("designer.client_detail", client_id=client_list[0].id))
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
    if request.method == "POST":
        client = Client(
            name=request.form["name"],
            wp_endpoint=request.form.get("wp_endpoint", ""),
            wp_username=request.form.get("wp_username", ""),
            wp_app_password=encrypt_field(request.form.get("wp_app_password", "")),
            ig_business_account_id=request.form.get("ig_business_account_id", ""),
            ig_access_token=encrypt_field(request.form.get("ig_access_token", "")),
            ig_hashtags=request.form.get("ig_hashtags", ""),
            themes=request.form.get("themes", ""),
            default_post_time=request.form.get("default_post_time") or None,
        )
        db.session.add(client)
        db.session.flush()

        # 作成者をアサイン
        db.session.add(DesignerClient(designer_id=current_user.id, client_id=client.id))
        db.session.commit()
        flash(f"「{client.name}」を追加しました", "success")
        return redirect(url_for("designer.client_detail", client_id=client.id))
    return render_template("designer/clients/form.html", client=None)


@designer_bp.route("/clients/<int:client_id>/edit", methods=["GET", "POST"])
@login_required
def client_edit(client_id: int):
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    if request.method == "POST":
        client.name = request.form["name"]
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
