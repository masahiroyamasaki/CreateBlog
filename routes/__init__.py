"""routes/__init__.py — Blueprint 登録"""
from flask import Blueprint

designer_bp = Blueprint("designer", __name__, url_prefix="/designer")

from routes import auth_routes, client_routes, topic_routes, post_routes, admin_routes  # noqa: E402, F401
