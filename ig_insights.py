"""ig_insights.py — Instagram Graph API インサイトデータ取得"""
import requests
from datetime import date, datetime, timedelta, timezone
from typing import Optional

GRAPH_BASE = "https://graph.instagram.com/v18.0"


def _unix(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


class InstagramInsightsFetcher:
    def __init__(self, access_token: str, ig_user_id: str):
        self._token = access_token
        self._uid = ig_user_id

    def _get(self, path: str, **params) -> dict:
        params["access_token"] = self._token
        resp = requests.get(f"{GRAPH_BASE}/{path}", params=params, timeout=20)
        data = resp.json()
        if "error" in data:
            err = data["error"]
            raise RuntimeError(f"[{err.get('code')}] {err.get('message', 'API error')}")
        return data

    # ── フォロワー数 ──────────────────────────────────────────────────────────

    def get_followers(self) -> Optional[int]:
        try:
            data = self._get(self._uid, fields="followers_count")
            return data.get("followers_count")
        except Exception:
            return None

    # ── アカウントインサイト（1週分）────────────────────────────────────────────

    def get_weekly_metrics(self, week_start: date) -> dict:
        since = _unix(week_start)
        until = _unix(week_start + timedelta(days=7))

        metrics: dict = {"reach": None, "profile_views": None,
                         "follows": None, "website_clicks": None}

        for name in ("reach", "profile_views", "website_clicks"):
            try:
                data = self._get(
                    f"{self._uid}/insights",
                    metric=name, period="week",
                    since=since, until=until,
                )
                for item in data.get("data", []):
                    if item.get("name") == name:
                        vals = item.get("values", [])
                        if vals:
                            metrics[name] = vals[0].get("value")
                        break
            except Exception:
                pass

        # フォロワー増減
        try:
            data = self._get(
                f"{self._uid}/insights",
                metric="follows_and_unfollows", period="week",
                since=since, until=until,
            )
            for item in data.get("data", []):
                if item.get("name") == "follows_and_unfollows":
                    vals = item.get("values", [])
                    if vals:
                        v = vals[0].get("value", {})
                        if isinstance(v, dict):
                            metrics["follows"] = v.get("follows", 0) - v.get("unfollows", 0)
                        elif isinstance(v, (int, float)):
                            metrics["follows"] = int(v)
                    break
        except Exception:
            pass

        return metrics

    # ── 指定週の投稿データ ────────────────────────────────────────────────────

    def get_media_for_week(self, week_start: date) -> list:
        since = _unix(week_start)
        until = _unix(week_start + timedelta(days=7))

        data = self._get(
            f"{self._uid}/media",
            fields="id,media_type,caption,timestamp",
            since=since, until=until, limit=50,
        )

        posts = []
        for media in data.get("data", []):
            media_id = media["id"]
            media_type = media.get("media_type", "IMAGE").lower()
            caption = (media.get("caption") or "")[:80]

            p: dict = {
                "post_id": media_id,
                "media_type": media_type,
                "caption_summary": caption or "(キャプションなし)",
                "reach": None, "saved": None, "likes": None,
                "comments": None, "shares": None, "views": None,
            }

            # per-post insights（instagram_manage_insights 必要）
            try:
                metric_names = "reach,saved,shares"
                if "video" in media_type or media_type == "reel":
                    metric_names += ",plays"
                ins = self._get(f"{media_id}/insights", metric=metric_names, period="lifetime")
                for item in ins.get("data", []):
                    name = item.get("name")
                    val = (
                        item.get("values", [{}])[0].get("value")
                        if item.get("values")
                        else item.get("value")
                    )
                    if name == "reach":
                        p["reach"] = val
                    elif name == "saved":
                        p["saved"] = val
                    elif name == "shares":
                        p["shares"] = val
                    elif name in ("plays", "video_views"):
                        p["views"] = val
            except Exception:
                pass

            # いいね・コメント（基本フィールド、常に取得可）
            try:
                detail = self._get(media_id, fields="like_count,comments_count")
                p["likes"] = detail.get("like_count")
                p["comments"] = detail.get("comments_count")
            except Exception:
                pass

            posts.append(p)

        return posts

    # ── 全データ取得（WeeklyInsight 作成用）─────────────────────────────────

    def fetch_insight_data(self, week_start: date) -> dict:
        followers = self.get_followers()
        this_week = self.get_weekly_metrics(week_start)
        last_week = self.get_weekly_metrics(week_start - timedelta(weeks=1))

        # 直近4週の平均
        weekly_history = []
        for i in range(1, 5):
            try:
                weekly_history.append(self.get_weekly_metrics(week_start - timedelta(weeks=i)))
            except Exception:
                pass

        def _avg(key):
            vals = [w[key] for w in weekly_history if w.get(key) is not None]
            return round(sum(vals) / len(vals)) if vals else None

        four_week_avg = {k: _avg(k) for k in ("reach", "profile_views", "follows", "website_clicks")}

        posts = self.get_media_for_week(week_start)

        return {
            "followers": followers,
            "this_week": this_week,
            "last_week": last_week,
            "four_week_avg": four_week_avg,
            "posts": posts,
        }
