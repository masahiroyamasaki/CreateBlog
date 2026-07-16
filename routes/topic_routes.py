"""routes/topic_routes.py — 記事ネタキュー管理"""
from flask import render_template, request, redirect, url_for, flash, jsonify, abort
from flask_login import login_required, current_user
from models import db, Client, TopicQueue
from routes import designer_bp


def _assert_access(client: Client):
    if not current_user.can_access_client(client.id):
        abort(403)


@designer_bp.route("/clients/<int:client_id>/topics")
@login_required
def topic_list(client_id: int):
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    topics = (
        TopicQueue.query.filter_by(client_id=client_id, status="pending")
        .order_by(TopicQueue.sort_order, TopicQueue.id)
        .all()
    )
    pending_count = len(topics)
    return render_template(
        "designer/topics/list.html",
        client=client,
        topics=topics,
        pending_count=pending_count,
    )


@designer_bp.route("/clients/<int:client_id>/topics/add", methods=["POST"])
@login_required
def topic_add(client_id: int):
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    title = request.form.get("title", "").strip()
    outline = request.form.get("outline", "").strip()
    if not title:
        flash("タイトルは必須です", "error")
        return redirect(url_for("designer.topic_list", client_id=client_id))

    # sort_order: 既存末尾 + 1
    last = (
        TopicQueue.query.filter_by(client_id=client_id, status="pending")
        .order_by(TopicQueue.sort_order.desc())
        .first()
    )
    next_order = (last.sort_order + 1) if last else 1

    topic = TopicQueue(
        client_id=client_id,
        title=title,
        outline=outline,
        sort_order=next_order,
        created_by="designer",
        created_by_designer_id=current_user.id,
    )
    db.session.add(topic)
    db.session.commit()
    flash(f"「{title}」を追加しました", "success")
    return redirect(url_for("designer.topic_list", client_id=client_id))


@designer_bp.route("/clients/<int:client_id>/topics/<int:topic_id>/edit", methods=["POST"])
@login_required
def topic_edit(client_id: int, topic_id: int):
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    topic = TopicQueue.query.get_or_404(topic_id)
    if topic.client_id != client_id:
        abort(403)

    topic.title = request.form.get("title", topic.title).strip()
    topic.outline = request.form.get("outline", topic.outline).strip()
    db.session.commit()
    return jsonify({"success": True})


@designer_bp.route("/clients/<int:client_id>/topics/<int:topic_id>/delete", methods=["POST"])
@login_required
def topic_delete(client_id: int, topic_id: int):
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    topic = TopicQueue.query.get_or_404(topic_id)
    if topic.client_id != client_id:
        abort(403)
    db.session.delete(topic)
    db.session.commit()
    flash("削除しました", "success")
    return redirect(url_for("designer.topic_list", client_id=client_id))


@designer_bp.route("/clients/<int:client_id>/topics/reorder", methods=["POST"])
@login_required
def topic_reorder(client_id: int):
    """ドラッグ&ドロップ後の順序を保存する。body: {"order": [id, id, ...]}"""
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    order = request.json.get("order", [])
    for i, topic_id in enumerate(order, 1):
        TopicQueue.query.filter_by(id=topic_id, client_id=client_id).update(
            {"sort_order": i}
        )
    db.session.commit()
    return jsonify({"success": True})
