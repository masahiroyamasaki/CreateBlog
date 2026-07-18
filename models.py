"""models.py — SQLAlchemy モデル定義（MySQL）"""
from datetime import datetime, timezone, timedelta
from flask_sqlalchemy import SQLAlchemy

_JST = timezone(timedelta(hours=9))
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


# ─── デザイナー（ログインユーザー）────────────────────────────────────────

class Designer(UserMixin, db.Model):
    __tablename__ = "designers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.Enum("designer", "admin"), default="designer", nullable=False)
    bank_account = db.Column(db.String(255), default="")   # 振込口座
    region = db.Column(db.String(100), default="")         # 活動地域
    job_type = db.Column(db.String(100), default="")       # 職種
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login_at = db.Column(db.DateTime)

    assignments = db.relationship("DesignerClient", back_populates="designer", lazy="dynamic")

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def client_ids(self) -> list[int]:
        return [a.client_id for a in self.assignments]

    def can_access_client(self, client_id: int) -> bool:
        if self.role == "admin":
            return True
        return client_id in self.client_ids


# ─── 契約企業マスタ ───────────────────────────────────────────────────────

class Client(db.Model):
    __tablename__ = "clients"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    wp_endpoint = db.Column(db.String(255), default="")
    wp_username = db.Column(db.String(255), default="")
    wp_app_password = db.Column(db.String(255), default="")   # 暗号化保存
    platform_type = db.Column(db.String(50), default="wordpress_instagram")  # wordpress / instagram / wordpress_instagram / custom_hp
    ig_business_account_id = db.Column(db.String(255), default="")
    ig_access_token = db.Column(db.Text, default="")          # 暗号化保存
    ig_token_expires_at = db.Column(db.DateTime)
    ig_hashtags = db.Column(db.Text)                           # 固定ハッシュタグ（改行区切り）
    themes = db.Column(db.Text)                               # 記事テーマ（改行区切り）
    custom_url = db.Column(db.String(255), default="")        # 独自HP URL
    client_status = db.Column(db.String(20), default="active")  # active/pending/setting
    monthly_post_count = db.Column(db.Integer, default=4)       # 月間契約投稿数
    monthly_fee = db.Column(db.Integer, default=0)              # 月額料金（円）
    schedule_type = db.Column(db.String(10), default="weekly")  # weekly / monthly
    schedule_day_of_week = db.Column(db.Integer, default=0)     # 旧: 単一曜日（後方互換）
    schedule_day_of_month = db.Column(db.Integer, default=1)    # 旧: 単一日付（後方互換）
    schedule_days_of_week = db.Column(db.Text, default="0")     # カンマ区切り "0,2,4"
    schedule_days_of_month = db.Column(db.Text, default="1")    # カンマ区切り "1,8,15,22"
    default_post_time = db.Column(db.Time)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    posts = db.relationship("Post", back_populates="client", lazy="dynamic")
    topics = db.relationship("TopicQueue", back_populates="client", lazy="dynamic")
    assignments = db.relationship("DesignerClient", back_populates="client", lazy="dynamic")

    @property
    def pending_topic_count(self) -> int:
        return self.topics.filter_by(status="pending").count()

    @property
    def needs_topic_replenishment(self) -> bool:
        return self.pending_topic_count < 5


# ─── デザイナー ↔ 契約企業 中間テーブル ────────────────────────────────────

class DesignerClient(db.Model):
    __tablename__ = "designer_clients"

    id = db.Column(db.Integer, primary_key=True)
    designer_id = db.Column(db.Integer, db.ForeignKey("designers.id"), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    assigned_at = db.Column(db.DateTime, default=datetime.utcnow)

    designer = db.relationship("Designer", back_populates="assignments")
    client = db.relationship("Client", back_populates="assignments")


# ─── 投稿コンテンツ ───────────────────────────────────────────────────────

class Post(db.Model):
    __tablename__ = "posts_ig"  # 既存の posts(SQLite) と衝突しないよう posts_ig に

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    created_by_designer_id = db.Column(db.Integer, db.ForeignKey("designers.id"))
    title = db.Column(db.String(255), default="")
    outline = db.Column(db.Text, default="")
    body_html = db.Column(db.Text, default="")
    ig_caption = db.Column(db.Text, default="")
    ig_hashtags_post = db.Column(db.Text, default="")  # 投稿固有ハッシュタグ
    status = db.Column(
        db.Enum("creating", "draft", "approved", "scheduled", "posted", "failed"),
        default="draft", nullable=False,
    )
    publish_mode = db.Column(db.Enum("immediate", "scheduled"))
    scheduled_at = db.Column(db.DateTime)
    wp_post_id = db.Column(db.String(100), default="")
    wp_post_url = db.Column(db.String(255), default="")
    ig_media_id = db.Column(db.String(100), default="")
    error_message = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    posted_at = db.Column(db.DateTime)

    client = db.relationship("Client", back_populates="posts")
    designer = db.relationship("Designer", foreign_keys=[created_by_designer_id])
    images = db.relationship(
        "PostImage", back_populates="post", lazy="dynamic",
        order_by="PostImage.sort_order", cascade="all, delete-orphan",
    )
    topic = db.relationship("TopicQueue", back_populates="generated_post", uselist=False)

    @property
    def image_list(self) -> list:
        return self.images.order_by("sort_order").all()

    @property
    def status_label(self) -> str:
        return {
            "creating": "作成中",
            "draft": "下書き",
            "approved": "承認済み",
            "scheduled": "予約中",
            "posted": "投稿済み",
            "failed": "失敗",
        }.get(self.status, self.status)

    @property
    def status_color(self) -> str:
        return {
            "creating": "purple",
            "draft": "gray",
            "approved": "blue",
            "scheduled": "orange",
            "posted": "green",
            "failed": "red",
        }.get(self.status, "gray")

    @property
    def is_overdue(self) -> bool:
        now_jst = datetime.now(_JST).replace(tzinfo=None)
        return (
            self.status == "scheduled"
            and self.scheduled_at is not None
            and self.scheduled_at < now_jst
        )


# ─── 投稿画像（カルーセル対応）─────────────────────────────────────────────

class PostImage(db.Model):
    __tablename__ = "post_images"

    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("posts_ig.id"), nullable=False)
    image_url = db.Column(db.String(500), default="")
    sort_order = db.Column(db.Integer, default=1)
    ig_container_id = db.Column(db.String(100), default="")

    post = db.relationship("Post", back_populates="images")


# ─── 記事ネタキュー ───────────────────────────────────────────────────────

class TopicQueue(db.Model):
    __tablename__ = "topic_queue"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    title = db.Column(db.String(255), default="")
    outline = db.Column(db.Text, default="")
    sort_order = db.Column(db.Integer, default=0)
    status = db.Column(db.Enum("pending", "processing", "generated"), default="pending", nullable=False)
    created_by = db.Column(db.Enum("designer", "ai_auto"), default="designer", nullable=False)
    created_by_designer_id = db.Column(db.Integer, db.ForeignKey("designers.id"))
    generated_post_id = db.Column(db.Integer, db.ForeignKey("posts_ig.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    client = db.relationship("Client", back_populates="topics")
    designer = db.relationship("Designer", foreign_keys=[created_by_designer_id])
    generated_post = db.relationship("Post", back_populates="topic", foreign_keys=[generated_post_id])
