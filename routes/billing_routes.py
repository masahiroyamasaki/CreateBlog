"""routes/billing_routes.py — Stripe 決済・プラン管理"""
from flask import render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user
from models import db, Designer
from routes import designer_bp
import stripe_utils


@designer_bp.route("/billing")
@login_required
def billing():
    return render_template(
        "designer/billing.html",
        stripe_enabled=stripe_utils.stripe_enabled(),
    )


@designer_bp.route("/billing/checkout", methods=["POST"])
@login_required
def billing_checkout():
    """Stripe Checkout セッションを作成してリダイレクト。"""
    base = request.host_url.rstrip("/")
    success_url = base + url_for("designer.billing")
    cancel_url  = base + url_for("designer.billing")

    checkout_url = stripe_utils.create_checkout_session(
        current_user, success_url, cancel_url
    )
    if not checkout_url:
        flash("決済ページの準備中です。しばらくお待ちください。", "error")
        return redirect(url_for("designer.billing"))
    return redirect(checkout_url, code=303)


@designer_bp.route("/billing/cancel-subscription", methods=["POST"])
@login_required
def billing_cancel():
    """サブスクリプションをキャンセルする。"""
    ok = stripe_utils.cancel_subscription(current_user.stripe_subscription_id or "")
    if ok:
        current_user.subscription_status = "cancelled"
        db.session.commit()
        flash("サブスクリプションをキャンセルしました。", "success")
    else:
        flash("キャンセルに失敗しました。サポートにお問い合わせください。", "error")
    return redirect(url_for("designer.billing"))


@designer_bp.route("/billing/webhook", methods=["POST"])
def billing_webhook():
    """Stripe Webhook エンドポイント（認証不要）。"""
    payload    = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")

    event = stripe_utils.handle_webhook(payload, sig_header)
    if event is None:
        return jsonify({"error": "Invalid signature"}), 400

    obj  = event["data"]["object"]
    etype = event["type"]

    # サブスクリプション作成・更新
    if etype in ("customer.subscription.created", "customer.subscription.updated"):
        _sync_subscription(obj)

    # サブスクリプション削除
    elif etype == "customer.subscription.deleted":
        _sync_subscription(obj, force_status="cancelled")

    # 支払い失敗
    elif etype == "invoice.payment_failed":
        customer_id = obj.get("customer")
        if customer_id:
            designer = Designer.query.filter_by(stripe_customer_id=customer_id).first()
            if designer:
                designer.subscription_status = "past_due"
                db.session.commit()

    # Checkout 完了 → customer_id を紐づけ
    elif etype == "checkout.session.completed":
        meta        = obj.get("metadata", {})
        designer_id = meta.get("designer_id")
        customer_id = obj.get("customer")
        if designer_id and customer_id:
            designer = Designer.query.get(int(designer_id))
            if designer and not designer.stripe_customer_id:
                designer.stripe_customer_id = customer_id
                db.session.commit()

    return jsonify({"received": True}), 200


def _sync_subscription(sub_obj, force_status: str | None = None):
    """Stripe Subscription オブジェクトから DB を更新する。"""
    customer_id = sub_obj.get("customer")
    if not customer_id:
        return
    designer = Designer.query.filter_by(stripe_customer_id=customer_id).first()
    if not designer:
        return

    designer.stripe_subscription_id = sub_obj.get("id", "")
    if force_status:
        designer.subscription_status = force_status
    else:
        status_map = {
            "active":   "active",
            "past_due": "past_due",
            "canceled": "cancelled",
            "unpaid":   "past_due",
            "trialing": "active",
        }
        designer.subscription_status = status_map.get(sub_obj.get("status", ""), "free")
    db.session.commit()
