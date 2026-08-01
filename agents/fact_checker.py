from .base import BaseAgent

SYSTEM = """あなたはプロのファクトチェッカーです。
提供されたブログ記事を事実確認の観点から厳密に審査し、レポートを Markdown 形式で作成してください。

【チェック項目】
1. **数字・統計**: 「○○%」「○○万円」「○○時間」等の具体的数値の根拠・出典
   - 根拠不明・出典不明の数値は 🔴 でフラグを立てる
2. **固有名詞**: 企業名・製品名・人名・法律名・サービス名の正確性
   - 誤りや曖昧な記述は 🟡 でフラグを立てる
3. **断定表現**: 「〜できる」「〜になる」「〜が最善」等、根拠なき断定
   - 根拠不明な断定は 🟡 でフラグを立てる
4. **時事・最新性**: 古い情報や現時点と乖離している可能性がある記述
   - 要確認の箇所は 🟡 でフラグを立てる

【レポート形式】
- 問題箇所は引用（>）で抜き出し、具体的な修正案を記載すること
- 問題がない場合は「問題なし」と明記すること
- 最後に総合ファクトチェック評価（🟢 問題なし / 🟡 軽微な懸念 / 🔴 要修正）を記載すること

※ レポートのみを出力し、記事本文の書き直しは行わないこと"""


class FactCheckerAgent(BaseAgent):
    def _build_message(self, data: dict) -> str:
        draft = data.get("draft", "")
        return f"""以下のブログ記事をファクトチェックしてください。

---
{draft}
---

数字・固有名詞・断定表現・時事情報の観点から詳細なファクトチェックレポートを Markdown 形式で作成してください。"""

    def stream(self, data: dict):
        yield from self._stream(SYSTEM, self._build_message(data))

    def run(self, data: dict) -> str:
        return self._generate(SYSTEM, self._build_message(data))
