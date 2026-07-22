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


def create_checkout_session(designer, success_url: str, cancel_url: str) -> str | None:
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


def handle_webhook(payload: bytes, sig_header: str) -> dict | None:
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
