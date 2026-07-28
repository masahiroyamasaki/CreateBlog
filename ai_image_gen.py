"""ai_image_gen.py — Claude でプロンプト生成 → DALL-E 3 で画像生成"""
import base64
import io
import os
import re
import uuid
import logging

import requests as _requests

logger = logging.getLogger(__name__)

# アスペクト比 → gpt-image-1 サイズマッピング
_ASPECT_TO_SIZE = {
    "1:1":  "1024x1024",
    "4:5":  "1024x1536",
    "16:9": "1536x1024",
}

# Claude 失敗時フォールバック用
_TASTE_HINTS = {
    "business_clean":   "clean professional business style, white and light gray tones, corporate minimalist",
    "photo_real":       "realistic photographic style, natural lighting, vivid sharp details",
    "illustration_pop": "bright colorful illustration, vector art, cheerful flat design",
    "minimal":          "minimalist design, generous white space, simple geometric, monochrome with one accent color",
    "japanese_calm":    "Japanese aesthetic, calm design, earthy tones, matcha green and indigo blue, zen-inspired",
    "colorful_energy":  "vibrant design, bold primary colors, energetic composition, high saturation",
}
_BALANCE_HINTS = {
    "balanced":    "balanced composition with blank space for text overlay",
    "image_focus": "full bleed image, pure visual, no empty space",
    "text_focus":  "clean background suitable for text overlay, infographic style",
}
_ASPECT_HINTS = {
    "1:1":  "square 1:1 format",
    "4:5":  "portrait 4:5 format",
    "16:9": "widescreen 16:9 landscape format",
}

_TASTE_LABELS = {
    "business_clean":   "ビジネス・清潔感（白・グレー基調、プロフェッショナルでシンプル）",
    "photo_real":       "写真・リアル（本格的な写真スタイル、自然な光、鮮明）",
    "illustration_pop": "イラスト・ポップ（明るいカラフルなイラスト、フラットデザイン）",
    "minimal":          "ミニマル（余白多め、シンプルな幾何学、モノクロ＋アクセントカラー）",
    "japanese_calm":    "和風・落ち着き（アース・緑・藍色、禅、和紙テクスチャ）",
    "colorful_energy":  "カラフル・元気（鮮やかなグラデーション、高彩度、インパクト重視）",
}
_BALANCE_LABELS = {
    "balanced":    "テキスト余白を確保したバランス型構図",
    "image_focus": "画像フル活用・テキストエリアなし",
    "text_focus":  "テキスト配置しやすいクリーンな背景",
}
_ASPECT_LABELS = {
    "1:1":  "正方形 1:1（Instagramフィード・ブログサムネイル）",
    "4:5":  "縦長 4:5（Instagramポートレート）",
    "16:9": "横長 16:9（WordPressヘッダー・OGP）",
}


# ─── Claude によるプロンプト生成 ──────────────────────────────────────────────

_DEFAULT_BASE_PROMPT = (
    "flat vector illustration style, warm pastel palette, "
    "consistent character design of a young professional woman with short black hair"
)

_NEGATIVE_GUIDANCE = (
    "Avoid: text, watermark, extra limbs, inconsistent character design, "
    "photorealistic rendering, low quality."
)


def _generate_prompt_with_claude(title: str, body_html: str, taste: str,
                                  balance: str, aspect_ratio: str,
                                  client_name: str = "",
                                  base_prompt: str = "") -> str:
    """Claude Haiku で記事内容と企業設定から詳細な英語画像プロンプトを生成する。"""
    import anthropic
    from config import Config

    api_key = Config.ANTHROPIC_API_KEY
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY が設定されていません")

    body_text = _strip_html(body_html or "")[:1500]
    effective_base = (base_prompt.strip() if base_prompt and base_prompt.strip()
                      else _DEFAULT_BASE_PROMPT)

    if balance == "text_focus":
        text_section = f"""
## テキスト描写指示（最重要）
記事タイトル「{title}」を画像内に美しく配置すること。
【必須制約】
- テキストは画像の全四辺から必ず10%以上内側に収めること（絶対に端で切れないこと）
- フォントサイズは全文字が画像内に完全に収まる大きさに調整すること
- タイトルが長い場合は2〜3行に折り返してよい（行ごとに収まるサイズで）
- テキストが1文字でもはみ出たり途切れたりしてはならない
- 日本語タイトルはそのまま日本語で、または英語に意訳して配置してよい"""
        output_suffix = (
            f'title text "{title}" fully contained within image boundaries with generous safe margins from all edges, '
            f'complete text fully visible without any cropping or cutoff, '
            f'font size adjusted small enough so entire title fits within the frame, '
            f'text placed in center or lower third area, high quality'
        )
        no_text_rule = f'- タイトル「{title}」を画像内に描写するため「no text」「no letters」は含めないこと'
    else:
        text_section = ""
        output_suffix = "high quality, no text, no letters, no words, no japanese characters"
        no_text_rule = '- 必ず "no text, no letters, no words, no japanese characters" を含めること'

    user_msg = f"""あなたはAI画像生成（DALL-E 3）のプロンプト専門家です。
以下の記事情報と企業設定をもとに、ブログ記事のサムネイル画像を生成するための英語プロンプトを作成してください。

## ベースプロンプト（必ず先頭に付加すること・変更禁止）
{effective_base}

## 記事情報
企業名: {client_name or "（未設定）"}
タイトル: {title}
本文抜粋:
{body_text or "（本文なし）"}

## 画像スタイル設定
- テイスト: {_TASTE_LABELS.get(taste, taste)}
- レイアウト: {_BALANCE_LABELS.get(balance, balance)}
- アスペクト比: {_ASPECT_LABELS.get(aspect_ratio, aspect_ratio)}
{text_section}
## 埋めるべき項目
- 主題: 記事の内容を象徴する被写体・情景。ベースキャラクター（若い女性）の配置も含めること
- 構図: アングル・俯瞰/クローズアップ・配置
- スタイル補足: ベースを踏襲しつつ追加補足があれば
- 照明: 自然光/逆光/スタジオライティングなど
- 色調補足: ベースのwarm pastel paletteを踏襲しつつ差分があれば
- 雰囲気: 明るい/落ち着いた/活力あるなど

## 出力フォーマット（必ずこの形式で出力）
"{effective_base}, [主題], [構図], [スタイル補足], [照明], [色調補足], [雰囲気], {output_suffix}"

## 注意事項
{_NEGATIVE_GUIDANCE}

## 出力ルール
- 英語のみで出力すること
- ベースプロンプトを必ず先頭に含めること（変更・省略禁止）
- ダブルクォーテーションで囲んだプロンプト文のみを出力すること（説明文・日本語不要）
- キャラクターデザイン・配色・スタイルの一貫性を保つこと
- DALL-E 3 向けに自然な英語の描写文として書くこと（カンマ区切りタグではなく文章調）
{no_text_rule}"""

    client_obj = anthropic.Anthropic(api_key=api_key)
    message = client_obj.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = message.content[0].text.strip()
    if raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1]
    logger.info(f"[Claude] 生成プロンプト: {raw[:120]}...")
    return raw


_TEXT_REQUEST_KEYWORDS = [
    "タイトル", "文字", "テキスト", "title", "text", "letter", "words",
    "書いて", "入れて", "追加して", "載せて", "表示して",
]

_TEXT_REMOVE_KEYWORDS = [
    "テキストはいらない", "文字はいらない", "テキストいらない", "文字いらない",
    "テキストを消して", "文字を消して", "テキスト消して", "文字消して",
    "テキストを外して", "文字を外して", "テキストを削除", "文字を削除",
    "テキスト不要", "文字不要", "テキストなし", "文字なし",
    "remove text", "without text", "no text",
]


def _wants_text_in_image(instruction: str) -> bool:
    low = instruction.lower()
    return any(kw in low for kw in _TEXT_REQUEST_KEYWORDS)


def _wants_remove_text(instruction: str) -> bool:
    return any(kw in instruction for kw in _TEXT_REMOVE_KEYWORDS)


def _enhance_refinement_with_claude(instruction: str, title: str = "",
                                     body_html: str = "") -> str:
    """修正指示＋ブログ記事情報を gpt-image-1 edit 向け英語プロンプトに変換する。"""
    import anthropic
    from config import Config

    api_key = Config.ANTHROPIC_API_KEY
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY が設定されていません")

    body_text = _strip_html(body_html or "")[:1000]
    wants_text = _wants_text_in_image(instruction)
    wants_remove = _wants_remove_text(instruction)

    if wants_text and not wants_remove:
        text_rule = (
            f"- ユーザーが文字・タイトルを画像に入れるよう指示している。"
            f"ブログタイトル「{title}」を画像内に自然に描写すること\n"
            "- 「no text」「no letters」は含めないこと"
        )
    elif wants_remove:
        text_rule = (
            "- ユーザーが画像からテキスト・文字をすべて取り除くよう指示している\n"
            '- "no text, no letters, no words, no japanese characters" を含めること\n'
            "- テキストのない純粋なビジュアルのみの画像にすること"
        )
    else:
        text_rule = (
            '- "no text, no letters, no words, no japanese characters" を含めること'
        )

    user_msg = f"""あなたはAI画像編集（gpt-image-1）のプロンプト専門家です。
元の画像に対してユーザーの指示を反映した修正プロンプトを英語で作成してください。

## ブログ記事情報
タイトル: {title or "（未設定）"}
本文抜粋:
{body_text or "（本文なし）"}

## ユーザーの修正指示（日本語）
{instruction}

## 重要: 元の画像を最大限保持すること
- 元の画像のスタイル・キャラクターデザイン・全体的な構図・配色・雰囲気を可能な限り保持すること
- ユーザーが指示した箇所・要素のみを変更すること
- 「そのまま」「変えないで」「画像はそのまま」などの指示は、現在の状態をほぼ維持した上で最小限の変更のみ加えること

## 出力ルール
- 英語のみで出力すること
{text_rule}
- "Edit this image: [変更内容のみ]. Keep the original [保持する要素] unchanged." の形式で書くこと
- 変更点と保持する要素を明確に分けて記述すること
- 60〜120語の英語プロンプトのみを出力すること（説明文・日本語不要）"""

    client_obj = anthropic.Anthropic(api_key=api_key)
    message = client_obj.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[{"role": "user", "content": user_msg}],
    )
    prompt = message.content[0].text.strip()
    logger.info(f"[Claude] 修正プロンプト (text_request={wants_text}): {prompt[:120]}...")
    return prompt


# ─── DALL-E 3 画像生成 ────────────────────────────────────────────────────────

def generate_image(title: str, taste: str, aspect_ratio: str, client_id: int,
                   balance: str = "balanced", body_html: str = "",
                   client_name: str = "", base_prompt: str = "") -> str:
    """Claude でプロンプトを生成し、DALL-E 3 で画像を生成して保存する。"""
    from config import Config

    api_key = Config.OPENAI_API_KEY
    if not api_key:
        raise ValueError("OPENAI_API_KEY が設定されていません")

    try:
        prompt = _generate_prompt_with_claude(
            title=title,
            body_html=body_html,
            taste=taste,
            balance=balance,
            aspect_ratio=aspect_ratio,
            client_name=client_name,
            base_prompt=base_prompt,
        )
    except Exception as e:
        logger.warning(f"[Claude] プロンプト生成失敗、フォールバック使用: {e}")
        taste_hint   = _TASTE_HINTS.get(taste, _TASTE_HINTS["business_clean"])
        balance_hint = _BALANCE_HINTS.get(balance, _BALANCE_HINTS["balanced"])
        aspect_hint  = _ASPECT_HINTS.get(aspect_ratio, _ASPECT_HINTS["1:1"])
        if balance == "text_focus":
            prompt = (
                f"Professional blog article thumbnail about: {title}. "
                f"{taste_hint}, {balance_hint}, {aspect_hint}, "
                f'title text "{title}" fully contained within image boundaries with generous safe margins, '
                f"complete text fully visible without any cropping or cutoff, "
                f"font size adjusted so entire title fits within frame, high quality."
            )
        else:
            prompt = (
                f"Professional blog article thumbnail about: {title}. "
                f"{taste_hint}, {balance_hint}, {aspect_hint}, "
                f"high quality, no text, no letters, no japanese characters."
            )

    size = _ASPECT_TO_SIZE.get(aspect_ratio, "1024x1024")
    return _call_dalle(prompt, size, client_id, api_key)


def refine_image(original_url: str, instruction: str, client_id: int,
                 title: str = "", body_html: str = "") -> str:
    """元画像を gpt-image-1 edit API に渡してimage-to-image修正を行う。"""
    from config import Config

    api_key = Config.OPENAI_API_KEY
    if not api_key:
        raise ValueError("OPENAI_API_KEY が設定されていません")

    # 元画像をダウンロード
    original_bytes = None
    try:
        img_resp = _requests.get(original_url, timeout=30)
        if img_resp.status_code == 200:
            original_bytes = img_resp.content
            logger.info(f"元画像ダウンロード完了: {len(original_bytes)} bytes")
    except Exception as e:
        logger.warning(f"元画像ダウンロード失敗、新規生成にフォールバック: {e}")

    try:
        prompt = _enhance_refinement_with_claude(
            instruction=instruction,
            title=title,
            body_html=body_html,
        )
    except Exception as e:
        logger.warning(f"[Claude] 修正プロンプト強化失敗、フォールバック: {e}")
        wants_text = _wants_text_in_image(instruction)
        wants_remove = _wants_remove_text(instruction)
        no_text = "" if (wants_text and not wants_remove) else "no text, no letters, no words, no japanese characters."
        prompt = (
            f"Edit this image. Change: {instruction}. "
            f"Keep the overall style, character design, and composition. {no_text}"
        )

    size = "1024x1024"

    if original_bytes:
        try:
            return _call_dalle_edit(prompt, original_bytes, size, client_id, api_key)
        except Exception as e:
            logger.warning(f"画像編集API失敗、新規生成にフォールバック: {e}")

    return _call_dalle(prompt, size, client_id, api_key)


def _call_dalle(prompt: str, size: str, client_id: int, api_key: str) -> str:
    """gpt-image-1 API を呼び出して画像を生成・保存し、絶対 URL を返す。"""
    if len(prompt) > 4000:
        prompt = prompt[:4000]

    resp = _requests.post(
        "https://api.openai.com/v1/images/generations",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": "gpt-image-1",
            "prompt": prompt,
            "n": 1,
            "size": size,
            "quality": "high",
        },
        timeout=180,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"gpt-image-1 API エラー (HTTP {resp.status_code}): {resp.text[:300]}")

    data = resp.json()
    b64 = data["data"][0]["b64_json"]
    return _save_image(base64.b64decode(b64), "png", client_id)


def _call_dalle_edit(prompt: str, image_bytes: bytes, size: str, client_id: int, api_key: str) -> str:
    """gpt-image-1 の画像編集 API で元画像ベースの修正を行い、絶対 URL を返す。"""
    from PIL import Image

    if len(prompt) > 4000:
        prompt = prompt[:4000]

    # edit API は PNG RGBA・1024x1024 を推奨
    img = Image.open(io.BytesIO(image_bytes))
    img = img.convert("RGBA")
    if img.size != (1024, 1024):
        img = img.resize((1024, 1024), Image.LANCZOS)
    png_buf = io.BytesIO()
    img.save(png_buf, format="PNG")
    png_bytes = png_buf.getvalue()

    resp = _requests.post(
        "https://api.openai.com/v1/images/edits",
        headers={"Authorization": f"Bearer {api_key}"},
        files={"image": ("image.png", png_bytes, "image/png")},
        data={
            "model": "gpt-image-1",
            "prompt": prompt,
            "n": "1",
            "size": size,
            "quality": "high",
        },
        timeout=180,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"gpt-image-1 edit API エラー (HTTP {resp.status_code}): {resp.text[:300]}")

    data = resp.json()
    b64 = data["data"][0]["b64_json"]
    return _save_image(base64.b64decode(b64), "png", client_id)


def _save_image(img_bytes: bytes, ext: str, client_id: int) -> str:
    """画像バイト列をディスクに保存して絶対 URL を返す。"""
    compressed = _compress_to_5mb(img_bytes, ext)

    base_dir = os.path.dirname(os.path.abspath(__file__))
    save_dir = os.path.join(base_dir, "static", "uploads", "companies", str(client_id), "images")
    os.makedirs(save_dir, exist_ok=True)

    filename = f"{uuid.uuid4().hex}.{ext}"
    save_path = os.path.join(save_dir, filename)
    with open(save_path, "wb") as f:
        f.write(compressed)

    logger.info(f"AI画像保存完了: {save_path}")
    base_url = os.getenv("BASE_URL", "").rstrip("/")
    return f"{base_url}/static/uploads/companies/{client_id}/images/{filename}"


# ─── ユーティリティ ──────────────────────────────────────────────────────────

def _strip_html(html: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'&[a-zA-Z#0-9]+;', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _compress_to_5mb(data: bytes, ext: str = "png",
                     max_bytes: int = 5 * 1024 * 1024) -> bytes:
    if len(data) <= max_bytes:
        return data

    from PIL import Image
    img = Image.open(io.BytesIO(data))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    fmt = "JPEG"
    quality = 85
    while quality >= 30:
        buf = io.BytesIO()
        img.save(buf, format=fmt, quality=quality)
        if buf.tell() <= max_bytes:
            return buf.getvalue()
        quality -= 10

    w, h = img.size
    img = img.resize((w // 2, h // 2), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format=fmt, quality=70)
    return buf.getvalue()
