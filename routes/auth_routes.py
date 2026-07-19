"""routes/auth_routes.py — ログイン / 新規登録 / ログアウト"""
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


@designer_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("designer.clients"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")
        business_name = request.form.get("business_name", "").strip()
        bank_account = request.form.get("bank_account", "").strip()
        region = request.form.get("region", "").strip()
        job_type = request.form.get("job_type", "").strip()

        if not email or not name or not password:
            flash("メールアドレス・氏名・パスワードは必須です", "error")
        elif password != password2:
            flash("パスワードが一致しません", "error")
        elif len(password) < 8:
            flash("パスワードは8文字以上にしてください", "error")
        elif Designer.query.filter_by(email=email).first():
            flash("このメールアドレスはすでに登録されています", "error")
        else:
            designer = Designer(
                name=name,
                email=email,
                business_name=business_name,
                bank_account=bank_account,
                region=region,
                job_type=job_type,
                role="designer",
            )
            designer.set_password(password)
            db.session.add(designer)
            db.session.commit()
            flash("登録が完了しました。ログインしてください。", "success")
            return redirect(url_for("designer.login"))

    return render_template("designer/register.html")


@designer_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("designer.login"))
