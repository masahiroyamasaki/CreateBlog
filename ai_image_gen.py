"""ai_image_gen.py — Google Gemini Imagen 3 による記事アイキャッチ画像生成"""
import base64
import io
import os
import uuid
import logging

import requests as _requests

logger = logging.getLogger(__name__)

_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
_IMAGEN_MODEL = "gemini-2.5-flash-image"

_TASTE_HINTS = {
    "business_clean": (
        "clean professional business style, white and light gray tones, "
        "corporate minimalist design, high contrast, sharp modern aesthetic"
    ),
    "photo_real": (
        "realistic photographic style, high quality photography, "
        "natural lighting, vivid sharp details, professional camera shot"
    ),
    "illustration_pop": (
        "bright colorful illustration style, vector art, cheerful and friendly design, "
        "vibrant colors, flat design with playful elements"
    ),
    "minimal": (
        "minimalist design, generous white space, simple geometric shapes, "
        "monochrome with one accent color, clean and elegant layout"
    ),
    "japanese_calm": (
        "Japanese aesthetic, calm and serene design, natural earthy tones, "
        "matcha green, indigo blue, washi paper texture, zen-inspired composition"
    ),
    "colorful_energy": (
        "vibrant colorful design, bold primary colors, energetic dynamic composition, "
        "eye-catching gradient, high saturation, striking visual impact"
    ),
}

_BALANCE_HINTS = {
    "balanced": (
        "balanced composition with blank space reserved for text overlay, "
        "image and text area coexist harmoniously"
    ),
    "image_focus": (
        "full bleed image, no text area, pure visual composition, "
        "graphic or photographic art filling the entire frame"
    ),
    "text_focus": (
        "infographic style, prominent area for text content, "
        "clean background suitable for displaying information, data visualization layout"
    ),
}

_ASPECT_HINTS = {
    "1:1":  "square format 1:1 aspect ratio",
    "4:5":  "portrait format 4:5 aspect ratio",
    "16:9": "widescreen landscape format 16:9 aspect ratio",
}


def generate_image(title: str, taste: str, aspect_ratio: str, client_id: int,
                   balance: str = "balanced") -> str:
    """Google Gemini Imagen 3 で画像を生成して
    /static/uploads/companies/{client_id}/images/ に保存する。

    Returns:
        保存された画像の web 相対パス（例: /static/uploads/companies/1/images/abc.jpg）
    """
    from config import Config

    api_key = Config.GEMINI_API_KEY
    if not api_key:
        raise ValueError("GEMINI_API_KEY が設定されていません")

    taste_hint = _TASTE_HINTS.get(taste, _TASTE_HINTS["business_clean"])
    balance_hint = _BALANCE_HINTS.get(balance, _BALANCE_HINTS["balanced"])
    aspect_hint = _ASPECT_HINTS.get(aspect_ratio, _ASPECT_HINTS["1:1"])

    prompt = (
        f"professional blog article thumbnail, topic: {title}, "
        f"{taste_hint}, "
        f"{balance_hint}, "
        f"{aspect_hint}, "
        f"modern high quality, business blog header image, "
        f"no text, no letters, no japanese characters"
    )

    resp = _requests.post(
        f"{_GEMINI_BASE}/models/{_IMAGEN_MODEL}:generateContent",
        params={"key": api_key},
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
            },
        },
        timeout=120,
    )

    if resp.status_code != 200:
        raise RuntimeError(
            f"Gemini Imagen API エラー (HTTP {resp.status_code}): {resp.text[:300]}"
        )

    data = resp.json()
    try:
        parts = data["candidates"][0]["content"]["parts"]
        inline = next(p["inlineData"] for p in parts if "inlineData" in p)
        b64 = inline["data"]
        mime = inline.get("mimeType", "image/png")
    except (KeyError, IndexError, StopIteration) as e:
        raise RuntimeError(f"Gemini API レスポンス解析エラー: {e} / {str(data)[:300]}")

    img_bytes = base64.b64decode(b64)
    ext = "jpg" if "jpeg" in mime else "png"

    compressed = _compress_to_5mb(img_bytes, ext)

    base_dir = os.path.dirname(os.path.abspath(__file__))
    save_dir = os.path.join(
        base_dir, "static", "uploads", "companies", str(client_id), "images"
    )
    os.makedirs(save_dir, exist_ok=True)

    filename = f"{uuid.uuid4().hex}.{ext}"
    save_path = os.path.join(save_dir, filename)
    with open(save_path, "wb") as f:
        f.write(compressed)

    logger.info(f"AI画像生成完了: {save_path}")
    return f"/static/uploads/companies/{client_id}/images/{filename}"


def _compress_to_5mb(data: bytes, ext: str = "png",
                     max_bytes: int = 5 * 1024 * 1024) -> bytes:
    """画像を 5MB 以下に圧縮して返す。"""
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
