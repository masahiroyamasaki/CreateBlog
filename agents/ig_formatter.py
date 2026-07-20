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
- タイトルや「投稿文:」「トピック:」などのメタ情報は一切含めない
- 企業名・屋号・アカウント名・@メンション・URL・社名は本文に絶対含めない（ブログ記事内に登場する社名・ブランド名も含めてはならない）
- 「〜です！」「〜でお届けします！」のような自己紹介・挨拶で始めることは禁止
- 投稿の1文字目から読者へのメッセージ・問いかけ・共感から始めること
- 出力は投稿本文とハッシュタグのみ"""


class IgFormatterAgent(BaseAgent):
    def _build_message(self, data: dict) -> str:
        blog_content = data.get("blog_content", "")
        topic = data.get("topic", "")
        return f"""以下のブログ記事をInstagram投稿文に変換してください。

トピック: {topic}

## ブログ記事
---
{blog_content}
---

Instagram投稿文（本文1000文字前後＋ハッシュタグ）を出力してください。
※ 冒頭にアカウント名・企業名を書かず、すぐに本文内容から始めること。"""

    def stream(self, data: dict):
        yield from self._stream(SYSTEM, self._build_message(data))

    def run(self, data: dict) -> str:
        return self._generate(SYSTEM, self._build_message(data))
