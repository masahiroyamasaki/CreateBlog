"""stripe_utils.py — Stripe API ヘルパー"""
import os


def stripe_enabled() -> bool:
    return bool(os.getenv("STRIPE_SECRET_KEY"))


def get_stripe():
    """stripe モジュールを返す。キー未設定の場合は None。"""
    if not stripe_enabled():
        return None
    import stripe as _stripe
    _stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
    return _stripe


def can_add_client(designer) -> bool:
    """デザイナーが企業を追加できるか判定する。"""
    if designer.role == "admin":
        return True
    return designer.subscription_status == "active"


def is_client_operational(client, designer) -> bool:
    """クライアントの操作（生成・投稿）が許可されているか判定する。
    管理者・テスト企業は常に許可。
    デザイナーは「利用規約同意済み」かつ「subscription_status == active」かつ「client_status == active」の場合のみ許可。
    """
    if designer.role == "admin":
        return True
    if client.client_status == "test":
        return True
    if not designer.has_agreed_terms:
        return False
    if designer.subscription_status != "active":
        return False
    return client.client_status == "active"


def create_checkout_session(designer, success_url: str, cancel_url: str):
    """Stripe Checkout セッションを作成し URL を返す。失敗時は None。"""
    stripe = get_stripe()
    if not stripe:
        return None

    price_id = os.getenv("STRIPE_PRICE_ID", "")
    if not price_id:
        return None

    # 既存の Stripe 顧客 ID があれば再利用
    customer_kwargs = {}
    if designer.stripe_customer_id:
        customer_kwargs["customer"] = designer.stripe_customer_id
    else:
        customer_kwargs["customer_email"] = designer.email

    session = stripe.checkout.Session.create(
        **customer_kwargs,
        payment_method_types=["card"],
        line_items=[{"price": price_id, "quantity": 1}],
        mode="subscription",
        success_url=success_url + "?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=cancel_url,
        metadata={"designer_id": str(designer.id)},
    )
    return session.url


def add_client_subscription_item(designer, stripe_price_id: str) -> str:
    """デザイナーのサブスクリプションに企業プランのアイテムを追加し、item IDを返す。
    サブスクリプションがなければ新規作成する。失敗時は空文字。
    """
    stripe = get_stripe()
    if not stripe or not stripe_price_id:
        return ""
    try:
        sub_id = designer.stripe_subscription_id or ""
        if sub_id:
            item = stripe.SubscriptionItem.create(
                subscription=sub_id,
                price=stripe_price_id,
            )
        else:
            # 初回企業追加時にサブスクリプションを作成
            customer_kwargs = {}
            if designer.stripe_customer_id:
                customer_kwargs["customer"] = designer.stripe_customer_id
            else:
                customer_kwargs["customer_email"] = designer.email
            sub = stripe.Subscription.create(
                **customer_kwargs,
                items=[{"price": stripe_price_id}],
                payment_behavior="default_incomplete",
                expand=["latest_invoice.payment_intent"],
            )
            from models import db, Designer as _Designer
            d = _Designer.query.get(designer.id)
            if d:
                d.stripe_subscription_id = sub.id
                if not d.stripe_customer_id:
                    d.stripe_customer_id = sub.customer
                d.subscription_status = "active"
                db.session.commit()
            item = sub["items"]["data"][0]
        return item.id
    except Exception:
        return ""


def remove_client_subscription_item(subscription_item_id: str) -> bool:
    """サブスクリプションアイテムを削除する。"""
    stripe = get_stripe()
    if not stripe or not subscription_item_id:
        return False
    try:
        stripe.SubscriptionItem.delete(subscription_item_id)
        return True
    except Exception:
        return False


def cancel_subscription(subscription_id: str) -> bool:
    """サブスクリプションをキャンセルする。"""
    stripe = get_stripe()
    if not stripe or not subscription_id:
        return False
    try:
        stripe.Subscription.cancel(subscription_id)
        return True
    except Exception:
        return False


def handle_webhook(payload: bytes, sig_header: str):
    """Webhook ペイロードを検証してイベントを返す。署名不一致は None。"""
    stripe = get_stripe()
    if not stripe:
        return None
    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    if not secret:
        return None
    try:
        return stripe.Webhook.construct_event(payload, sig_header, secret)
    except Exception:
        return None
