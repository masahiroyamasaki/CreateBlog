"""schedule_utils.py — 投稿予約日時の自動計算（複数曜日・複数日付対応）"""
import calendar
from datetime import datetime, date, timedelta, time as dt_time, timezone

_JST = timezone(timedelta(hours=9))


def _parse_days(raw_text, fallback_int) -> list[int]:
    """カンマ区切り文字列または旧整数値を整数リストに変換する。"""
    raw = (str(raw_text) if raw_text is not None else "").strip()
    parts = [p.strip() for p in raw.split(",") if p.strip().isdigit()]
    if parts:
        return sorted(set(int(p) for p in parts))
    # 旧整数フォールバック
    try:
        return [int(fallback_int)]
    except (TypeError, ValueError):
        return [0]


def _target_weekdays(client) -> list[int]:
    return _parse_days(
        getattr(client, "schedule_days_of_week", None),
        getattr(client, "schedule_day_of_week", 0),
    )


def _target_monthdays(client) -> list[int]:
    days = _parse_days(
        getattr(client, "schedule_days_of_month", None),
        getattr(client, "schedule_day_of_month", 1),
    )
    return [max(1, min(d, 31)) for d in days]


def next_scheduled_at(client, existing_dates: set = None) -> datetime:
    """クライアントのスケジュール設定から次の投稿日時を返す（JST naive）。"""
    if existing_dates is None:
        existing_dates = set()

    today = datetime.now(_JST).date()
    post_time = client.default_post_time or dt_time(10, 0)

    if (client.schedule_type or "weekly") == "weekly":
        target_dows = set(_target_weekdays(client))
        d = today + timedelta(days=1)
        for _ in range(365):
            if d.weekday() in target_dows and d not in existing_dates:
                return datetime.combine(d, post_time)
            d += timedelta(days=1)

    else:  # monthly
        target_doms = _target_monthdays(client)
        year, month = today.year, today.month
        for _ in range(48):  # 最大4年先まで
            last_day = calendar.monthrange(year, month)[1]
            for dom in target_doms:
                actual_dom = min(dom, last_day)
                d = date(year, month, actual_dom)
                if d > today and d not in existing_dates:
                    return datetime.combine(d, post_time)
            month += 1
            if month > 12:
                month = 1
                year += 1

    # 安全フォールバック
    return datetime.combine(today + timedelta(days=7), post_time)


def bulk_auto_schedule(client, posts) -> int:
    """scheduled_at が未設定の投稿にスケジュールを順番に割り当てる。
    Returns: 割り当て件数。"""
    from models import Post

    existing_dates = {
        p.scheduled_at.date()
        for p in Post.query.filter(
            Post.client_id == client.id,
            Post.scheduled_at.isnot(None),
        ).all()
        if p.scheduled_at
    }

    count = 0
    for post in posts:
        if post.scheduled_at:
            continue
        dt = next_scheduled_at(client, existing_dates)
        post.scheduled_at = dt
        existing_dates.add(dt.date())
        count += 1

    return count
