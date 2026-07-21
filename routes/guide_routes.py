"""routes/guide_routes.py — 使い方ガイドページ"""
from flask import render_template
from flask_login import login_required
from routes import designer_bp


@designer_bp.route("/guide")
@login_required
def guide():
    return render_template("designer/guide.html")
