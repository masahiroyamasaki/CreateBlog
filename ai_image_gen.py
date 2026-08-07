"""ai_image_gen.py — Claude でプロンプト生成 → DALL-E 3 で画像生成"""
import base64
import io
import os
import re
import uuid
import logging

import requests as _requests

logger = logging.getLogger(__name__)

# アスペクト比 → gpt-image-1 サイズマッピング（API サポート値）
_ASPECT_TO_SIZE = {
    "1:1":  "1024x1024",
    "4:5":  "1024x1536",   # 生成後に 1024x1280 へクロップして正確な 4:5 を実現
    "16:9": "1536x1024",   # 生成後に 1536x864 へクロップして正確な 16:9 を実現
}
# 生成後センタークロップ比率 (w, h) — None のサイズはクロップ不要
_ASPECT_TO_CROP = {
    "4:5":  (4, 5),
    "16:9": (16, 9),
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
    "no people, no persons, no human figures, no characters, no faces"
)

_NEGATIVE_GUIDANCE = (
    "Avoid: text, watermark, photorealistic rendering, low quality."
)


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
    # 日本語が含まれている場合は Claude に翻訳させる
    _has_japanese = bool(re.search(r'[぀-鿿]', effective_base))

    if balance == "text_focus":
        text_section = f"""
## テキスト描写指示（最重要）
記事タイトル「{title}」を画像内に美しく配置すること。
【必須制約】
- テキストは画像の全四辺から必ず10%以上内側に収めること（絶対に端で切れないこと）
- フォントサイズは全文字が画像内に完全に収まる大きさに調整すること
- タイトルが長い場合は2〜3行に折り返してよい（行ごとに収まるサイズで）
- テキストが1文字でもはみ出たり途切れたりしてはならない
- 日本語タイトルはそのまま日本語で、または英語に意訳して配置してよい
- ★人物・人間・キャラクターは一切含めないこと（背景＋テキストのみの構図にすること）"""
        output_suffix = (
            f'title text "{title}" fully contained within image boundaries with generous safe margins from all edges, '
            f'complete text fully visible without any cropping or cutoff, '
            f'font size adjusted small enough so entire title fits within the frame, '
            f'text placed in center or lower third area, '
            f'no people, no persons, no human figures, no characters, no faces, '
            f'high quality'
        )
        no_text_rule = f'- タイトル「{title}」を画像内に描写するため「no text」「no letters」は含めないこと'
    else:
        text_section = ""
        output_suffix = "high quality, no text, no letters, no words, no japanese characters"
        no_text_rule = '- 必ず "no text, no letters, no words, no numbers, no japanese characters, no logos, no signs" を含めること（絶対省略禁止）'

    # --- base_instruction / output_format を balance × base_prompt × sample の組み合わせで決定 ---
    # まず素直に設定し、後段で上書きする
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

    # text_focus の場合: キャラクター指定を含むデフォルトbase_promptを使わない
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
            # カスタムbase_promptなし → デフォルトキャラクター定義を一切使わない
            base_instruction = (
                "## スタイル指定\n"
                "抽象的・グラフィカルな背景スタイルで生成すること。\n"
                "人物・キャラクター・顔・シルエットは一切含めないこと。\n"
                "テキストが映えるクリーンな背景デザインにすること。"
            )
        output_format = '"[人物なし・抽象グラフィック背景], [構図], [照明], [色調], [雰囲気], ' + output_suffix + '"'

    # ── 画像参照の優先度ロジック ──────────────────────────────────────────────
    # 1. 企業設定サンプル画像: 最優先。テイスト/ベースプロンプト設定を完全上書き。
    # 2. 過去投稿画像: 補助参照。テイスト/バランス設定と併用（上書きしない）。
    # 3. なし: テイスト/バランス/ベースプロンプトのみ。
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

    # サンプル画像を事前分析（APIコール1回）→ 文字/人物有無を確定させてから分岐
    sample_analysis = _analyze_sample_images(sample_image_paths or []) if company_blocks else {}
    has_text_in_sample   = sample_analysis.get("has_text", False)
    has_people_in_sample = sample_analysis.get("has_people", False)
    analysis_desc        = sample_analysis.get("description", "")

    # text/人物禁止フラグの初期値（後段で上書き可）
    allow_text_in_image   = False   # True = サンプルに文字あり → 生成画像にも文字を入れる
    allow_people_in_image = False   # True = サンプルに人物あり → 生成画像にも人物を入れてよい

    if company_blocks:
        # ── パターン1: 企業設定サンプル画像あり（事前分析済み）──
        # テイスト・ベースプロンプトを完全無視し、分析結果のスタイルのみで生成する
        image_blocks = company_blocks

        allow_text_in_image   = has_text_in_sample and balance != "text_focus"
        allow_people_in_image = has_people_in_sample and balance != "text_focus"

        # 分析結果から詳細なデザイン仕様を組み立てる
        a = sample_analysis  # 短縮エイリアス

        # 背景仕様
        bg_spec = f"背景タイプ: {a.get('bg_type','')}, 背景色: {a.get('bg_color','')}, 背景要素: {a.get('bg_elements','')}"

        # テキスト仕様（文字ありの場合のみ）
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
        # ── パターン2: 過去投稿画像あり（サンプル画像なし）──
        # テイスト/バランス設定は維持しつつ、過去画像のスタイルを補助参照する
        image_blocks = past_blocks
        sample_section = """
## 過去投稿画像（スタイル参考・テイスト設定と併用）
添付した画像はこの企業の直近の投稿画像です。テイスト設定に従いつつ、これらのスタイル・雰囲気も参考にすること。
- 過去画像の色調・配色・全体的な雰囲気・タッチを参考にすること
- テイスト設定（下記）が最優先だが、過去画像のスタイルと整合するよう努めること
- 過去画像内のテキスト・ロゴ・文字は生成画像に含めないこと
"""
        # base_instruction / output_format はそのまま（テイスト設定を維持）

    # no_text_critical: サンプル画像に文字がある場合はテキスト禁止を解除
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

    # text_focus かつ人物禁止の追加注意
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

    # マルチモーダル対応: サンプル画像がある場合は画像ブロックを先頭に追加
    content = image_blocks + [{"type": "text", "text": text_body}] if image_blocks else text_body

    client_obj = anthropic.Anthropic(api_key=api_key)
    message = client_obj.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        messages=[{"role": "user", "content": content}],
    )
    raw = message.content[0].text.strip()
    if raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1]

    # ── 最終プロンプトへの強制付与（Claude Haiku が忘れた場合の保険）──
    # 人物禁止: サンプル画像に人物がいる場合のみ人物を許可し、それ以外は必ず禁止ワードを追加
    if not allow_people_in_image:
        _no_people_kws = ["no people", "no person", "no human", "no character", "no face"]
        if not any(kw in raw.lower() for kw in _no_people_kws):
            raw += ", no people, no persons, no human figures, no characters, no faces"

    # 文字禁止: サンプル画像に文字がある or text_focus 以外で文字禁止の場合
    if not allow_text_in_image and balance != "text_focus":
        _no_text_kws = ["no text", "no letter", "no word", "no number", "no sign"]
        if not any(kw in raw.lower() for kw in _no_text_kws):
            raw += ", no text, no letters, no words, no numbers, no japanese characters, no logos"

    logger.info(f"[Claude] 生成プロンプト: {raw[:150]}...")
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
                   client_name: str = "", base_prompt: str = "",
                   sample_image_paths: list = None,
                   past_image_paths: list = None) -> str:
    """Claude でプロンプトを生成し、DALL-E 3 で画像を生成して保存する。

    sample_image_paths が未設定の場合は自動で過去投稿画像を取得してスタイル参照に使う。
    past_image_paths=[] を明示的に渡すと自動取得をスキップする。
    """
    from config import Config

    api_key = Config.OPENAI_API_KEY
    if not api_key:
        raise ValueError("OPENAI_API_KEY が設定されていません")

    # サンプル画像未設定の場合、過去投稿画像を自動取得
    if not sample_image_paths and past_image_paths is None:
        past_image_paths = _get_past_post_image_paths(client_id)

    try:
        prompt = _generate_prompt_with_claude(
            title=title,
            body_html=body_html,
            taste=taste,
            balance=balance,
            aspect_ratio=aspect_ratio,
            client_name=client_name,
            base_prompt=base_prompt,
            sample_image_paths=sample_image_paths,
            past_image_paths=past_image_paths,
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
    crop_ratio = _ASPECT_TO_CROP.get(aspect_ratio)
    return _call_dalle(prompt, size, client_id, api_key, crop_ratio=crop_ratio)


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


def _call_dalle(prompt: str, size: str, client_id: int, api_key: str,
                crop_ratio: tuple = None) -> str:
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
    img_bytes = base64.b64decode(b64)
    if crop_ratio:
        try:
            img_bytes = _crop_to_ratio(img_bytes, *crop_ratio)
        except Exception as e:
            logger.warning(f"クロップ失敗、元画像を使用: {e}")
    return _save_image(img_bytes, "png", client_id)


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
    # 過去投稿画像を一度だけ取得（サンプル画像がある場合は不要）
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

    # 1枚目: タイトルイメージ（body_html は渡さず title のみに集中）
    try:
        url1 = generate_image(title=title, body_html="", **_common)
        urls.append(url1)
    except Exception as e:
        logger.warning(f"[画像1枚目生成エラー] {e}")

    # 要点を count-1 個抽出
    try:
        key_points = _extract_key_points(body_html, count - 1, title)
    except Exception as e:
        logger.warning(f"[要点抽出エラー] {e}")
        key_points = [f"{title} — ポイント{i + 1}" for i in range(count - 1)]

    # 2枚目以降: 各要点ベースの画像
    for kp in key_points:
        try:
            url = generate_image(title=f"{title}: {kp}", body_html=body_html, **_common)
            urls.append(url)
        except Exception as e:
            logger.warning(f"[画像生成エラー: {kp[:30]}] {e}")

    return urls


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
