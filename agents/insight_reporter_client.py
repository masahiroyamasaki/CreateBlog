"""Stage 2-B: エンドクライアント向け月次サマリー生成エージェント"""
import json
from .base import BaseAgent

SYSTEM = """あなたはInstagram運用代行の成果を、クライアント企業の担当者向けに報告する文章を作成するAIです。
運用を任せている側（＝専門知識がない発注者）が「継続する価値がある」と感じられる報告にしてください。

# 入力
過去4週間分のStage 1分析結果（配列）

# 出力制約
1. 社外向けの丁寧な文体（です・ます調）
2. 数値は「先月比」「4週間の推移」など、変化の方向性を中心に伝える
3. 弱かった点があっても、次月の改善方針とセットで前向きに触れる。隠蔽や誇張はしない
4. 専門用語は使わない。使う場合は括弧で平易な説明を添える
5. 全体で600字以内
6. 出力はプレーンテキスト（レポート本文のみ、見出しは可）

# 出力形式（プレーンテキスト）
以下の見出し構成に従う：
- 今月のハイライト
- 主要指標の推移
- 特に反応が良かった投稿の傾向
- 来月に向けて"""


class InsightReporterClientAgent(BaseAgent):
    def run(self, stage1_results: list, industry: str = "", designer_name: str = "") -> str:
        industry_note   = f"\nクライアント業種：{industry}" if industry else ""
        designer_note   = f"\nデザイナー名（署名用）：{designer_name}" if designer_name else ""
        user_msg = (
            "以下の4週間分のデータから、クライアント向け月次サマリーを作成してください。"
            + industry_note
            + designer_note
            + "\n\n"
            + json.dumps(stage1_results, ensure_ascii=False, indent=2)
        )
        return self._generate(SYSTEM, user_msg)
