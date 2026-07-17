"""config.py — アプリケーション設定"""
import os
import base64
import hashlib
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "change-this-in-production-please")

    # MySQL (ConoHa WING)
    MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
    MYSQL_PORT = int(os.getenv("MYSQL_PORT", 3306))
    MYSQL_USER = os.getenv("MYSQL_USER", "root")
    MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
    MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "blog_ig_system")

    SQLALCHEMY_DATABASE_URI = (
        f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}"
        f"@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}?charset=utf8mb4"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # アップロード上限 50MB
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_recycle": 280,
        "pool_pre_ping": True,
    }

    # Anthropic
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

    # Instagram Graph API
    IG_API_VERSION = os.getenv("IG_API_VERSION", "v20.0")
    IG_API_BASE = f"https://graph.facebook.com/{os.getenv('IG_API_VERSION', 'v20.0')}"


def get_fernet():
    """SECRET_KEY から Fernet キーを生成する（認証情報の暗号化用）"""
    from cryptography.fernet import Fernet
    raw = Config.SECRET_KEY.encode()
    key = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
    return Fernet(key)


def encrypt_field(value: str) -> str:
    if not value:
        return value
    return get_fernet().encrypt(value.encode()).decode()


def decrypt_field(value: str) -> str:
    if not value:
        return value
    try:
        return get_fernet().decrypt(value.encode()).decode()
    except Exception:
        return value
