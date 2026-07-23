"""agents/ig_formatter.py — ブログ記事を Instagram / Threads 投稿文に変換"""
from .base import BaseAgent

SYSTEM = """あなたはSNSマーケターです。
提供されたブログ記事の内容をもとに、SNS投稿文を作成してください。

要件:
- 日本語
- 改行を活用して視覚的に読みやすくする
- 絵文字を適度に使って親しみやすさを出す（使いすぎ禁止）
- 投稿内容に関連するハッシュタグを5〜10個、本文の後ろに付ける
- HTMLタグは一切含めない（プレーンテキストのみ）
- タイトルや「投稿文:」「トピック:」などのメタ情報は一切含めない
- 企業名・屋号・アカウント名・@メンション・URL・社名は本文に絶対含めない（ブログ記事内に登場する社名・ブランド名も含めてはならない）
- 「〜です！」「〜でお届けします！」のような自己紹介・挨拶で始めることは禁止
- 投稿の1文字目から読者へのメッセージ・問いかけ・共感から始めること
- 【最重要】指定された文字数制限を絶対に守ること。超過した場合は必ず削ること
- 出力は投稿本文とハッシュタグのみ"""


class IgFormatterAgent(BaseAgent):
    def _build_message(self, data: dict) -> str:
        blog_content = data.get("blog_content", "")
        topic = data.get("topic", "")
        threads_limit = int(data.get("threads_limit", 0) or 0)
        word_count = int(data.get("word_count", 0) or 0)

        if threads_limit:
            char_req = (
                f"【文字数厳守】本文は{threads_limit}文字以内で作成すること。"
                f"ハッシュタグを含む全体も500文字以内に収めること。"
                f"Threads同時投稿のため絶対に超過禁止。超えた場合は必ず削って{threads_limit}字に収めること。"
            )
            char_label = f"本文{threads_limit}字以内＋ハッシュタグ（合計500字以内）"
        elif word_count:
            char_req = f"本文は{word_count}文字前後で作成すること。大幅な超過・不足は禁止。"
            char_label = f"本文{word_count}字前後＋ハッシュタグ"
        else:
            char_req = "本文は1000文字前後（読みやすく魅力的な文章）で作成すること。"
            char_label = "本文1000字前後＋ハッシュタグ"

        return f"""以下のブログ記事をSNS投稿文に変換してください。

【文字数指定】{char_req}

トピック: {topic}

## ブログ記事
---
{blog_content}
---

SNS投稿文（{char_label}）を出力してください。
※ 冒頭にアカウント名・企業名を書かず、すぐに本文内容から始めること。"""

    def stream(self, data: dict):
        yield from self._stream(SYSTEM, self._build_message(data))

    def run(self, data: dict) -> str:
        return self._generate(SYSTEM, self._build_message(data))
