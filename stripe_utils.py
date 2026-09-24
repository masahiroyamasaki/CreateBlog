"""stripe_utils.py — Stripe API ヘルパー（カード保存 + オフセッション課金）"""
import os
import logging

logger = logging.getLogger(__name__)


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
    if getattr(designer, "billing_exempt", False):
        return True
    return designer.subscription_status == "active"


def is_client_operational(client, designer) -> bool:
    """クライアントの操作（生成・投稿）が許可されているか判定する。
    管理者・課金免除・テスト企業は常に許可。
    デザイナーは「利用規約同意済み」かつ「subscription_status == active」かつ「client_status == active」の場合のみ許可。
    """
    if designer.role == "admin":
        return True
    if getattr(designer, "billing_exempt", False):
        return client.client_status == "active"
    if client.client_status == "test":
        return True
    if not designer.has_agreed_terms:
        return False
    if designer.subscription_status != "active":
        return False
    return client.client_status == "active"


def create_setup_session(designer, success_url: str, cancel_url: str):
    """カード情報のみ保存する Checkout Session（mode=setup）を作成して URL を返す。失敗時は None。"""
    stripe = get_stripe()
    if not stripe:
        return None

    # 既存 Customer があれば再利用、なければ新規作成して即 DB 保存
    customer_id = designer.stripe_customer_id or ""
    if not customer_id:
        try:
            customer = stripe.Customer.create(
                email=designer.email,
                name=designer.name,
                metadata={"designer_id": str(designer.id)},
            )
            customer_id = customer.id
            from models import db, Designer as _Designer
            d = _Designer.query.get(designer.id)
            if d:
                d.stripe_customer_id = customer_id
                db.session.commit()
        except Exception as e:
            logger.error(f"[stripe] Customer作成エラー: {e}")
            return None

    try:
        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="setup",
            currency="jpy",
            success_url=success_url + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=cancel_url,
            metadata={"designer_id": str(designer.id)},
        )
        return session.url
    except Exception as e:
        logger.error(f"[stripe] Setup Session作成エラー: {e}")
        return None


def charge_designer(designer, amount_jpy: int, description: str) -> dict:
    """off_session=True で PaymentIntent を即時課金する。
    戻り値: {"success": bool, "payment_intent_id": str, "reason": str}
    """
    stripe = get_stripe()
    if not stripe:
        return {"success": False, "reason": "Stripe 未設定"}

    pm_id = designer.stripe_payment_method_id or ""
    customer_id = designer.stripe_customer_id or ""
    if not pm_id or not customer_id:
        return {"success": False, "reason": "支払方法が未登録"}
    if amount_jpy <= 0:
        return {"success": False, "reason": "課金額が0円以下"}

    try:
        intent = stripe.PaymentIntent.create(
            amount=amount_jpy,
            currency="jpy",
            customer=customer_id,
            payment_method=pm_id,
            off_session=True,
            confirm=True,
            description=description,
            metadata={"designer_id": str(designer.id)},
        )
        return {"success": True, "payment_intent_id": intent.id, "reason": ""}
    except stripe.error.CardError as e:
        return {"success": False, "payment_intent_id": "", "reason": f"カードエラー: {e.user_message}"}
    except Exception as e:
        return {"success": False, "payment_intent_id": "", "reason": str(e)}


def detach_payment_method(payment_method_id: str) -> bool:
    """PaymentMethod を顧客から切り離す（カード登録解除）。"""
    stripe = get_stripe()
    if not stripe or not payment_method_id:
        return False
    try:
        stripe.PaymentMethod.detach(payment_method_id)
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
