"""ai_image_gen.py — Claude でプロンプト生成 → gpt-image-1 で背景生成 → Playwright でテキスト合成"""
import base64
import io
import json
import os
import re
import uuid
import logging

import requests as _requests

logger = logging.getLogger(__name__)

# ── アスペクト比マッピング ──────────────────────────────────────────────────
_ASPECT_TO_SIZE = {
    "1:1":  "1024x1024",
    "4:5":  "1024x1536",   # 生成後に 4:5 クロップ
    "16:9": "1536x1024",   # 生成後に 16:9 クロップ
}
_ASPECT_TO_CROP = {
    "4:5":  (4, 5),
    "16:9": (16, 9),
}
_ASPECT_TO_PLAYWRIGHT_SIZE = {
    "1:1":  (1080, 1080),
    "4:5":  (1080, 1350),
    "16:9": (1920, 1080),
}

# ── テイスト / バランス / アスペクトのラベル・ヒント ──────────────────────
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

# ── 除外指示（全生成に適用する必須ネガティブプロンプト） ─────────────────
_UNIVERSAL_NEGATIVES = (
    "no text, no typography, no logos, no watermarks, "
    "no human figures, no people, no faces, no hands, "
    "no unrequested objects, clean composition"
)

# ── ジャンル別ネガティブプロンプト ────────────────────────────────────────
_GENRE_NEGATIVE_MAP = [
    (["料理", "レシピ", "食材", "飲食", "グルメ"],
     "no cutlery close-up, no food labels, no menu text"),
    (["不動産", "物件", "マンション", "賃貸", "住宅"],
     "no signage text, no price tags, no address plaques"),
    (["美容", "コスメ", "スキンケア", "化粧", "ヘア"],
     "no product labels, no ingredient lists, no price stickers"),
    (["健康", "医療", "ダイエット", "病院", "クリニック"],
     "no medical charts, no prescription labels, no calorie counts"),
    (["IT", "テクノロジー", "プログラム", "エンジニア", "システム"],
     "no code screens, no terminal text, no error messages"),
    (["旅行", "観光", "ホテル", "海外", "トラベル"],
     "no travel brochure text, no price boards, no destination signs"),
    (["ビジネス", "投資", "副業", "起業", "経営"],
     "no financial charts with numbers, no contract text, no stock tickers"),
]

# ── レイアウト別の余白指示 ────────────────────────────────────────────────
_LAYOUT_SPACE_HINTS = {
    "top":    "leave clean, low-detail empty space in the upper 30% of the image",
    "bottom": "leave clean, low-detail empty space in the lower 30% of the image",
    "center": "keep a soft, low-detail area in the center for text overlay",
}

_DEFAULT_BASE_PROMPT = (
    "flat vector illustration style, warm pastel palette, "
    "no people, no persons, no human figures, no characters, no faces"
)
_NEGATIVE_GUIDANCE = (
    "Avoid: text, watermark, photorealistic rendering, low quality."
)


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


# ─── 画像読み込みユーティリティ ──────────────────────────────────────────────

def _load_image_blocks(paths: list) -> list:
    """ファイルパスリストから Claude 用画像コンテンツブロックを生成する。"""
    import base64 as _b64
    blocks = []
    ext_to_media = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
    for p in paths:
        if not p:
            continue
        try:
            import os as _os
            base_dir = _os.path.dirname(_os.path.abspath(__file__))
            abs_path = _os.path.join(base_dir, "static", p)
            if not _os.path.exists(abs_path):
                continue
            ext = _os.path.splitext(abs_path)[1].lower()
            media_type = ext_to_media.get(ext, "image/jpeg")
            with open(abs_path, "rb") as f:
                data = _b64.standard_b64encode(f.read()).decode("utf-8")
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}})
        except Exception as e:
            logger.warning(f"サンプル画像読み込み失敗: {p} — {e}")
    return blocks


def _load_sample_image_bytes(path: str) -> bytes | None:
    """static 相対パスから画像バイト列を返す。失敗時は None。"""
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        abs_path = os.path.join(base_dir, "static", path)
        if not os.path.exists(abs_path):
            return None
        with open(abs_path, "rb") as f:
            return f.read()
    except Exception as e:
        logger.warning(f"サンプル画像バイト読み込み失敗: {path} — {e}")
        return None


def _get_past_post_image_paths(client_id: int, limit: int = 3) -> list:
    """この企業の直近投稿画像のファイルパス（static 相対）を最新順で返す。"""
    try:
        from models import PostImage, Post
        imgs = (
            PostImage.query
            .join(Post, PostImage.post_id == Post.id)
            .filter(Post.client_id == client_id)
            .filter(PostImage.image_url.isnot(None))
            .order_by(PostImage.id.desc())
            .limit(limit)
            .all()
        )
        paths = []
        for img in imgs:
            url = (img.image_url or "").split("?")[0]
            if url.startswith("/static/"):
                paths.append(url[len("/static/"):])
            elif url.startswith("uploads/"):
                paths.append(url)
        logger.info(f"[過去投稿画像] client_id={client_id}: {len(paths)} 件取得")
        return paths
    except Exception as e:
        logger.warning(f"過去投稿画像取得失敗: {e}")
        return []


# ─── サンプル画像分析 ────────────────────────────────────────────────────────

def _analyze_sample_images(image_paths: list) -> dict:
    """サンプル画像を Claude で事前分析し、背景・テキスト・デザイン情報を詳細に返す。"""
    import re as _re
    import anthropic
    from config import Config

    blocks = _load_image_blocks(image_paths)
    if not blocks:
        return {"has_text": False, "has_people": False, "description": ""}

    api_key = Config.ANTHROPIC_API_KEY
    if not api_key:
        return {"has_text": False, "has_people": False, "description": ""}

    analysis_prompt = """添付した画像を徹底的に分析してください。以下の各項目を正確に記述すること。

━━ 基本情報 ━━
HAS_TEXT: [YES または NO]
HAS_PEOPLE: [YES または NO]
OVERALL_STYLE: [例: ミニマル写真調 / フラットイラスト / リアル写真 / グラフィックデザインなど]
MOOD: [例: プロフェッショナル / 温かい / クール / 活発など]

━━ 背景の詳細 ━━
BG_TYPE: [例: 単色 / グラデーション / 写真 / イラスト / テクスチャ / パターンなど]
BG_COLOR: [背景の具体的な色。例: オフホワイト #F5F5F5 / 濃紺 / 淡いグレーなど]
BG_ELEMENTS: [背景に含まれる要素。例: 抽象的な曲線 / 幾何学模様 / 自然の風景 / なしなど]

━━ テキスト・文字の詳細（テキストがない場合は各項目を NONE と記載）━━
TEXT_CONTENT: [画像内に実際に書かれているテキストをそのまま書き起こす。なければ NONE]
TEXT_SIZE: [例: 見出し大（画像幅の40%以上）/ 中（20〜40%）/ 小（20%未満）など]
TEXT_FONT_STYLE: [例: サンセリフ太字 / セリフ細め / 手書き風 / 明朝体など]
TEXT_WEIGHT: [例: Bold / Regular / Light / ExtraBlack など]
TEXT_COLOR: [文字色。例: 白 / 黒 / 濃紺 / ゴールドなど]
TEXT_EFFECTS: [例: ドロップシャドウ / アウトライン / グラデーション / なしなど]
TEXT_POSITION: [例: 中央 / 左上 / 右下 / 下部中央 / 上部1/3など]
TEXT_ALIGNMENT: [例: 中央揃え / 左揃え / 右揃えなど]
TEXT_LINES: [行数。例: 1行 / 2行 / 3行以上など]
TEXT_AREA_RATIO: [テキストが画像全体に占める割合。例: 約10% / 約30% / 約50%など]

━━ 全体の配色 ━━
MAIN_COLORS: [主要な色を優先順に3〜5色。例: 白・濃紺・ゴールド・グレー]
ACCENT_COLORS: [アクセントカラー。なければ NONE]

━━ 構図・レイアウト ━━
LAYOUT_STRUCTURE: [例: テキスト中央・背景全面 / 左半分テキスト右半分画像 / テキスト上部・ビジュアル下部など]
VISUAL_WEIGHT: [視覚的な重心。例: 中央 / 左寄り / 上部など]

━━ 総合説明 ━━
DESCRIPTION: [上記を踏まえた画像全体の詳細な説明を3〜4文で。デザインの意図・印象まで含める]

各行を上記形式で出力すること（余分な説明文は不要）。"""

    content = blocks + [{"type": "text", "text": analysis_prompt}]

    try:
        client_obj = anthropic.Anthropic(api_key=api_key)
        message = client_obj.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1200,
            messages=[{"role": "user", "content": content}],
        )
        raw = message.content[0].text.strip()
        logger.info(f"[サンプル画像分析]\n{raw}")

        def _field(key):
            m = _re.search(rf'^{key}:\s*(.+)$', raw, _re.MULTILINE | _re.IGNORECASE)
            return m.group(1).strip() if m else ""

        has_text   = _field("HAS_TEXT").upper()   == "YES"
        has_people = _field("HAS_PEOPLE").upper() == "YES"

        return {
            "has_text":           has_text,
            "has_people":         has_people,
            "overall_style":      _field("OVERALL_STYLE"),
            "mood":               _field("MOOD"),
            "bg_type":            _field("BG_TYPE"),
            "bg_color":           _field("BG_COLOR"),
            "bg_elements":        _field("BG_ELEMENTS"),
            "text_content":       _field("TEXT_CONTENT"),
            "text_size":          _field("TEXT_SIZE"),
            "text_font_style":    _field("TEXT_FONT_STYLE"),
            "text_weight":        _field("TEXT_WEIGHT"),
            "text_color":         _field("TEXT_COLOR"),
            "text_effects":       _field("TEXT_EFFECTS"),
            "text_position":      _field("TEXT_POSITION"),
            "text_alignment":     _field("TEXT_ALIGNMENT"),
            "text_lines":         _field("TEXT_LINES"),
            "text_area_ratio":    _field("TEXT_AREA_RATIO"),
            "main_colors":        _field("MAIN_COLORS"),
            "accent_colors":      _field("ACCENT_COLORS"),
            "layout_structure":   _field("LAYOUT_STRUCTURE"),
            "visual_weight":      _field("VISUAL_WEIGHT"),
            "description":        raw,
        }
    except Exception as e:
        logger.warning(f"サンプル画像分析失敗: {e}")
        return {"has_text": False, "has_people": False, "description": ""}


# ─── ジャンル別ネガティブプロンプト ─────────────────────────────────────────

def _get_genre_extra_negatives(title: str, body_html: str) -> str:
    """記事のジャンルキーワードを判定し、追加のネガティブプロンプト文字列を返す。"""
    combined = title + " " + _strip_html(body_html or "")[:500]
    extras = []
    for keywords, negative in _GENRE_NEGATIVE_MAP:
        if any(kw in combined for kw in keywords):
            extras.append(negative)
    return ", ".join(extras)


# ─── Claude によるプロンプト・メタデータ生成 ──────────────────────────────────

def _generate_prompt_with_claude(title: str, body_html: str, taste: str,
                                  balance: str, aspect_ratio: str,
                                  client_name: str = "",
                                  base_prompt: str = "",
                                  sample_image_paths: list = None,
                                  past_image_paths: list = None) -> str:
    """Claude Haiku で記事内容と企業設定から詳細な英語画像プロンプトを生成する。

    優先度:
    1. sample_image_paths (企業設定サンプル画像) → 最優先。テイスト設定を完全に上書き。
    2. past_image_paths (過去投稿画像) → 補助参照。テイスト/バランス設定と併用。
    3. なし → テイスト/バランス/ベースプロンプトのみで生成。
    """
    import anthropic
    from config import Config

    api_key = Config.ANTHROPIC_API_KEY
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY が設定されていません")

    body_text = _strip_html(body_html or "")[:1500]
    raw_base = base_prompt.strip() if base_prompt and base_prompt.strip() else ""
    effective_base = raw_base if raw_base else _DEFAULT_BASE_PROMPT
    _has_japanese = bool(re.search(r'[぀-鿿]', effective_base))

    # ジャンル別ネガティブプロンプト
    genre_neg = _get_genre_extra_negatives(title, body_html)

    if balance == "text_focus":
        text_section = """
## テキストオーバーレイ対応（最重要）
この画像はPlaywrightで後からテキストを重ねるため、画像自体にはテキスト・文字を一切描画しないこと。
【必須制約】
- 画像内に文字・テキスト・数字・ロゴを含めないこと（英語・日本語・記号すべて禁止）
- 下部1/3エリアをやや暗め・シンプルに仕上げること（テキストが読みやすい領域を確保するため）
- 人物・人間・キャラクター・顔は一切含めないこと
- 抽象的・グラフィカルな背景のみで構成すること"""
        output_suffix = (
            "clean abstract background optimized for text overlay, "
            "lower third area with subtle darker tone or gradient for text readability, "
            "no people, no persons, no human figures, no characters, no faces, "
            "no text, no letters, no words, no numbers, no watermarks, "
            + (_UNIVERSAL_NEGATIVES + ", ") +
            ("" if not genre_neg else genre_neg + ", ") +
            "high quality"
        )
        no_text_rule = '- text_focusモードのため「no text, no letters, no words, no numbers」を必ず含めること（Playwrightでテキストを後から重ねるため画像自体には文字不要）'
    else:
        text_section = ""
        output_suffix = (
            "high quality, " + _UNIVERSAL_NEGATIVES +
            (", " + genre_neg if genre_neg else "")
        )
        no_text_rule = '- 必ず "no text, no letters, no words, no numbers, no japanese characters, no logos, no signs" を含めること（絶対省略禁止）'

    if _has_japanese:
        base_instruction = (
            f"## ベースプロンプト（日本語→英語に翻訳して必ず先頭に付加すること）\n"
            f"{effective_base}\n"
            f"※ 上記を自然な英語に翻訳し、出力プロンプトの先頭に組み込むこと（日本語をそのまま出力しないこと）"
        )
        output_format = '"[ベースプロンプトを英語訳したもの], [主題], [構図], [スタイル補足], [照明], [色調補足], [雰囲気], ' + output_suffix + '"'
    else:
        base_instruction = (
            f"## ベースプロンプト（必ず先頭に付加すること・変更禁止）\n"
            f"{effective_base}"
        )
        output_format = f'"{effective_base}, [主題], [構図], [スタイル補足], [照明], [色調補足], [雰囲気], {output_suffix}"'

    if balance == "text_focus":
        if raw_base:
            no_char_note = "※ 人物・キャラクターの記述は無視し、背景スタイルのみを参照すること"
            if _has_japanese:
                base_instruction = (
                    f"## ベースプロンプト（背景スタイルのみ参照・人物除外）\n"
                    f"{effective_base}\n"
                    f"※ 英語に翻訳すること。{no_char_note}"
                )
            else:
                base_instruction = (
                    f"## ベースプロンプト（背景スタイルのみ参照・人物除外）\n"
                    f"{effective_base}\n"
                    f"※ {no_char_note}"
                )
        else:
            base_instruction = (
                "## スタイル指定\n"
                "抽象的・グラフィカルな背景スタイルで生成すること。\n"
                "人物・キャラクター・顔・シルエットは一切含めないこと。\n"
                "テキストが映えるクリーンな背景デザインにすること。"
            )
        output_format = '"[人物なし・抽象グラフィック背景], [構図], [照明], [色調], [雰囲気], ' + output_suffix + '"'

    company_blocks = _load_image_blocks(sample_image_paths or [])
    past_blocks    = _load_image_blocks(past_image_paths or [])

    if balance == "text_focus":
        subject_item = "- 主題: テキストを配置しやすいクリーンな背景・抽象的なグラフィック構図。人物・キャラクター・顔は絶対に含めないこと"
    else:
        subject_item = "- 主題: 記事の内容を象徴する被写体・情景・抽象表現（人物・キャラクター・顔は含めないこと）"
    style_hint = "ベースを踏襲しつつ追加補足があれば"
    color_hint = "ベースのwarm pastel paletteを踏襲しつつ差分があれば"
    base_prompt_rule = "- ベースプロンプトを必ず先頭に含めること（変更・省略禁止）"
    sample_section = ""
    image_blocks = []

    sample_analysis = _analyze_sample_images(sample_image_paths or []) if company_blocks else {}
    has_text_in_sample   = sample_analysis.get("has_text", False)
    has_people_in_sample = sample_analysis.get("has_people", False)
    analysis_desc        = sample_analysis.get("description", "")

    allow_text_in_image   = False
    allow_people_in_image = False

    if company_blocks:
        image_blocks = company_blocks

        allow_text_in_image   = has_text_in_sample and balance != "text_focus"
        allow_people_in_image = has_people_in_sample and balance != "text_focus"

        a = sample_analysis
        bg_spec = f"背景タイプ: {a.get('bg_type','')}, 背景色: {a.get('bg_color','')}, 背景要素: {a.get('bg_elements','')}"

        if allow_text_in_image:
            text_spec = (
                f"文字サイズ: {a.get('text_size','')}\n"
                f"フォントスタイル: {a.get('text_font_style','')} / ウェイト: {a.get('text_weight','')}\n"
                f"文字色: {a.get('text_color','')} / エフェクト: {a.get('text_effects','')}\n"
                f"配置位置: {a.get('text_position','')} / 揃え: {a.get('text_alignment','')}\n"
                f"行数: {a.get('text_lines','')} / 占有率: {a.get('text_area_ratio','')}"
            )
            text_rule_line = (
                f"- ★テキスト: サンプルに文字あり → 記事タイトル「{title}」を以下のタイポグラフィ仕様で配置すること\n"
                f"  {text_spec}"
            )
        else:
            text_rule_line = "- ★テキスト: サンプルに文字なし → 生成画像にも文字・テキストは一切含めないこと"

        people_rule_line = (
            "- 人物・キャラクター: サンプルに人物あり → 同様のスタイルで人物を含めてよい"
            if allow_people_in_image else
            "- 人物・キャラクター: サンプルに人物なし → 生成画像にも人物・顔・キャラクターは含めないこと"
        )

        sample_section = f"""
## ★企業サンプル画像（スタイル絶対最優先）
添付した画像はこの企業が指定したビジュアルスタイルのサンプルです。
テイスト設定・ベースプロンプト・デフォルトスタイルはすべて無視し、以下の分析結果のみに従うこと。

【背景デザイン】
{bg_spec}

【配色】
メインカラー: {a.get('main_colors','')}
アクセントカラー: {a.get('accent_colors','')}

【構図・レイアウト】
レイアウト構造: {a.get('layout_structure','')}
視覚的重心: {a.get('visual_weight','')}

【全体スタイル・雰囲気】
スタイル: {a.get('overall_style','')}
雰囲気: {a.get('mood','')}

【生成ルール】
- 上記の背景・配色・構図・スタイルを忠実に再現すること
- ★ロゴ・ブランド固有の商標・記号は含めないこと
{text_rule_line}
{people_rule_line}
"""
        if balance == "text_focus":
            subject_item = "- 主題: テキストを配置しやすいクリーンな背景（企業サンプル画像の背景デザインを再現）。人物・顔は含めないこと"
        elif allow_people_in_image:
            subject_item = "- 主題: 記事の内容を象徴する情景（企業サンプル画像と同様に人物を含めること）"
        else:
            subject_item = "- 主題: 記事の内容を象徴する被写体・情景（企業サンプル画像スタイルで、人物なし）"

        style_hint       = f"企業サンプル画像のスタイル（{a.get('overall_style','サンプル参照')}）に合わせること"
        color_hint       = f"メインカラー: {a.get('main_colors','サンプル参照')} を忠実に再現すること"
        base_prompt_rule = "- 企業サンプル画像の分析結果のみに従うこと（ベースプロンプトは無視してよい）"

        base_instruction = (
            "## スタイル指定\n"
            "企業サンプル画像の分析結果のスタイルのみを参照すること。\n"
            "デフォルトの固定キャラクター（若い女性・フラットベクターイラスト等）は一切使用しないこと。"
        )

        if allow_text_in_image:
            _text_suffix = (
                f'title text "{title}" placed at {a.get("text_position","center")} '
                f'in {a.get("text_font_style","sans-serif")} {a.get("text_weight","bold")} style, '
                f'color {a.get("text_color","matching sample")}, '
                f'{a.get("text_effects","no effects")}, '
                f'high quality'
            )
            output_format = (
                '"[企業サンプル画像の背景・配色・レイアウトを再現した描写], [主題], [構図], [照明], [色調], [雰囲気], '
                + _text_suffix + '"'
            )
            no_text_rule = (
                f'- 記事タイトル「{title}」を上記タイポグラフィ仕様（位置: {a.get("text_position","center")}、'
                f'フォント: {a.get("text_font_style","sans-serif")} {a.get("text_weight","bold")}、'
                f'色: {a.get("text_color","サンプルに合わせる")}）で画像内に配置すること'
            )
        else:
            output_format = (
                '"[企業サンプル画像の背景・配色・レイアウトを再現した描写], [主題], [構図], [照明], [色調], [雰囲気], '
                + output_suffix + '"'
            )

    elif past_blocks:
        image_blocks = past_blocks
        sample_section = """
## 過去投稿画像（スタイル参考・テイスト設定と併用）
添付した画像はこの企業の直近の投稿画像です。テイスト設定に従いつつ、これらのスタイル・雰囲気も参考にすること。
- 過去画像の色調・配色・全体的な雰囲気・タッチを参考にすること
- テイスト設定（下記）が最優先だが、過去画像のスタイルと整合するよう努めること
- 過去画像内のテキスト・ロゴ・文字は生成画像に含めないこと
"""

    if allow_text_in_image:
        no_text_critical = ""
    elif balance == "text_focus":
        no_text_critical = ""
    else:
        no_text_critical = """
## ★絶対制約: 文字・テキスト禁止★
生成する画像には文字・テキスト・ロゴ・記号を一切含めてはならない。
プロンプトには必ず "no text, no letters, no words, no numbers, no japanese characters, no logos, no signs" を含めること。
"""

    people_avoid_note = (
        "Absolutely avoid: people, persons, human figures, faces, characters, portraits. Background and typography only."
        if balance == "text_focus" and not allow_people_in_image else ""
    )

    text_body = f"""あなたはAI画像生成（gpt-image-1）のプロンプト専門家です。
以下の記事情報と企業設定をもとに、ブログ記事のサムネイル画像を生成するための英語プロンプトを作成してください。
{no_text_critical}
{base_instruction}
{sample_section}
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
{subject_item}
- 構図: アングル・俯瞰/クローズアップ・配置
- スタイル補足: {style_hint}
- 照明: 自然光/逆光/スタジオライティングなど
- 色調補足: {color_hint}
- 雰囲気: 明るい/落ち着いた/活力あるなど

## 出力フォーマット（必ずこの形式で出力）
{output_format}

## 注意事項
{_NEGATIVE_GUIDANCE}
{people_avoid_note}

## 出力ルール
- 英語のみで出力すること
{base_prompt_rule}
- ダブルクォーテーションで囲んだプロンプト文のみを出力すること（説明文・日本語不要）
- gpt-image-1 向けに自然な英語の描写文として書くこと（カンマ区切りタグではなく文章調）
{no_text_rule}"""

    content = image_blocks + [{"type": "text", "text": text_body}] if image_blocks else text_body

    client_obj = anthropic.Anthropic(api_key=api_key)
    message = client_obj.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        messages=[{"role": "user", "content": content}],
    )
    raw = message.content[0].text.strip()
    if raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1]

    _no_people_str = "no people, no persons, no human figures, no characters, no faces"
    if not allow_people_in_image:
        if not any(kw in raw.lower() for kw in ["no people", "no person", "no human", "no character", "no face"]):
            raw = _no_people_str + ", " + raw
        if balance == "text_focus" and not raw.startswith(_no_people_str):
            raw = _no_people_str + ", " + raw

    _no_text_str = "no text, no letters, no words, no numbers, no japanese characters, no logos"
    if not allow_text_in_image or balance == "text_focus":
        if not any(kw in raw.lower() for kw in ["no text", "no letter", "no word", "no number", "no sign"]):
            raw += ", " + _no_text_str

    logger.info(f"[Claude] 生成プロンプト: {raw[:150]}...")
    return raw


def _generate_copy_metadata(
    title: str,
    body_html: str,
    taste: str,
    balance: str,
) -> dict:
    """Claude Haiku でキャッチコピー・レイアウトタイプ・トーンを生成する。

    Returns:
        {"catch_copy": str, "layout_type": str, "tone": str}
    """
    import anthropic
    from config import Config

    api_key = Config.ANTHROPIC_API_KEY
    if not api_key:
        return {"catch_copy": title[:20], "layout_type": "bottom", "tone": "professional"}

    body_text = _strip_html(body_html or "")[:300]

    prompt = f"""以下の記事情報をもとに、画像に重ねるキャッチコピーとレイアウト情報をJSONで生成してください。

記事タイトル: {title}
本文抜粋: {body_text}
テイスト: {_TASTE_LABELS.get(taste, taste)}
バランス設定: {_BALANCE_LABELS.get(balance, balance)}

出力（JSONのみ・説明文不要）:
{{
  "catch_copy": "記事の魅力を伝える20文字以内の日本語キャッチコピー",
  "layout_type": "center（画像中央）を基本とする。上部に重要ビジュアルがある場合のみ bottom、下部に重要ビジュアルがある場合のみ top",
  "tone": "bright または calm または professional のいずれか"
}}

※ layout_type は "top" / "bottom" / "center" のいずれかの文字列のみを出力してください。デフォルトは center です。"""

    try:
        client_obj = anthropic.Anthropic(api_key=api_key)
        message = client_obj.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        m = re.search(r'\{.*?\}', raw, re.DOTALL)
        if m:
            data = json.loads(m.group())
            layout = data.get("layout_type", "bottom")
            tone   = data.get("tone", "professional")
            return {
                "catch_copy":  str(data.get("catch_copy", title[:20]))[:20],
                "layout_type": layout  if layout  in ("top", "bottom", "center")       else "center",
                "tone":        tone    if tone     in ("bright", "calm", "professional") else "professional",
            }
    except Exception as e:
        logger.warning(f"[Claude] コピーメタデータ生成失敗: {e}")

    return {"catch_copy": title[:20], "layout_type": "center", "tone": "professional"}


def _generate_claude_metadata(
    title: str, body_html: str, taste: str,
    balance: str, aspect_ratio: str,
    client_name: str = "", base_prompt: str = "",
    sample_image_paths: list = None,
    past_image_paths: list = None,
) -> dict:
    """image_prompt / catch_copy / layout_type / tone を取得する。

    Returns:
        {
            "image_prompt": str,   # gpt-image-1 向け英語プロンプト
            "catch_copy":   str,   # 画像に重ねる日本語キャッチコピー（20文字以内）
            "layout_type":  str,   # "top" | "bottom" | "center"
            "tone":         str,   # "bright" | "calm" | "professional"
        }
    """
    # 背景画像プロンプト生成
    image_prompt = _generate_prompt_with_claude(
        title=title, body_html=body_html,
        taste=taste, balance=balance, aspect_ratio=aspect_ratio,
        client_name=client_name, base_prompt=base_prompt,
        sample_image_paths=sample_image_paths,
        past_image_paths=past_image_paths,
    )

    # キャッチコピー・レイアウト情報（text_focus 時のみ生成）
    if balance == "text_focus":
        copy_meta = _generate_copy_metadata(title, body_html, taste, balance)
    else:
        copy_meta = {"catch_copy": "", "layout_type": "bottom", "tone": "professional"}

    return {
        "image_prompt": image_prompt,
        "catch_copy":   copy_meta["catch_copy"],
        "layout_type":  copy_meta["layout_type"],
        "tone":         copy_meta["tone"],
    }


# ─── 修正指示処理（refine_image 用） ─────────────────────────────────────────

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


# ─── Playwright テキスト合成 ─────────────────────────────────────────────────

def _build_playwright_html(
    bg_image_bytes: bytes,
    catch_copy: str,
    layout_type: str,
    tone: str,
    width: int,
    height: int,
) -> str:
    """Playwright レンダリング用 HTML 文字列を生成する。"""
    import base64 as _b64

    bg_data = _b64.b64encode(bg_image_bytes).decode("utf-8")
    bg_uri = f"data:image/png;base64,{bg_data}"

    # トーン別スタイル: (band_bg, text_color, text_shadow)
    _tone_map = {
        "bright":       ("rgba(0,0,0,0.35)",  "#FFFFFF",  "2px 2px 6px rgba(0,0,0,0.7)"),
        "calm":         ("rgba(20,15,10,0.55)", "#FFF8E7", "2px 2px 10px rgba(0,0,0,0.85)"),
        "professional": ("rgba(0,0,0,0.65)",  "#FFFFFF",  "1px 1px 4px rgba(0,0,0,0.95)"),
    }
    band_bg, text_color, text_shadow = _tone_map.get(tone, _tone_map["professional"])

    # フォントサイズ（文字数ベース）
    n = len(catch_copy)
    if n <= 8:    fs = width // 10
    elif n <= 12: fs = width // 13
    elif n <= 16: fs = width // 16
    elif n <= 20: fs = width // 20
    else:         fs = width // 24

    pad   = max(24, height // 22)
    pad_x = max(40, width  // 14)

    # レイアウト別 CSS position
    _pos_map = {
        "top":    "top: 0; left: 0; right: 0;",
        "bottom": "bottom: 0; left: 0; right: 0;",
        "center": "top: 50%; left: 0; right: 0; transform: translateY(-50%);",
    }
    band_pos = _pos_map.get(layout_type, _pos_map["bottom"])

    import html as _html
    catch_copy_escaped = _html.escape(catch_copy)

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{ width: {width}px; height: {height}px; overflow: hidden; background: #000; }}
.canvas {{
  width: {width}px;
  height: {height}px;
  background: url('{bg_uri}') center / cover no-repeat;
  position: relative;
}}
.band {{
  position: absolute;
  {band_pos}
  padding: {pad}px {pad_x}px;
  background: {band_bg};
  display: flex;
  align-items: center;
  justify-content: center;
}}
.copy {{
  font-family: 'Noto Sans CJK JP', 'Noto Sans JP', 'Hiragino Kaku Gothic Pro', 'Hiragino Sans',
               'Yu Gothic', 'YuGothic', 'Meiryo', 'MS PGothic', sans-serif;
  font-size: {fs}px;
  font-weight: 900;
  color: {text_color};
  text-shadow: {text_shadow};
  text-align: center;
  line-height: 1.45;
  letter-spacing: 0.04em;
  max-width: {width - pad_x * 2}px;
  word-break: break-all;
}}
</style>
</head>
<body>
<div class="canvas">
  <div class="band">
    <span class="copy">{catch_copy_escaped}</span>
  </div>
</div>
</body>
</html>"""


def _compose_with_playwright(
    bg_image_bytes: bytes,
    catch_copy: str,
    layout_type: str,
    tone: str,
    aspect_ratio: str,
    client_id: int,
) -> bytes:
    """HTML/CSS + Playwright で背景画像にキャッチコピーを合成してPNG bytesを返す。"""
    import tempfile
    from pathlib import Path

    width, height = _ASPECT_TO_PLAYWRIGHT_SIZE.get(aspect_ratio, (1080, 1080))

    # 背景画像は base64 インラインで HTML に埋め込むため、tmpdir への書き出し不要
    html = _build_playwright_html(bg_image_bytes, catch_copy, layout_type, tone, width, height)

    with tempfile.TemporaryDirectory() as tmpdir:
        html_path = os.path.join(tmpdir, "card.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)

        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": width, "height": height})
                page.goto(Path(html_path).as_uri())
                page.wait_for_load_state("domcontentloaded", timeout=5000)
                screenshot = page.screenshot(type="png", full_page=False)
            finally:
                browser.close()

    logger.info(f"[Playwright] 合成完了: layout={layout_type}, tone={tone}, {width}x{height}, copy='{catch_copy}'")
    return screenshot


# ─── 画像検証 ────────────────────────────────────────────────────────────────

def _validate_image(img_bytes: bytes) -> bool:
    """生成画像の簡易チェック: 最低1KB以上かつPILで開けること。"""
    if len(img_bytes) < 1024:
        logger.warning(f"[検証] 画像サイズが小さすぎます: {len(img_bytes)} bytes")
        return False
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(img_bytes))
        img.verify()
        return True
    except Exception as e:
        logger.warning(f"[検証] 画像破損: {e}")
        return False


# ─── 画像処理ユーティリティ ──────────────────────────────────────────────────

def _crop_to_ratio(img_bytes: bytes, w_ratio: int, h_ratio: int) -> bytes:
    """生成画像をセンタークロップして正確なアスペクト比に調整する。"""
    from PIL import Image
    img = Image.open(io.BytesIO(img_bytes))
    w, h = img.size
    target = w_ratio / h_ratio
    current = w / h
    if abs(current - target) < 0.01:
        return img_bytes
    if current > target:
        new_w = int(h * target)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    else:
        new_h = int(w / target)
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    logger.info(f"画像クロップ完了: {w}x{h} → {img.size[0]}x{img.size[1]} ({w_ratio}:{h_ratio})")
    return buf.getvalue()


def _overlay_title_text(img_bytes: bytes, title: str) -> bytes:
    """PIL フォールバック: 画像下部にタイトルテキストを重ねて返す。"""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
    w, h = img.size

    font_candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Bold.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
        "C:/Windows/Fonts/meiryob.ttc",
        "C:/Windows/Fonts/YuGothB.ttc",
        "C:/Windows/Fonts/msgothic.ttc",
    ]

    font_size = max(28, w // 18)
    font = None
    for fp in font_candidates:
        if os.path.exists(fp):
            try:
                font = ImageFont.truetype(fp, font_size)
                break
            except Exception:
                continue
    if font is None:
        try:
            font = ImageFont.load_default(size=font_size)
        except Exception:
            font = ImageFont.load_default()

    margin_x = int(w * 0.06)
    max_text_w = w - margin_x * 2
    draw_dummy = ImageDraw.Draw(Image.new("RGBA", (1, 1)))

    def _text_width(t):
        try:
            return draw_dummy.textlength(t, font=font)
        except Exception:
            return len(t) * font_size * 0.6

    lines = []
    current = ""
    for ch in title:
        test = current + ch
        if _text_width(test) > max_text_w and current:
            lines.append(current)
            current = ch
        else:
            current = test
    if current:
        lines.append(current)

    line_h = int(font_size * 1.4)
    text_block_h = line_h * len(lines)
    pad = int(h * 0.03)
    overlay_h = text_block_h + pad * 2

    overlay_y = h - overlay_h
    overlay = Image.new("RGBA", (w, overlay_h), (0, 0, 0, 170))
    img.paste(overlay, (0, overlay_y), overlay)

    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        lw = _text_width(line)
        x = (w - lw) / 2
        y = overlay_y + pad + i * line_h
        draw.text((x + 2, y + 2), line, font=font, fill=(0, 0, 0, 200))
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    logger.info(f"[PIL fallback] テキストオーバーレイ完了: '{title}' ({len(lines)}行)")
    return buf.getvalue()


# ─── gpt-image-1 API 呼び出し ────────────────────────────────────────────────

def _call_dalle_bytes(prompt: str, size: str, api_key: str) -> bytes:
    """gpt-image-1 text-to-image を呼び出して画像バイト列を返す。"""
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
    return base64.b64decode(b64)


def _call_dalle_edit_bytes(prompt: str, image_bytes: bytes, size: str, api_key: str) -> bytes:
    """gpt-image-1 image-to-image 編集を呼び出して画像バイト列を返す。"""
    from PIL import Image

    if len(prompt) > 4000:
        prompt = prompt[:4000]

    # edit API は PNG RGBA 推奨
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
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
    return base64.b64decode(b64)


def _call_dalle(prompt: str, size: str, client_id: int, api_key: str,
                crop_ratio: tuple = None, overlay_title: str = None) -> str:
    """gpt-image-1 API を呼び出して画像を生成・保存し、絶対 URL を返す（後方互換）。"""
    img_bytes = _call_dalle_bytes(prompt, size, api_key)
    if crop_ratio:
        try:
            img_bytes = _crop_to_ratio(img_bytes, *crop_ratio)
        except Exception as e:
            logger.warning(f"クロップ失敗、元画像を使用: {e}")
    if overlay_title:
        try:
            img_bytes = _overlay_title_text(img_bytes, overlay_title)
        except Exception as e:
            logger.warning(f"テキストオーバーレイ失敗、元画像を使用: {e}")
    return _save_image(img_bytes, "png", client_id)


def _call_dalle_edit(prompt: str, image_bytes: bytes, size: str, client_id: int, api_key: str) -> str:
    """gpt-image-1 画像編集 API で元画像ベースの修正を行い、絶対 URL を返す（後方互換）。"""
    img_bytes = _call_dalle_edit_bytes(prompt, image_bytes, size, api_key)
    return _save_image(img_bytes, "png", client_id)


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


# ─── 公開 API ────────────────────────────────────────────────────────────────

def generate_image(title: str, taste: str, aspect_ratio: str, client_id: int,
                   balance: str = "balanced", body_html: str = "",
                   client_name: str = "", base_prompt: str = "",
                   sample_image_paths: list = None,
                   past_image_paths: list = None) -> str:
    """背景画像を gpt-image-1 で生成し、text_focus モードでは Playwright でキャッチコピーを合成する。

    フロー:
    [1] サンプル画像の有無を判定
    [2] Claude: image_prompt / catch_copy / layout_type / tone を生成
    [3] gpt-image-1: 背景画像生成（サンプルあり→image-to-image, なし→text-to-image）
    [4] 簡易検証（破損・極小ファイル検知）
    [5] text_focus: Playwright でキャッチコピーを合成（失敗時は PIL フォールバック）
    [6] 保存して URL を返す
    """
    from config import Config

    api_key = Config.OPENAI_API_KEY
    if not api_key:
        raise ValueError("OPENAI_API_KEY が設定されていません")

    # [1] サンプル画像未設定の場合、過去投稿画像を自動取得
    if not sample_image_paths and past_image_paths is None:
        past_image_paths = _get_past_post_image_paths(client_id)

    # [2] Claude: メタデータ生成
    try:
        metadata = _generate_claude_metadata(
            title=title, body_html=body_html,
            taste=taste, balance=balance, aspect_ratio=aspect_ratio,
            client_name=client_name, base_prompt=base_prompt,
            sample_image_paths=sample_image_paths,
            past_image_paths=past_image_paths,
        )
    except Exception as e:
        logger.warning(f"メタデータ生成失敗、フォールバック使用: {e}")
        taste_hint   = _TASTE_HINTS.get(taste, _TASTE_HINTS["business_clean"])
        balance_hint = _BALANCE_HINTS.get(balance, _BALANCE_HINTS["balanced"])
        aspect_hint  = _ASPECT_HINTS.get(aspect_ratio, _ASPECT_HINTS["1:1"])
        if balance == "text_focus":
            fb_prompt = (
                f"Professional blog thumbnail, {taste_hint}, {aspect_hint}, "
                f"clean abstract background for text overlay, "
                f"lower third area with darker tone, {_UNIVERSAL_NEGATIVES}, high quality."
            )
        else:
            fb_prompt = (
                f"Professional blog thumbnail about: {title}. "
                f"{taste_hint}, {balance_hint}, {aspect_hint}, "
                f"high quality, {_UNIVERSAL_NEGATIVES}."
            )
        metadata = {
            "image_prompt": fb_prompt,
            "catch_copy":   title[:20] if balance == "text_focus" else "",
            "layout_type":  "bottom",
            "tone":         "professional",
        }

    size       = _ASPECT_TO_SIZE.get(aspect_ratio, "1024x1024")
    crop_ratio = _ASPECT_TO_CROP.get(aspect_ratio)

    # [3] 背景画像生成
    img_bytes = None
    if sample_image_paths:
        # 添付画像あり → image-to-image を試みる
        base_bytes = _load_sample_image_bytes(sample_image_paths[0])
        if base_bytes:
            try:
                img_bytes = _call_dalle_edit_bytes(metadata["image_prompt"], base_bytes, size, api_key)
                logger.info("[gpt-image-1] image-to-image 生成完了")
            except Exception as e:
                logger.warning(f"image-to-image 失敗、text-to-image にフォールバック: {e}")

    if img_bytes is None:
        img_bytes = _call_dalle_bytes(metadata["image_prompt"], size, api_key)

    # アスペクト比クロップ
    if crop_ratio:
        try:
            img_bytes = _crop_to_ratio(img_bytes, *crop_ratio)
        except Exception as e:
            logger.warning(f"クロップ失敗: {e}")

    # [4] 簡易検証
    if not _validate_image(img_bytes):
        logger.warning("[検証失敗] 再生成を試みます")
        try:
            img_bytes = _call_dalle_bytes(metadata["image_prompt"], size, api_key)
            if crop_ratio:
                try:
                    img_bytes = _crop_to_ratio(img_bytes, *crop_ratio)
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"再生成も失敗: {e}")

    # [5] text_focus: Playwright でキャッチコピーを合成
    if balance == "text_focus" and metadata.get("catch_copy"):
        try:
            img_bytes = _compose_with_playwright(
                bg_image_bytes=img_bytes,
                catch_copy=metadata["catch_copy"],
                layout_type=metadata["layout_type"],
                tone=metadata["tone"],
                aspect_ratio=aspect_ratio,
                client_id=client_id,
            )
        except Exception as e:
            logger.warning(f"[Playwright] 合成失敗、PILフォールバック: {e}")
            try:
                img_bytes = _overlay_title_text(img_bytes, metadata["catch_copy"])
            except Exception as e2:
                logger.warning(f"[PIL fallback] テキスト合成も失敗: {e2}")

    # [6] 保存
    return _save_image(img_bytes, "png", client_id)


def refine_image(original_url: str, instruction: str, client_id: int,
                 title: str = "", body_html: str = "") -> str:
    """元画像を gpt-image-1 edit API に渡して image-to-image 修正を行う。"""
    from config import Config

    api_key = Config.OPENAI_API_KEY
    if not api_key:
        raise ValueError("OPENAI_API_KEY が設定されていません")

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


def _extract_key_points(body_html: str, count: int, title: str) -> list:
    """本文から画像生成テーマとなる要点を count 個抽出する（Claude Haiku 使用）。"""
    import anthropic
    from config import Config

    api_key = Config.ANTHROPIC_API_KEY
    body_text = _strip_html(body_html or "")[:2000]
    if not api_key or not body_text:
        return [f"{title} — ポイント{i + 1}" for i in range(count)]

    user_msg = f"""以下のブログ記事の本文から、画像生成のテーマとなる要点を{count}つ日本語で抽出してください。

記事タイトル: {title}

本文:
{body_text}

## 出力ルール
- {count}つの要点を1行1つで出力
- 各行を「・」で始める（例: ・○○のポイント）
- 各要点は15〜30文字程度の簡潔な日本語フレーズ
- 記事の異なるセクション・視点・ポイントを網羅すること
- 説明文・番号・空行は不要"""

    client_obj = anthropic.Anthropic(api_key=api_key)
    message = client_obj.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = message.content[0].text.strip()
    points = []
    for line in raw.split("\n"):
        line = line.strip().lstrip("・-•").strip()
        if line:
            points.append(line)
    while len(points) < count:
        points.append(f"{title} — ポイント{len(points) + 1}")
    logger.info(f"[Claude] 要点抽出: {points[:count]}")
    return points[:count]


def generate_images_for_post(
    title: str, body_html: str, taste: str, aspect_ratio: str, client_id: int,
    balance: str = "balanced", client_name: str = "",
    base_prompt: str = "", sample_image_paths: list = None,
    count: int = 1
) -> list:
    """記事1件に対して count 枚の画像 URL リストを返す。

    count=1: タイトル+本文ベースの1枚（従来通り）
    count>=2: 1枚目=タイトル専用、2枚目以降=本文各要点ベース

    サンプル画像未設定の場合は過去投稿画像を一度だけ取得して全枚数に使いまわす。
    """
    past_image_paths = None
    if not sample_image_paths:
        past_image_paths = _get_past_post_image_paths(client_id)

    _common = dict(
        taste=taste, aspect_ratio=aspect_ratio, client_id=client_id,
        balance=balance, client_name=client_name, base_prompt=base_prompt,
        sample_image_paths=sample_image_paths, past_image_paths=past_image_paths,
    )

    if count <= 1:
        url = generate_image(title=title, body_html=body_html, **_common)
        return [url]

    urls = []

    try:
        url1 = generate_image(title=title, body_html="", **_common)
        urls.append(url1)
    except Exception as e:
        logger.warning(f"[画像1枚目生成エラー] {e}")

    try:
        key_points = _extract_key_points(body_html, count - 1, title)
    except Exception as e:
        logger.warning(f"[要点抽出エラー] {e}")
        key_points = [f"{title} — ポイント{i + 1}" for i in range(count - 1)]

    for kp in key_points:
        try:
            url = generate_image(title=f"{title}: {kp}", body_html=body_html, **_common)
            urls.append(url)
        except Exception as e:
            logger.warning(f"[画像生成エラー: {kp[:30]}] {e}")

    return urls
