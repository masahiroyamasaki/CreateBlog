"""ai_image_gen.py — Claude でプロンプト生成 → Gemini で画像生成"""
import base64
import io
import os
import re
import uuid
import logging

import requests as _requests

logger = logging.getLogger(__name__)

_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
_IMAGEN_MODEL = "gemini-2.5-flash-image"

# Claude が失敗したときのフォールバック用
_TASTE_HINTS = {
    "business_clean": "clean professional business style, white and light gray tones, corporate minimalist, high contrast",
    "photo_real":     "realistic photographic style, natural lighting, vivid sharp details, professional camera shot",
    "illustration_pop": "bright colorful illustration, vector art, cheerful flat design with playful elements",
    "minimal":        "minimalist design, generous white space, simple geometric, monochrome with one accent color",
    "japanese_calm":  "Japanese aesthetic, calm design, earthy tones, matcha green and indigo blue, zen-inspired",
    "colorful_energy":"vibrant design, bold primary colors, energetic composition, eye-catching gradient, high saturation",
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

_NEGATIVE_PROMPT = (
    "text, watermark, extra limbs, inconsistent character design, "
    "photorealistic, low quality"
)


def _generate_prompt_with_claude(title: str, body_html: str, taste: str,
                                  balance: str, aspect_ratio: str,
                                  client_name: str = "",
                                  base_prompt: str = "") -> str:
    """Claude API（Haiku）で記事内容と企業設定から詳細な英語画像プロンプトを生成する。"""
    import anthropic
    from config import Config

    api_key = Config.ANTHROPIC_API_KEY
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY が設定されていません")

    body_text = _strip_html(body_html or "")[:1500]
    effective_base = (base_prompt.strip() if base_prompt and base_prompt.strip()
                      else _DEFAULT_BASE_PROMPT)

    user_msg = f"""あなたはAI画像生成の専門家です。
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

## 埋めるべき項目
- 主題: 記事の内容を象徴する被写体・情景。ベースキャラクター（若い女性）の配置も含めること
- 構図: アングル・俯瞰/クローズアップ・配置
- スタイル補足: ベースのflat vector illustrationを踏襲しつつ追加補足があれば
- 照明: 自然光/逆光/スタジオライティングなど
- 色調補足: ベースのwarm pastel paletteを踏襲しつつセクション固有の差分があれば
- 雰囲気: 明るい/落ち着いた/活力あるなど

## 出力フォーマット（必ずこの形式で出力）
"{effective_base}, [主題], [構図], [スタイル補足], [照明], [色調補足], [雰囲気], high quality, 4k, no text, no letters, no words, no japanese characters"

## 禁止事項（Negative prompt に相当）
{_NEGATIVE_PROMPT}
※上記の要素が出力プロンプトに含まれないよう注意すること

## 出力ルール
- 英語のみで出力すること
- ベースプロンプトを必ず先頭に含めること（変更・省略禁止）
- ダブルクォーテーションで囲んだプロンプト文のみを出力すること（説明文・日本語不要）
- キャラクターデザイン・配色・スタイルの一貫性を保つこと"""

    client_obj = anthropic.Anthropic(api_key=api_key)
    message = client_obj.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = message.content[0].text.strip()
    # ダブルクォーテーションがあれば中身だけ取り出す
    if raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1]
    logger.info(f"[Claude] 生成プロンプト: {raw[:120]}...")
    return raw


_TEXT_REQUEST_KEYWORDS = [
    "タイトル", "文字", "テキスト", "title", "text", "letter", "words",
    "書いて", "入れて", "追加して", "載せて", "表示して",
]


def _wants_text_in_image(instruction: str) -> bool:
    """修正指示がテキスト描画を要求しているか判定する。"""
    low = instruction.lower()
    return any(kw in low for kw in _TEXT_REQUEST_KEYWORDS)


def _enhance_refinement_with_claude(instruction: str, title: str = "",
                                     body_html: str = "") -> str:
    """ユーザーの修正指示＋ブログ記事情報を詳細な英語画像編集プロンプトに変換する。"""
    import anthropic
    from config import Config

    api_key = Config.ANTHROPIC_API_KEY
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY が設定されていません")

    body_text = _strip_html(body_html or "")[:1000]
    wants_text = _wants_text_in_image(instruction)

    if wants_text:
        text_rule = (
            "- ユーザーがタイトルや文字を画像に入れるよう指示している場合は、"
            "その文字列を正確に英語で画像に含めること\n"
            f"- ブログタイトルは「{title}」。英語で画像内に描写すること\n"
            "- 「no text」「no letters」は絶対に含めないこと"
        )
    else:
        text_rule = (
            '- 必ず "no text, no letters, no words, no japanese characters" を含めること\n'
            "- 文字・ロゴ・記号は一切含めないこと"
        )

    user_msg = f"""あなたはAI画像生成の専門家です。
以下のブログ記事と元画像に対して、ユーザーの修正指示を反映した新しい画像を生成するためのプロンプトを作成してください。

## ブログ記事情報
タイトル: {title or "（未設定）"}
本文抜粋:
{body_text or "（本文なし）"}

## ユーザーの修正指示
{instruction}

## 出力ルール
- 英語のみで出力すること
{text_rule}
- 修正指示の変更点を具体的に記述すること（色・構図・被写体・雰囲気など）
- ブログ記事の内容と一貫性を保つこと
- 元の画像の良い部分を活かしながら指示を正確に反映すること
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


# ─── Gemini 画像生成・修正 ───────────────────────────────────────────────────

def generate_image(title: str, taste: str, aspect_ratio: str, client_id: int,
                   balance: str = "balanced", body_html: str = "",
                   client_name: str = "", base_prompt: str = "") -> str:
    """Claude でプロンプトを生成し、Gemini で画像を生成して保存する。

    Returns:
        保存された画像の絶対 URL
    """
    from config import Config

    gemini_key = Config.GEMINI_API_KEY
    if not gemini_key:
        raise ValueError("GEMINI_API_KEY が設定されていません")

    # Step 1: Claude でプロンプト生成（失敗時はフォールバック）
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
        prompt = (
            f"professional blog article thumbnail, topic: {title}, "
            f"{taste_hint}, {balance_hint}, {aspect_hint}, "
            f"modern high quality, no text, no letters, no japanese characters"
        )

    # Step 2: Gemini で画像生成
    return _call_gemini_generate(prompt, client_id, gemini_key)


def refine_image(original_url: str, instruction: str, client_id: int,
                 title: str = "", body_html: str = "") -> str:
    """Claude で修正プロンプトを生成し、元画像を参照して Gemini で再生成する。

    テイスト等の企業スタイル設定は使わず、修正指示とブログ記事内容のみを使用する。

    Returns:
        保存された新しい画像の絶対 URL
    """
    from config import Config

    gemini_key = Config.GEMINI_API_KEY
    if not gemini_key:
        raise ValueError("GEMINI_API_KEY が設定されていません")

    # Step 1: Claude で修正プロンプトを強化（ブログ内容を参照）
    try:
        enhanced_prompt = _enhance_refinement_with_claude(
            instruction=instruction,
            title=title,
            body_html=body_html,
        )
    except Exception as e:
        logger.warning(f"[Claude] 修正プロンプト強化失敗、フォールバック: {e}")
        enhanced_prompt = (
            f"Edit this image: {instruction}. "
            f"Blog article title: {title}. "
            f"Maintain professional blog thumbnail style. "
            f"No text, no letters, no words, no japanese characters."
        )

    # Step 2: 元画像を読み込んで Gemini で編集生成
    img_bytes = _load_image_from_url(original_url)
    b64_original = base64.b64encode(img_bytes).decode()

    return _call_gemini_refine(enhanced_prompt, b64_original, client_id, gemini_key)


# ─── Gemini API 呼び出し共通処理 ────────────────────────────────────────────

def _call_gemini_generate(prompt: str, client_id: int, api_key: str) -> str:
    resp = _requests.post(
        f"{_GEMINI_BASE}/models/{_IMAGEN_MODEL}:generateContent",
        params={"key": api_key},
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["IMAGE"]},
        },
        timeout=120,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini API エラー (HTTP {resp.status_code}): {resp.text[:300]}")
    return _save_gemini_response(resp.json(), client_id)


def _call_gemini_refine(prompt: str, b64_original: str,
                        client_id: int, api_key: str) -> str:
    resp = _requests.post(
        f"{_GEMINI_BASE}/models/{_IMAGEN_MODEL}:generateContent",
        params={"key": api_key},
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inlineData": {"mimeType": "image/png", "data": b64_original}},
                ]
            }],
            "generationConfig": {"responseModalities": ["IMAGE"]},
        },
        timeout=120,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini API エラー (HTTP {resp.status_code}): {resp.text[:300]}")
    return _save_gemini_response(resp.json(), client_id)


def _save_gemini_response(data: dict, client_id: int) -> str:
    try:
        parts = data["candidates"][0]["content"]["parts"]
        inline = next(p["inlineData"] for p in parts if "inlineData" in p)
        b64 = inline["data"]
        mime = inline.get("mimeType", "image/png")
    except (KeyError, IndexError, StopIteration) as e:
        raise RuntimeError(f"Gemini レスポンス解析エラー: {e} / {str(data)[:300]}")

    img_bytes = base64.b64decode(b64)
    ext = "jpg" if "jpeg" in mime else "png"
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


def _load_image_from_url(image_url: str) -> bytes:
    base_url = os.getenv("BASE_URL", "").rstrip("/")
    base_dir = os.path.dirname(os.path.abspath(__file__))

    if base_url and image_url.startswith(base_url):
        relative = image_url[len(base_url):]
    elif image_url.startswith("/"):
        relative = image_url
    else:
        relative = None

    if relative:
        fs_path = os.path.join(base_dir, relative.lstrip("/").replace("/", os.sep))
        if os.path.exists(fs_path):
            with open(fs_path, "rb") as f:
                return f.read()

    r = _requests.get(image_url, timeout=30)
    r.raise_for_status()
    return r.content


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
