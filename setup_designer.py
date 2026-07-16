"""setup_designer.py — 初回セットアップ: 管理者アカウントと DB テーブルを作成する

実行:
  python setup_designer.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask
from config import Config
from models import db, Designer


def main():
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)

    with app.app_context():
        db.create_all()
        print("✓ テーブルを作成しました")

        email = input("管理者メールアドレス: ").strip()
        if not email:
            print("キャンセルしました")
            return

        name = input("管理者名: ").strip() or "Admin"
        import getpass
        password = getpass.getpass("パスワード: ")

        existing = Designer.query.filter_by(email=email).first()
        if existing:
            print(f"⚠️  {email} は既に登録済みです")
            return

        admin = Designer(name=name, email=email, role="admin")
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()
        print(f"✓ 管理者アカウントを作成しました: {name} <{email}>")
        print("  http://localhost:5000/designer/login からログインできます")


if __name__ == "__main__":
    main()
