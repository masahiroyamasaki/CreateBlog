"""pricing.py — 料金プラン定義"""

# platform_type → (投稿数, 月額料金) のリスト
PLANS = {
    "instagram": [
        {"posts": 4,  "fee": 4000},
        {"posts": 8,  "fee": 9000},
        {"posts": 12, "fee": 13000},
        {"posts": 16, "fee": 17000},
    ],
    "wordpress": [
        {"posts": 4,  "fee": 3000},
        {"posts": 8,  "fee": 7000},
        {"posts": 12, "fee": 9800},
        {"posts": 16, "fee": 11800},
        {"posts": 20, "fee": 13800},
        {"posts": 24, "fee": 15800},
        {"posts": 28, "fee": 17800},
    ],
    "custom_hp": [
        {"posts": 4,  "fee": 3000},
        {"posts": 8,  "fee": 7000},
        {"posts": 12, "fee": 9800},
        {"posts": 16, "fee": 11800},
        {"posts": 20, "fee": 13800},
        {"posts": 24, "fee": 15800},
        {"posts": 28, "fee": 17800},
    ],
}
