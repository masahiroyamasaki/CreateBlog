from .base import BaseAgent

SYSTEM = """あなたはSEOスペシャリストです。
提供されたブログ記事を分析し、SEO観点で最適化した改訂版記事を作成してください。

【チェック・修正項目】
1. **キーワード配置**: タイトル・見出し・冒頭段落にキーワードが適切に含まれているか
2. **見出し構造**: H1/H2/H3 の階層と論理構成が検索エンジンに評価されやすいか
3. **検索意図マッチ**: 記事が読者の検索意図（知りたい・やりたい・比較したい）に応えているか
4. **読みやすさ**: 1段落3〜5文程度、スキャンしやすいリスト・箇条書きの活用
5. **内部リンク示唆**: 関連トピックへの言及（実URLは不要）
6. **E-E-A-T**: 経験・専門性・権威性・信頼性を高める表現の強化

【出力ルール】
- SEO改善を反映した完全な記事本文のみを Markdown 形式で出力すること
- 前置き・変更点の説明・AIのコメントは一切含めないこと
- 元の文体・トーン・意図を維持しつつ、SEO的に弱い箇所のみ改善すること
- H1 タイトルから始まる完全な記事として仕上げること"""


class SeoCheckerAgent(BaseAgent):
    def _build_message(self, data: dict) -> str:
        draft = data.get("draft", "")
        topic = data.get("topic", "")
        keywords = data.get("keywords", "")
        kw_note = f"\n\n【ターゲットキーワード・大枠】\n{keywords}" if keywords else ""
        return f"""以下のブログ記事をSEO観点でチェックし、改善した記事を作成してください。

記事タイトル（テーマ）: {topic}{kw_note}

---
{draft}
---

SEO改善済みの完全な記事本文のみを Markdown 形式で出力してください。"""

    def stream(self, data: dict):
        yield from self._stream(SYSTEM, self._build_message(data))

    def run(self, data: dict) -> str:
        return self._generate(SYSTEM, self._build_message(data))
