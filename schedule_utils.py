"""schedule_utils.py — 投稿予約日時の自動計算"""
import calendar
from datetime import datetime, date, timedelta, time as dt_time, timezone


_JST = timezone(timedelta(hours=9))


def next_scheduled_at(client, existing_dates: set = None) -> datetime:
    """クライアントのスケジュール設定から次の投稿日時を返す。

    Args:
        client: Client モデルインスタンス
        existing_dates: すでに予約済みの date の集合（重複を避けるために使用）
    Returns:
        JST naive datetime
    """
    if existing_dates is None:
        existing_dates = set()

    today = datetime.now(_JST).date()
    post_time = client.default_post_time or dt_time(10, 0)

    if (client.schedule_type or "weekly") == "weekly":
        target_dow = client.schedule_day_of_week or 0  # 0=月曜
        # 明日以降で最初の対象曜日
        d = today + timedelta(days=1)
        days_ahead = (target_dow - d.weekday()) % 7
        d = d + timedelta(days=days_ahead)
        # 予約済みならば1週ずつずらす
        while d in existing_dates:
            d += timedelta(days=7)

    else:  # monthly
        target_dom = client.schedule_day_of_month or 1
        year, month = today.year, today.month
        while True:
            # 月の末日を超える日付は最終日に丸める
            last_day = calendar.monthrange(year, month)[1]
            dom = min(target_dom, last_day)
            d = date(year, month, dom)
            if d > today and d not in existing_dates:
                break
            month += 1
            if month > 12:
                month = 1
                year += 1

    return datetime.combine(d, post_time)


def bulk_auto_schedule(client, posts) -> int:
    """ステータスが draft/approved でかつ scheduled_at が未設定の投稿に
    スケジュールを自動割り当てる。Returns: 割り当て件数。"""
    from models import Post

    # 既存の予約済み日付を収集
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
            continue  # すでに設定済みはスキップ
        dt = next_scheduled_at(client, existing_dates)
        post.scheduled_at = dt
        existing_dates.add(dt.date())
        count += 1

    return count
