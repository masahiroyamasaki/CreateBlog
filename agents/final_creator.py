from .base import BaseAgent

SYSTEM = """あなたはプロの日本語ブログエディターです。
元の下書きと、内容チェック・リーガルチェックのフィードバックを踏まえて、
改善された最終版のブログ記事を Markdown 形式で作成してください。

最終版の要件:
- 内容チェックで指摘された問題点をすべて修正する
- リーガルチェックで🔴高リスク・🟡中リスクと判定された箇所を修正する
- 著者の意図・文体・トーンを維持しながら品質を向上させる
- H1 タイトルから始まる完全な記事として仕上げる
- 【重要】目標文字数が指定されている場合は必ずその文字数を守ること。大幅な増減は禁止。
- 記事本文のみを出力すること。前置き・後書き・変更点の説明・AIのコメントは一切含めないこと"""

_WC_MAP = {
    100:  "100字以内の超短文。タイトル除き本文は100字を絶対に超えないこと。",
    150:  "150字以内の短文。タイトル除き本文は150字を絶対に超えないこと。",
    200:  "約200字。簡潔にまとめ、200字を大きく超えないこと。",
    300:  "約300字。要点を絞り、300字前後で完結させること。",
    400:  "約400字。400字前後で完結させること。",
    500:  "約500字（短いTips記事）。500字前後で完結させること。",
    1000: "約1000字。読みやすい中編記事として1000字前後で完結させること。",
    1500: "約1500字。1500字前後でしっかりまとめること。",
    2000: "約2000字。2000字前後の読み物記事として構成すること。",
    3000: "約3000字。3000字前後のしっかりした読み物記事として構成すること。",
}


class FinalCreatorAgent(BaseAgent):
    def _build_message(self, data: dict) -> str:
        draft = data.get("draft", "")
        content_check = data.get("content_check", "")
        legal_check = data.get("legal_check", "")

        wc_num = int(data.get("word_count", 0) or 0)
        wc_note = f"\n\n## 【厳守】目標文字数\n{_WC_MAP[wc_num]}" if wc_num in _WC_MAP else ""

        return f"""以下の情報を基に、最終版のブログ記事を作成してください。{wc_note}

## 元の下書き
---
{draft}
---

## 内容チェック結果
---
{content_check}
---

## リーガルチェック結果
---
{legal_check}
---

すべてのフィードバックを反映した最終版記事を Markdown 形式で作成してください。"""

    def stream(self, data: dict):
        yield from self._stream(SYSTEM, self._build_message(data))

    def run(self, data: dict) -> str:
        return self._generate(SYSTEM, self._build_message(data))
