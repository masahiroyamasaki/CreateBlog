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
    business_name = db.Column(db.String(255), default="")  # 屋号・法人名
    bank_name          = db.Column(db.String(100), default="")  # 銀行名
    bank_branch        = db.Column(db.String(100), default="")  # 支店名
    bank_account_type  = db.Column(db.String(20),  default="")  # 口座種別（普通/当座）
    bank_account_number= db.Column(db.String(30),  default="")  # 口座番号
    bank_account_holder= db.Column(db.String(100), default="")  # 口座名義（カナ）
    region = db.Column(db.String(100), default="")         # 活動地域
    job_type = db.Column(db.String(100), default="")       # 職種
    stripe_customer_id = db.Column(db.String(255), default="")      # Stripe 顧客 ID
    stripe_subscription_id = db.Column(db.String(255), default="")  # Stripe サブスクリプション ID
    subscription_status = db.Column(db.String(20), default="free")  # free / active / past_due / cancelled
    postal_code = db.Column(db.String(10), default="")   # 郵便番号
    address = db.Column(db.Text, default="")             # 住所
    phone = db.Column(db.String(20), default="")         # 電話番号
    invoice_number = db.Column(db.String(20), default="") # インボイス登録番号（T始まり）
    referred_by_id = db.Column(db.Integer, nullable=True)  # 紹介者デザイナーID
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

    @property
    def has_agreed_terms(self) -> bool:
        """利用規約への同意済みか（DesignerAgreement レコードが存在するか）"""
        return self.agreements.count() > 0


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
    threads_user_id = db.Column(db.String(255), default="")   # Threads ユーザー ID
    threads_access_token = db.Column(db.Text)                 # 暗号化保存（DEFAULT NULL で TEXT 制約を回避）
    threads_fixed_url = db.Column(db.String(500), default="") # Threads 投稿末尾に付ける固定 URL
    threads_enabled = db.Column(db.Boolean, default=True)     # Threads 投稿オン/オフ
    business_description = db.Column(db.Text)                  # 事業内容・サービス概要
    themes = db.Column(db.Text)                               # 記事テーマ（改行区切り）
    custom_url = db.Column(db.String(255), default="")        # 独自HP URL
    delivery_method = db.Column(db.String(20), default="email")  # 配信方法: email / webhook
    webhook_url = db.Column(db.String(500), default="")          # Webhook 送信先 URL
    client_email = db.Column(db.String(255), default="")       # メール送信先（email_onlyプラン用）
    wp_sample_posts_json = db.Column(db.Text)                 # WP既存記事キャッシュ（JSON）
    hp_template_path = db.Column(db.String(500), default="") # 独自HPテンプレートファイルパス
    hp_design_prompt = db.Column(db.Text)                     # HPデザイン指示（AI生成）
    article_taste      = db.Column(db.String(30), default="standard")  # 記事テイスト
    target_word_count  = db.Column(db.Integer, default=0)              # 目標文字数（0=自動）
    target_audience    = db.Column(db.Text)                            # ターゲット設定
    character_prompt   = db.Column(db.Text)                            # キャラ・ペルソナ設定
    email_format       = db.Column(db.String(10), default="html")      # email_only出力形式: html/text
    client_status = db.Column(db.String(20), default="active")  # active/pending/setting
    monthly_post_count = db.Column(db.Integer, default=4)       # 月間契約投稿数
    monthly_fee = db.Column(db.Integer, default=0)              # 月額料金（円）
    schedule_type = db.Column(db.String(10), default="weekly")  # weekly / monthly
    schedule_day_of_week = db.Column(db.Integer, default=0)     # 旧: 単一曜日（後方互換）
    schedule_day_of_month = db.Column(db.Integer, default=1)    # 旧: 単一日付（後方互換）
    schedule_days_of_week = db.Column(db.String(255), default="0")   # カンマ区切り "0,2,4"
    schedule_days_of_month = db.Column(db.String(255), default="1") # カンマ区切り "1,8,15,22"
    default_post_time = db.Column(db.Time)
    last_idea_theme_idx = db.Column(db.Integer, default=0)    # テーマ回転用インデックス
    image_gen_enabled = db.Column(db.Boolean, default=False)           # AI画像生成オプション ON/OFF
    image_taste = db.Column(db.String(30), default="business_clean")   # business_clean / photo_real / illustration_pop / minimal / japanese_calm / colorful_energy
    image_balance = db.Column(db.String(30), default="balanced")        # balanced / image_focus / text_focus
    image_aspect_ratio = db.Column(db.String(10), default="1:1")       # 1:1 / 4:5 / 16:9
    image_base_prompt = db.Column(db.Text, default="")                 # 全画像共通ベースプロンプト（日本語可）
    sample_image_1_path = db.Column(db.String(500), default="")       # サンプル画像1（staticフォルダ相対パス）
    sample_image_2_path = db.Column(db.String(500), default="")       # サンプル画像2
    sample_image_3_path = db.Column(db.String(500), default="")       # サンプル画像3
    image_count_per_post = db.Column(db.Integer, default=1)           # 記事あたり生成画像枚数（1〜4）
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
    ig_hashtags_post = db.Column(db.Text)               # 投稿固有ハッシュタグ
    threads_url = db.Column(db.String(500), default="") # Threads投稿用URL（投稿個別）
    status = db.Column(
        db.Enum("creating", "draft", "approved", "scheduled", "posted", "failed"),
        default="draft", nullable=False,
    )
    publish_mode = db.Column(db.Enum("immediate", "scheduled"))
    scheduled_at = db.Column(db.DateTime)
    wp_post_id = db.Column(db.String(100), default="")
    wp_post_url = db.Column(db.String(255), default="")
    ig_media_id = db.Column(db.String(100), default="")
    threads_media_id = db.Column(db.String(100), default="")  # Threads 投稿済みメディア ID
    error_message = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    posted_at = db.Column(db.DateTime)
    image_edit_count = db.Column(db.Integer, default=0)

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
    image_slots = db.Column(db.Text, default="")   # 画像構成 JSON（最大5枚）
    sort_order = db.Column(db.Integer, default=0)
    status = db.Column(db.Enum("pending", "processing", "generated"), default="pending", nullable=False)
    created_by = db.Column(db.Enum("designer", "ai_auto"), default="designer", nullable=False)
    created_by_designer_id = db.Column(db.Integer, db.ForeignKey("designers.id"))
    generated_post_id = db.Column(db.Integer, db.ForeignKey("posts_ig.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    client = db.relationship("Client", back_populates="topics")
    designer = db.relationship("Designer", foreign_keys=[created_by_designer_id])
    generated_post = db.relationship("Post", back_populates="topic", foreign_keys=[generated_post_id])


# ─── 料金プラン ────────────────────────────────────────────────────────────────

class PricingPlan(db.Model):
    __tablename__ = "pricing_plans"

    id = db.Column(db.Integer, primary_key=True)
    platform_type = db.Column(db.String(50), nullable=False)
    monthly_posts = db.Column(db.Integer, nullable=False)
    monthly_fee = db.Column(db.Integer, nullable=False)
    sort_order = db.Column(db.Integer, default=0)


# ─── 請求書 ───────────────────────────────────────────────────────────────────

class Invoice(db.Model):
    __tablename__ = "invoices"

    id = db.Column(db.Integer, primary_key=True)
    designer_id = db.Column(db.Integer, db.ForeignKey("designers.id"), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    month = db.Column(db.Integer, nullable=False)
    total_amount = db.Column(db.Integer, default=0)
    pdf_path = db.Column(db.String(500), default="")
    status = db.Column(
        db.Enum("draft", "sent", "issued", "paid"),
        default="issued", nullable=False,
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    sent_at = db.Column(db.DateTime)
    is_trial = db.Column(db.Boolean, nullable=True)  # True = お試し期間の¥0請求書、NULL/False = 通常請求
    # 割引
    discount_type   = db.Column(db.String(20), default="")       # "amount" | "percent" | ""
    discount_value  = db.Column(db.Float, default=0.0)           # 割引額 or 割引率(%)
    discount_target = db.Column(db.String(20), default="pretax") # "pretax" | "posttax"

    designer = db.relationship("Designer", backref="invoices")
    items = db.relationship("InvoiceItem", back_populates="invoice",
                            cascade="all, delete-orphan", lazy="dynamic")

    @property
    def invoice_number(self) -> str:
        return f"MBS-{self.year}{self.month:02d}-{self.designer_id:04d}"

    @property
    def status_label(self) -> str:
        return {"draft": "下書き", "sent": "送付済み", "issued": "発行済み", "paid": "入金済み"}.get(self.status, self.status)

    @property
    def status_badge(self) -> str:
        return {"draft": "badge-gray", "sent": "badge-blue", "issued": "badge-orange", "paid": "badge-green"}.get(self.status, "badge-gray")

    @property
    def discount_amount(self) -> int:
        """実際の割引額（円）。"""
        if not self.discount_type or not (self.discount_value or 0):
            return 0
        if self.discount_type == "amount":
            return int(self.discount_value or 0)
        # percent
        base = (self.total_amount + int(self.total_amount * 0.1)
                if self.discount_target == "posttax"
                else self.total_amount)
        return int(base * (self.discount_value or 0) / 100)

    @property
    def tax_amount(self) -> int:
        if self.discount_target == "pretax" and self.discount_amount > 0:
            taxable = max(0, self.total_amount - self.discount_amount)
        else:
            taxable = self.total_amount
        return int(taxable * 0.1)

    @property
    def total_with_tax(self) -> int:
        if self.discount_target == "pretax":
            taxable = max(0, self.total_amount - self.discount_amount)
            return max(0, taxable + int(taxable * 0.1))
        # posttax
        return max(0, self.total_amount + int(self.total_amount * 0.1) - self.discount_amount)


class InvoiceItem(db.Model):
    __tablename__ = "invoice_items"

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id"), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"))
    client_name = db.Column(db.String(255), default="")
    description = db.Column(db.String(500), default="")
    amount = db.Column(db.Integer, default=0)

    invoice = db.relationship("Invoice", back_populates="items")


# ─── 企業契約・請求マスタ ─────────────────────────────────────────────────────

class ClientSubscription(db.Model):
    """企業ごとの契約・請求マスタ。請求書生成の唯一の根拠となる。"""
    __tablename__ = "client_subscriptions"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, unique=True)
    designer_id = db.Column(db.Integer, db.ForeignKey("designers.id"), nullable=False)
    plan_name = db.Column(db.String(255), default="")   # 例: "WordPress 4件/月"
    amount = db.Column(db.Integer, default=0)            # 月額料金（円）
    is_trial = db.Column(db.Boolean, default=True)       # True = 無料お試し期間中
    contract_date = db.Column(db.DateTime, nullable=False)  # 契約日（登録日）
    billing_date = db.Column(db.DateTime)                # 請求開始日（先払い第1回請求日）
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    client = db.relationship("Client", backref=db.backref("subscription", uselist=False))
    designer = db.relationship("Designer", backref=db.backref("subscriptions", lazy="dynamic"))


# ─── 契約書テンプレート ───────────────────────────────────────────────────────

class ContractTemplate(db.Model):
    __tablename__ = "contract_templates"

    id = db.Column(db.Integer, primary_key=True)
    version = db.Column(db.String(20), nullable=False, unique=True)
    body_text = db.Column(db.Text, nullable=False)
    effective_date = db.Column(db.Date, nullable=False)
    is_current = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ─── デザイナー同意ログ（追記専用）──────────────────────────────────────────

class DesignerAgreement(db.Model):
    __tablename__ = "designer_agreements"

    id = db.Column(db.Integer, primary_key=True)
    designer_id = db.Column(db.Integer, db.ForeignKey("designers.id"), nullable=False)
    contract_version = db.Column(db.String(20), nullable=False)
    contract_hash = db.Column(db.String(64), nullable=False)   # SHA-256
    agreed_at = db.Column(db.DateTime, nullable=False)         # サーバー側タイムスタンプ
    checkbox_text = db.Column(db.Text, nullable=False)         # 表示されていた同意文言
    ip_address = db.Column(db.String(45), nullable=False)
    user_agent = db.Column(db.Text, default="")
    snapshot_json = db.Column(db.Text, default="")             # 入力情報スナップショット

    designer = db.relationship("Designer", backref=db.backref("agreements", lazy="dynamic"))
    pdf = db.relationship("AgreementPdf", back_populates="agreement", uselist=False)


# ─── 契約書 PDF ─────────────────────────────────────────────────────────────

class AgreementPdf(db.Model):
    __tablename__ = "agreement_pdfs"

    id = db.Column(db.Integer, primary_key=True)
    designer_agreement_id = db.Column(db.Integer, db.ForeignKey("designer_agreements.id"), nullable=False)
    pdf_path = db.Column(db.String(500), nullable=False)
    generated_at = db.Column(db.DateTime, default=datetime.utcnow)

    agreement = db.relationship("DesignerAgreement", back_populates="pdf")


# ─── Instagram 週次インサイト ────────────────────────────────────────────────

class WeeklyInsight(db.Model):
    __tablename__ = "weekly_insights"

    id           = db.Column(db.Integer, primary_key=True)
    client_id    = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    week_start   = db.Column(db.Date, nullable=False)          # 対象週の月曜日

    # 入力データ（JSON文字列）
    account_json      = db.Column(db.Text, default="{}")       # フォロワー数など
    this_week_json    = db.Column(db.Text, default="{}")       # 今週集計値
    last_week_json    = db.Column(db.Text, default="{}")       # 前週集計値
    four_week_avg_json= db.Column(db.Text, default="{}")       # 直近4週平均
    posts_json        = db.Column(db.Text, default="[]")       # 各投稿データ

    # Stage 1 キャッシュ
    stage1_json          = db.Column(db.Text, nullable=True)
    stage1_generated_at  = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    client  = db.relationship("Client", backref=db.backref("weekly_insights", lazy="dynamic"))
    reports = db.relationship("WeeklyReport", back_populates="insight",
                              cascade="all, delete-orphan")


class WeeklyReport(db.Model):
    __tablename__ = "weekly_reports"

    id                = db.Column(db.Integer, primary_key=True)
    weekly_insight_id = db.Column(db.Integer, db.ForeignKey("weekly_insights.id"), nullable=False)
    report_type       = db.Column(db.String(20), nullable=False)  # "designer" | "client_monthly"
    result_json       = db.Column(db.Text, nullable=True)         # デザイナー向け（JSON）
    result_text       = db.Column(db.Text, nullable=True)         # クライアント向け（プレーンテキスト）
    generated_at      = db.Column(db.DateTime, default=datetime.utcnow)

    insight = db.relationship("WeeklyInsight", back_populates="reports")
