"""image_post_gen.py — Stage 0: 画像からテキスト抽出 → 記事トピック生成

フロー:
  extract_text_from_images(paths)  → 各画像の文字・内容を Claude Vision で抽出
  build_topic_from_images(results) → 抽出結果から投稿タイトル・アウトラインを生成
"""

import base64
import json
import logging
import os
import re

logger = logging.getLogger(__name__)

_SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_EXT_TO_MEDIA = {
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".webp": "image/webp",
    ".gif":  "image/gif",
}


# ─── 画像 → base64 ───────────────────────────────────────────────────────────

def _to_base64(file_path: str) -> tuple[str, str]:
    """画像ファイルを (base64文字列, media_type) に変換する。"""
    ext = os.path.splitext(file_path)[1].lower()
    media_type = _EXT_TO_MEDIA.get(ext, "image/jpeg")
    with open(file_path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("utf-8")
    return data, media_type


# ─── 1枚の画像分析 ────────────────────────────────────────────────────────────

def extract_text_from_image(file_path: str) -> dict:
    """Claude Vision で画像1枚を分析し、テキスト・説明・要点を返す。

    Returns:
        {
            "raw_text":   str,   # 画像内の文字（なければ空文字）
            "description": str,  # 画像の内容説明
            "key_points": list,  # 要点リスト（str のリスト）
        }
    """
    import anthropic
    from config import Config

    api_key = Config.ANTHROPIC_API_KEY
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY が設定されていません")

    try:
        b64_data, media_type = _to_base64(file_path)
    except Exception as e:
        logger.warning(f"[Stage0] 画像読み込み失敗: {file_path} — {e}")
        return {"raw_text": "", "description": "", "key_points": []}

    prompt = """この画像を詳しく分析してください。

以下のフォーマットで出力してください：

RAW_TEXT:
（画像内に書かれているテキスト・文字をすべてそのまま書き起こす。テキストがない場合は「なし」）

DESCRIPTION:
（画像に写っている内容・シーン・商品・サービス・人物・場所などの詳細な説明。3〜5文）

KEY_POINTS:
・（画像から読み取れる重要な情報や特徴を箇条書きで3〜5個）"""

    client_obj = anthropic.Anthropic(api_key=api_key)
    message = client_obj.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": b64_data,
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }],
    )

    raw = message.content[0].text.strip()
    logger.info(f"[Stage0] 画像分析完了: {os.path.basename(file_path)}")

    def _section(key: str) -> str:
        m = re.search(rf'{key}:\n(.*?)(?=\n[A-Z_]+:|$)', raw, re.DOTALL)
        return m.group(1).strip() if m else ""

    raw_text = _section("RAW_TEXT")
    description = _section("DESCRIPTION")
    key_points = [
        line.lstrip("・-•").strip()
        for line in _section("KEY_POINTS").splitlines()
        if line.strip().lstrip("・-•").strip()
    ]

    return {
        "raw_text":    "" if raw_text in ("なし", "NONE", "") else raw_text,
        "description": description,
        "key_points":  key_points,
    }


# ─── 複数画像の一括分析 ──────────────────────────────────────────────────────

def extract_text_from_images(file_paths: list[str]) -> list[dict]:
    """複数画像をアップロード順に分析して結果リストを返す。

    Returns:
        [
            {
                "index": 1,
                "file": "image.jpg",
                "raw_text": "...",
                "description": "...",
                "key_points": [...],
            },
            ...
        ]
    """
    results = []
    for i, path in enumerate(file_paths, start=1):
        try:
            result = extract_text_from_image(path)
        except Exception as e:
            logger.warning(f"[Stage0] 画像{i}の分析失敗: {e}")
            result = {"raw_text": "", "description": "（分析失敗）", "key_points": []}

        result["index"] = i
        result["file"]  = os.path.basename(path)
        results.append(result)

    return results


# ─── 分析結果 → トピック・アウトライン生成 ───────────────────────────────────

def build_topic_from_images(
    extracted_results: list[dict],
    client_name: str = "",
    business_description: str = "",
    target_word_count: int = 0,
) -> dict:
    """画像分析結果から記事生成用のトピック・アウトラインを生成する。

    Returns:
        {
            "title":   str,  # 投稿タイトル（Stage1/2の topic に渡す）
            "outline": str,  # アウトライン（Stage1/2の keywords/outline に渡す）
            "ig_base": str,  # IGキャプション下書きベース（任意参照用）
        }
    """
    import anthropic
    from config import Config

    api_key = Config.ANTHROPIC_API_KEY
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY が設定されていません")

    images_summary = _format_results_for_prompt(extracted_results)
    word_note = f"目標文字数: {target_word_count}文字" if target_word_count else "文字数は投稿として適切に"

    prompt = f"""あなたはInstagram投稿・ブログ記事のコンテンツプランナーです。
以下の画像分析結果をもとに、記事生成に使うトピックとアウトラインを作成してください。

企業名: {client_name or "（未設定）"}
事業内容: {business_description or "（未設定）"}
{word_note}

【画像から抽出した情報】
{images_summary}

以下のJSON形式のみで出力してください（説明文不要）:
{{
  "title": "投稿のメインテーマ・タイトル（20〜40文字の日本語）",
  "outline": "記事のアウトライン。各画像の情報を活かした構成と要点を箇条書きで記述",
  "ig_base": "Instagramキャプションの下書きベース（画像内容を反映した自然な投稿文・200文字程度）"
}}"""

    client_obj = anthropic.Anthropic(api_key=api_key)
    message = client_obj.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()

    try:
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            data = json.loads(m.group())
            return {
                "title":   data.get("title", ""),
                "outline": data.get("outline", ""),
                "ig_base": data.get("ig_base", ""),
            }
    except Exception as e:
        logger.warning(f"[Stage0] トピック生成JSON解析失敗: {e}\nraw={raw[:200]}")

    return {"title": "", "outline": "", "ig_base": ""}


# ─── ユーティリティ ───────────────────────────────────────────────────────────

def _format_results_for_prompt(results: list[dict]) -> str:
    """画像分析結果リストをプロンプト用テキストに変換する。"""
    lines = []
    for r in results:
        lines.append(f"\n【画像{r['index']}】")
        if r.get("raw_text"):
            lines.append(f"テキスト: {r['raw_text']}")
        lines.append(f"内容: {r.get('description', '')}")
        if r.get("key_points"):
            lines.append("要点:")
            lines.extend(f"・{p}" for p in r["key_points"])
    return "\n".join(lines)


def format_extraction_summary(results: list[dict]) -> str:
    """画像分析結果を人間が読みやすいサマリーに変換する（UI表示用）。"""
    return _format_results_for_prompt(results)
