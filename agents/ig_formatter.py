"""agents/ig_formatter.py — ブログ記事を Instagram 投稿文に変換"""
from .base import BaseAgent

SYSTEM = """あなたはInstagramマーケターです。
提供されたブログ記事の内容をもとに、Instagram投稿文を作成してください。

要件:
- 日本語
- 本文は1000文字前後（読みやすく魅力的な文章）
- 改行を活用して視覚的に読みやすくする
- 絵文字を適度に使って親しみやすさを出す（使いすぎ禁止）
- 投稿内容に関連するハッシュタグを5〜10個、本文の後ろに付ける
- HTMLタグは一切含めない（プレーンテキストのみ）
- タイトルや「投稿文:」などのメタ情報は一切含めない
- 投稿本文とハッシュタグのみを出力する"""


class IgFormatterAgent(BaseAgent):
    def _build_message(self, data: dict) -> str:
        blog_content = data.get("blog_content", "")
        topic = data.get("topic", "")
        client_name = data.get("client_name", "")
        return f"""以下のブログ記事をInstagram投稿文に変換してください。

企業名: {client_name}
トピック: {topic}

## ブログ記事
---
{blog_content}
---

Instagram投稿文（本文1000文字前後＋ハッシュタグ）を出力してください。"""

    def stream(self, data: dict):
        yield from self._stream(SYSTEM, self._build_message(data))

    def run(self, data: dict) -> str:
        return self._generate(SYSTEM, self._build_message(data))
