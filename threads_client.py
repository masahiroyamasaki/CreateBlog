"""threads_client.py — Meta Threads API 連携"""
import requests

_BASE = "https://graph.threads.net/v1.0"
_TEXT_LIMIT = 500  # Threads テキスト上限


def _localize_error(err: dict) -> str:
    """Threads API エラーを日本語メッセージに変換する。"""
    code = err.get("code", 0)
    subcode = err.get("error_subcode", 0)
    msg_lower = (err.get("message") or "").lower()
    if code == 190:
        if subcode in (463, 467) or "expir" in msg_lower:
            return (
                "【Threads】アクセストークンの有効期限が切れています。"
                "企業編集ページからトークンをリセット・更新してください。"
            )
        return (
            "【Threads】アクセストークンが無効です。"
            "企業編集ページからトークンをリセット・更新してください。"
        )
    if "access token" in msg_lower and ("expir" in msg_lower or "invalid" in msg_lower):
        return (
            "【Threads】アクセストークンが無効または期限切れです。"
            "企業編集ページからトークンをリセット・更新してください。"
        )
    return f"【Threads】{err.get('message', 'API エラーが発生しました')}"


def _build_text(text: str, url: str) -> str:
    """URL を末尾に付加し、_TEXT_LIMIT 以内に収める。"""
    suffix = f"\n\n{url}" if url else ""
    max_body = _TEXT_LIMIT - len(suffix)
    if len(text) > max_body:
        text = text[:max_body - 1] + "…"
    return text + suffix


def _publish_container(user_id: str, container_id: str, access_token: str) -> dict:
    """作成済みコンテナを公開する（Step 2 共通）。"""
    r = requests.post(
        f"{_BASE}/{user_id}/threads_publish",
        data={"creation_id": container_id, "access_token": access_token},
        timeout=30,
    )
    data = r.json()
    if "error" in data:
        return {"success": False, "reason": _localize_error(data["error"])}
    return {"success": True, "media_id": data.get("id", "")}


def post_text(user_id: str, access_token: str, text: str, url: str = "") -> dict:
    """Threads にテキストのみ投稿する。"""
    full_text = _build_text(text, url)

    r = requests.post(
        f"{_BASE}/{user_id}/threads",
        data={"media_type": "TEXT", "text": full_text, "access_token": access_token},
        timeout=30,
    )
    data = r.json()
    if "error" in data:
        return {"success": False, "reason": _localize_error(data["error"])}
    container_id = data.get("id")
    if not container_id:
        return {"success": False, "reason": "【Threads】コンテナ ID が取得できませんでした"}

    return _publish_container(user_id, container_id, access_token)


def post_image(user_id: str, access_token: str, image_url: str,
               text: str, url: str = "") -> dict:
    """Threads に画像1枚を投稿する。"""
    full_text = _build_text(text, url)

    r = requests.post(
        f"{_BASE}/{user_id}/threads",
        data={
            "media_type": "IMAGE",
            "image_url": image_url,
            "text": full_text,
            "access_token": access_token,
        },
        timeout=30,
    )
    data = r.json()
    if "error" in data:
        return {"success": False, "reason": _localize_error(data["error"])}
    container_id = data.get("id")
    if not container_id:
        return {"success": False, "reason": "【Threads】画像コンテナ ID が取得できませんでした"}

    return _publish_container(user_id, container_id, access_token)


def post_carousel(user_id: str, access_token: str, image_urls: list,
                  text: str, url: str = "") -> dict:
    """Threads にカルーセル（複数画像）を投稿する。最大 10 枚。"""
    image_urls = image_urls[:10]
    full_text = _build_text(text, url)

    # Step 1: 各画像のアイテムコンテナを作成
    item_ids = []
    for img_url in image_urls:
        r = requests.post(
            f"{_BASE}/{user_id}/threads",
            data={
                "media_type": "IMAGE",
                "image_url": img_url,
                "is_carousel_item": "true",
                "access_token": access_token,
            },
            timeout=30,
        )
        data = r.json()
        if "error" in data:
            return {"success": False, "reason": _localize_error(data["error"])}
        item_id = data.get("id")
        if not item_id:
            return {"success": False, "reason": "【Threads】カルーセルアイテムコンテナの作成に失敗しました"}
        item_ids.append(item_id)

    # Step 2: カルーセルコンテナを作成
    r2 = requests.post(
        f"{_BASE}/{user_id}/threads",
        data={
            "media_type": "CAROUSEL",
            "children": ",".join(item_ids),
            "text": full_text,
            "access_token": access_token,
        },
        timeout=30,
    )
    data2 = r2.json()
    if "error" in data2:
        return {"success": False, "reason": _localize_error(data2["error"])}
    carousel_id = data2.get("id")
    if not carousel_id:
        return {"success": False, "reason": "【Threads】カルーセルコンテナ ID が取得できませんでした"}

    # Step 3: 公開
    return _publish_container(user_id, carousel_id, access_token)
