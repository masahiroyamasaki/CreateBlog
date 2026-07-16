"""routes/auth_routes.py — ログイン / ログアウト"""
from datetime import datetime
from flask import render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from models import db, Designer
from routes import designer_bp


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


@designer_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("designer.login"))
