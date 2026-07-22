"""threads_client.py — Meta Threads API 連携"""
import requests

_BASE = "https://graph.threads.net/v1.0"
_TEXT_LIMIT = 500


def post_text(user_id: str, access_token: str, text: str) -> dict:
    """Threads にテキスト投稿する。500文字を超える場合は切り詰める。"""
    if len(text) > _TEXT_LIMIT:
        text = text[: _TEXT_LIMIT - 1] + "…"

    # Step 1: コンテナ作成
    r = requests.post(
        f"{_BASE}/{user_id}/threads",
        params={"media_type": "TEXT", "text": text, "access_token": access_token},
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
