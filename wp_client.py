"""wp_client.py — WordPress REST API クライアント（新マルチテナントシステム用）"""
import base64
from datetime import datetime
import requests


def _auth_header(username: str, app_password: str) -> dict:
    token = base64.b64encode(f"{username}:{app_password}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


def _urls(base: str, resource: str):
    """REST API URL を2形式で返す。(標準形式, クエリ形式)"""
    b = base.rstrip("/")
    return (
        f"{b}/wp-json/wp/v2/{resource}",
        f"{b}/?rest_route=/wp/v2/{resource}",
    )


def _get_with_fallback(base, resource, headers, params=None, timeout=10):
    primary, fallback = _urls(base, resource)
    res = requests.get(primary, headers=headers, params=params, timeout=timeout)
    if res.status_code == 404:
        res = requests.get(fallback, headers=headers, params=params, timeout=timeout)
    return res


def _post_with_fallback(base, resource, headers, json=None, timeout=(15, 90)):
    primary, fallback = _urls(base, resource)
    res = requests.post(primary, headers=headers, json=json, timeout=timeout)
    if res.status_code == 404:
        res = requests.post(fallback, headers=headers, json=json, timeout=timeout)
    return res


def post_article(
    endpoint: str,
    username: str,
    app_password: str,
    title: str,
    body_html: str,
    publish_mode: str = "immediate",
    scheduled_at: datetime | None = None,
) -> dict:
    """WordPress に記事を投稿する。

    publish_mode:
      'immediate' → status='publish'
      'scheduled'  → status='future', date=scheduled_at(ISO8601)
    """
    if not endpoint or not username or not app_password:
        return {"success": False, "reason": "WordPress の接続情報が不完全です"}

    wp_url = endpoint.rstrip("/")

    if publish_mode == "scheduled" and scheduled_at:
        body = {
            "title": title,
            "content": body_html,
            "status": "future",
            "date": scheduled_at.strftime("%Y-%m-%dT%H:%M:%S"),
        }
    else:
        body = {
            "title": title,
            "content": body_html,
            "status": "publish",
        }

    try:
        res = _post_with_fallback(
            wp_url, "posts",
            headers=_auth_header(username, app_password),
            json=body,
        )
    except requests.exceptions.ConnectionError:
        return {"success": False, "reason": "WordPress に接続できません"}
    except requests.exceptions.Timeout:
        return {"success": False, "reason": "WordPress 接続タイムアウト（90秒）"}

    if res.status_code in (200, 201):
        data = res.json()
        return {
            "success": True,
            "wp_post_id": data.get("id"),
            "wp_post_url": data.get("link", ""),
        }

    try:
        err = res.json()
        reason = err.get("message", res.text[:300])
    except Exception:
        reason = res.text[:300]
    return {"success": False, "reason": f"HTTP {res.status_code}: {reason}"}


def test_connection(endpoint: str, username: str, app_password: str) -> dict:
    if not endpoint:
        return {"success": False, "reason": "エンドポイントが未設定です"}
    try:
        res = _get_with_fallback(
            endpoint, "users/me",
            headers=_auth_header(username, app_password),
        )
        if res.status_code == 200:
            return {"success": True, "name": res.json().get("name", "")}
        return {"success": False, "reason": f"認証失敗 (HTTP {res.status_code})"}
    except Exception as e:
        return {"success": False, "reason": str(e)}
