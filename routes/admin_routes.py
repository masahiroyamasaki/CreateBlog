"""routes/admin_routes.py — 管理者専用画面"""
from flask import render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from models import db, Designer, Client, Post, DesignerClient
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
        designer.name        = request.form.get("name", designer.name).strip()
        designer.region      = request.form.get("region", "").strip()
        designer.job_type    = request.form.get("job_type", "").strip()
        designer.bank_account = request.form.get("bank_account", "").strip()
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
