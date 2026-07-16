"""instagram.py — Instagram Graph API クライアント"""
import time
import requests
from config import Config

_BASE = Config.IG_API_BASE
_POLL_INTERVAL = 3   # ステータス確認間隔(秒)
_POLL_MAX = 20       # 最大ポーリング回数(= 最大60秒待機)


# ─── 内部ヘルパー ─────────────────────────────────────────────────────────

def _post(path: str, token: str, params: dict) -> dict:
    """Graph API に POST して JSON を返す。エラーは reason に入れて返す。"""
    params["access_token"] = token
    try:
        res = requests.post(
            f"{_BASE}/{path}",
            data=params,
            timeout=30,
        )
        data = res.json()
        if "error" in data:
            return {"success": False, "reason": data["error"].get("message", str(data["error"]))}
        return {"success": True, **data}
    except requests.exceptions.Timeout:
        return {"success": False, "reason": "Instagram API タイムアウト"}
    except Exception as e:
        return {"success": False, "reason": f"リクエストエラー: {e}"}


def _get(path: str, token: str, params: dict | None = None) -> dict:
    p = dict(params or {})
    p["access_token"] = token
    try:
        res = requests.get(f"{_BASE}/{path}", params=p, timeout=15)
        return res.json()
    except Exception as e:
        return {"error": {"message": str(e)}}


def _wait_until_finished(container_id: str, token: str) -> bool:
    """コンテナのステータスが FINISHED になるまでポーリングする。"""
    for _ in range(_POLL_MAX):
        data = _get(container_id, token, {"fields": "status_code"})
        status = data.get("status_code", "")
        if status == "FINISHED":
            return True
        if status in ("ERROR", "EXPIRED"):
            return False
        time.sleep(_POLL_INTERVAL)
    return False


# ─── 単一画像投稿 ─────────────────────────────────────────────────────────

def post_single_image(
    ig_user_id: str,
    access_token: str,
    image_url: str,
    caption: str,
) -> dict:
    """単一画像を Instagram に投稿する。media_id を返す。"""
    # Step 1: メディアコンテナ作成
    r = _post(f"{ig_user_id}/media", access_token, {
        "image_url": image_url,
        "caption": caption,
    })
    if not r.get("success"):
        return r
    container_id = r.get("id")
    if not container_id:
        return {"success": False, "reason": "コンテナ ID が取得できませんでした"}

    # ポーリング: FINISHED 待ち
    if not _wait_until_finished(container_id, access_token):
        return {"success": False, "reason": f"メディアコンテナの処理がタイムアウト (id={container_id})"}

    # Step 2: 公開
    r2 = _post(f"{ig_user_id}/media_publish", access_token, {
        "creation_id": container_id,
    })
    if not r2.get("success"):
        return r2
    return {"success": True, "media_id": r2.get("id", "")}


# ─── カルーセル投稿 ───────────────────────────────────────────────────────

def create_carousel_item(
    ig_user_id: str,
    access_token: str,
    image_url: str,
) -> dict:
    """カルーセル用の子コンテナを作成し、container_id を返す。"""
    r = _post(f"{ig_user_id}/media", access_token, {
        "image_url": image_url,
        "is_carousel_item": "true",
    })
    if not r.get("success"):
        return r
    container_id = r.get("id")
    if not container_id:
        return {"success": False, "reason": "子コンテナ ID が取得できませんでした"}
    if not _wait_until_finished(container_id, access_token):
        return {"success": False, "reason": f"子コンテナの処理タイムアウト (id={container_id})"}
    return {"success": True, "container_id": container_id}


def post_carousel(
    ig_user_id: str,
    access_token: str,
    container_ids: list[str],
    caption: str,
) -> dict:
    """親カルーセルコンテナを作成・公開する。media_id を返す。"""
    if not container_ids:
        return {"success": False, "reason": "子コンテナが空です"}

    # Step 2: 親カルーセルコンテナ作成
    r = _post(f"{ig_user_id}/media", access_token, {
        "media_type": "CAROUSEL",
        "caption": caption,
        "children": ",".join(container_ids),
    })
    if not r.get("success"):
        return r
    creation_id = r.get("id")
    if not creation_id:
        return {"success": False, "reason": "カルーセルコンテナ ID が取得できませんでした"}

    if not _wait_until_finished(creation_id, access_token):
        return {"success": False, "reason": f"カルーセルコンテナの処理タイムアウト (id={creation_id})"}

    # Step 3: 公開
    r3 = _post(f"{ig_user_id}/media_publish", access_token, {
        "creation_id": creation_id,
    })
    if not r3.get("success"):
        return r3
    return {"success": True, "media_id": r3.get("id", "")}
