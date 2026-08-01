import os
import re
import json
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

    def _generate_json(self, system: str, user_message: str):
        """JSON（オブジェクトまたは配列）を返すAPIを呼び出し、パース済みオブジェクトを返す。
        コードブロック除去 → 直接パース → {}/[] 切り出しの順に試みる。"""
        text = self._generate(system, user_message)
        cleaned = re.sub(r'```[\w]*', '', text).replace('```', '').strip()

        # trailing comma の修復
        cleaned = re.sub(r',\s*([\]}])', r'\1', cleaned)

        def _try(s):
            try:
                return json.loads(s)
            except (json.JSONDecodeError, ValueError):
                return None

        r = _try(cleaned)
        if r is not None:
            return r

        for open_c, close_c in [('{', '}'), ('[', ']')]:
            start = cleaned.find(open_c)
            end   = cleaned.rfind(close_c)
            if start >= 0 and end > start:
                r = _try(cleaned[start:end + 1])
                if r is not None:
                    return r

        snippet = cleaned[:300].replace('\n', '↵')
        raise ValueError(f"JSONを抽出できませんでした。先頭: {snippet}")
