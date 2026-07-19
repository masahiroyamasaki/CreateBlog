"""auth.py — Flask-Login 設定"""
from flask_login import LoginManager
from models import Designer

login_manager = LoginManager()
login_manager.login_view = "designer.login"
login_manager.login_message = "ログインが必要です"
login_manager.login_message_category = "warning"


@login_manager.user_loader
def load_user(user_id: str):
    return Designer.query.get(int(user_id))
