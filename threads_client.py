"""threads_client.py — Meta Threads API 連携"""
import requests

_BASE = "https://graph.threads.net/v1.0"
_TEXT_LIMIT = 500


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
        return {"success": False, "reason": data["error"].get("message", "Threads コンテナ作成失敗")}
    container_id = data.get("id")
    if not container_id:
        return {"success": False, "reason": "Threads: コンテナ ID が取得できませんでした"}

    # Step 2: 投稿
    r2 = requests.post(
        f"{_BASE}/{user_id}/threads_publish",
        params={"creation_id": container_id, "access_token": access_token},
        timeout=30,
    )
    data2 = r2.json()
    if "error" in data2:
        return {"success": False, "reason": data2["error"].get("message", "Threads 投稿失敗")}

    return {"success": True, "media_id": data2.get("id", "")}
