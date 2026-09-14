"""routes/billing_routes.py — Stripe 決済・プラン管理"""
from flask import render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user
from models import db, Designer
from routes import designer_bp
import stripe_utils


@designer_bp.route("/billing")
@login_required
def billing():
    if request.args.get("session_id"):
        flash("カード情報を登録しました。企業の追加が可能になりました。", "success")
        return redirect(url_for("designer.billing"))
    return render_template(
        "designer/billing.html",
        stripe_enabled=stripe_utils.stripe_enabled(),
    )


@designer_bp.route("/billing/checkout", methods=["POST"])
@login_required
def billing_checkout():
    """Stripe Setup Session を作成してカード登録ページへリダイレクト。"""
    base = request.host_url.rstrip("/")
    success_url = base + url_for("designer.billing")
    cancel_url  = base + url_for("designer.billing")

    checkout_url = stripe_utils.create_setup_session(
        current_user, success_url, cancel_url
    )
    if not checkout_url:
        current_app.logger.error(
            f"[billing] Setup Session作成失敗 designer_id={current_user.id} "
            f"customer_id={current_user.stripe_customer_id!r} "
            f"stripe_enabled={stripe_utils.stripe_enabled()}"
        )
        flash("決済ページの準備中です。しばらくお待ちください。", "error")
        return redirect(url_for("designer.billing"))
    return redirect(checkout_url, code=303)


@designer_bp.route("/billing/cancel-subscription", methods=["POST"])
@login_required
def billing_cancel():
    """カード登録情報を解除してステータスを free に戻す。"""
    stripe_utils.detach_payment_method(current_user.stripe_payment_method_id or "")
    current_user.stripe_payment_method_id = ""
    current_user.subscription_status = "free"
    db.session.commit()
    flash("カード情報の登録を解除しました。", "success")
    return redirect(url_for("designer.billing"))


@designer_bp.route("/billing/webhook", methods=["POST"])
def billing_webhook():
    """Stripe Webhook エンドポイント（認証不要）。"""
    payload    = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")

    event = stripe_utils.handle_webhook(payload, sig_header)
    if event is None:
        return jsonify({"error": "Invalid signature"}), 400

    obj   = event["data"]["object"]
    etype = event["type"]

    # Setup Session 完了 → payment_method を取得して DB に保存
    if etype == "checkout.session.completed":
        _handle_setup_session_completed(obj)

    # off_session PaymentIntent 失敗 → past_due に更新
    elif etype == "payment_intent.payment_failed":
        customer_id = obj.get("customer")
        if customer_id:
            designer = Designer.query.filter_by(stripe_customer_id=customer_id).first()
            if designer:
                designer.subscription_status = "past_due"
                db.session.commit()

    return jsonify({"received": True}), 200


def _handle_setup_session_completed(session_obj):
    """Setup セッション完了: setup_intent から payment_method を取得して DB に保存。"""
    stripe = stripe_utils.get_stripe()
    if not stripe:
        return

    meta            = session_obj.get("metadata", {})
    designer_id     = meta.get("designer_id")
    customer_id     = session_obj.get("customer")
    setup_intent_id = session_obj.get("setup_intent")

    if not designer_id or not customer_id or not setup_intent_id:
        return

    designer = Designer.query.get(int(designer_id))
    if not designer:
        return

    try:
        setup_intent = stripe.SetupIntent.retrieve(setup_intent_id)
        pm_id = setup_intent.payment_method
        if not pm_id:
            return

        # Stripe 側でデフォルト支払方法を設定
        stripe.Customer.modify(
            customer_id,
            invoice_settings={"default_payment_method": pm_id},
        )

        designer.stripe_customer_id      = customer_id
        designer.stripe_payment_method_id = pm_id
        designer.subscription_status      = "active"
        db.session.commit()
        current_app.logger.info(
            f"[webhook] Designer {designer_id}: カード登録完了 PM={pm_id}"
        )
    except Exception as e:
        current_app.logger.error(f"[webhook] setup_session 処理エラー: {e}")
