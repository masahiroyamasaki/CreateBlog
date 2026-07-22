"""threads_client.py — Meta Threads API 連携"""
import requests

_BASE = "https://graph.threads.net/v1.0"
_TEXT_LIMIT = 400


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


def post_text(user_id: str, access_token: str, text: str, url: str = "") -> dict:
    """Threads にテキスト投稿する。
    url が指定された場合はキャプション末尾に付与し、500文字に収まるよう本文を切り詰める。
    """
    suffix = f"\n\n{url}" if url else ""
    max_body = _TEXT_LIMIT - len(suffix)
    if len(text) > max_body:
        text = text[:max_body - 1] + "…"
    full_text = text + suffix

    # Step 1: コンテナ作成
    r = requests.post(
        f"{_BASE}/{user_id}/threads",
        params={"media_type": "TEXT", "text": full_text, "access_token": access_token},
        timeout=30,
    )
    data = r.json()
    if "error" in data:
        return {"success": False, "reason": _localize_error(data["error"])}
    container_id = data.get("id")
    if not container_id:
        return {"success": False, "reason": "【Threads】コンテナ ID が取得できませんでした"}

    # Step 2: 投稿
    r2 = requests.post(
        f"{_BASE}/{user_id}/threads_publish",
        params={"creation_id": container_id, "access_token": access_token},
        timeout=30,
    )
    data2 = r2.json()
    if "error" in data2:
        return {"success": False, "reason": _localize_error(data2["error"])}

    return {"success": True, "media_id": data2.get("id", "")}
