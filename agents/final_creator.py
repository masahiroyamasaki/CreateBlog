from .base import BaseAgent

SYSTEM = """あなたはプロの日本語ブログエディターです。
元の下書きと、内容チェック・リーガルチェックのフィードバックを踏まえて、
改善された最終版のブログ記事を Markdown 形式で作成してください。

最終版の要件:
- 内容チェックで指摘された問題点をすべて修正する
- リーガルチェックで🔴高リスク・🟡中リスクと判定された箇所を修正する
- 著者の意図・文体・トーンを維持しながら品質を向上させる
- H1 タイトルから始まる完全な記事として仕上げる
- 記事本文のみを出力すること。前置き・後書き・変更点の説明・AIのコメントは一切含めないこと"""


class FinalCreatorAgent(BaseAgent):
    def stream(self, data: dict):
        draft = data.get("draft", "")
        content_check = data.get("content_check", "")
        legal_check = data.get("legal_check", "")

        user_message = f"""以下の情報を基に、最終版のブログ記事を作成してください。

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

        yield from self._stream(SYSTEM, user_message)
