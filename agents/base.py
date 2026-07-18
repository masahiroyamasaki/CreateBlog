import os
import logging
import anthropic
from dotenv import load_dotenv

load_dotenv()

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
logger = logging.getLogger(__name__)

# ストリーミング(UI表示用)のトークン上限
STREAM_MAX_TOKENS = 8192
# バッチ処理(完全生成)のトークン上限
GENERATE_MAX_TOKENS = 8192


class BaseAgent:
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.model = MODEL

    def _stream(self, system: str, user_message: str):
        """UIリアルタイム表示専用。テキストを順次 yield する。"""
        with self.client.messages.stream(
            model=self.model,
            max_tokens=STREAM_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        ) as stream:
            yield from stream.text_stream

    def _generate(self, system: str, user_message: str) -> str:
        """バックグラウンド処理専用。完全なレスポンスを1回で取得する。
        stop_reason が max_tokens の場合はログに警告を出す。"""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=GENERATE_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
        text = response.content[0].text if response.content else ""
        if response.stop_reason == "max_tokens":
            logger.warning(
                f"[{self.__class__.__name__}] max_tokens ({GENERATE_MAX_TOKENS}) に達しました。"
                f" 生成文字数={len(text)}。トークン上限の引き上げを検討してください。"
            )
        else:
            logger.info(
                f"[{self.__class__.__name__}] 完了 stop_reason={response.stop_reason}"
                f" 文字数={len(text)}"
            )
        return text
