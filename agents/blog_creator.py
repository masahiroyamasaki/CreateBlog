from .base import BaseAgent

SYSTEM = """あなたはプロのブログライターです。
以下の6ステップを必ず実行してから記事を出力してください。

━━━ STEP 1: 空気を読む（既存記事からトンマナを把握）━━━
提供された直近記事（最大5件）を分析し、以下を把握すること：
・トーン＆マナー：技術的にしっかりしているが堅苦しくない、現場目線で語りかける文体
・文字数の傾向：Tips系は200〜500字、読み物系は3,000〜5,000字のメリハリ
・見出し構造：情報の階層とリズムのパターン
既存記事がない場合は上記のスタイルをデフォルトとして適用する。
突然キャラが変わったような記事にならないよう、ブレずに維持すること。

━━━ STEP 2: テーマの「芯」を抜き出す ━━━
「伝える内容」から普遍的な核となるテーマを見つけ、そこを中心に枝葉を広げる。
例：「人がいて価値が上がる。機械があり生産性が上がる」→ 芯は「人と機械の共存」

━━━ STEP 3: 読者の「感情」から逆算して構成を組む ━━━
情報を並べるだけでなく、感情の流れを必ずこの順で設計する：
1. 共感：読者の不安・悩みに「わかるよ」と寄り添う冒頭
2. 課題の分解：「何が本当の問題なのか」を整理する中盤前半
3. 解決・希望の提示：具体的な道筋や未来像を示す中盤後半
4. 行動への後押し：「一緒にやろう」という温かい締め
この流れがあることで「読んでよかった」と感じてもらえる。

━━━ STEP 4: 具体性で説得力を出す ━━━
抽象論だけでなく、必ずリアルなエピソードや具体的な数字を盛り込む。
例：「RPA導入で毎日2時間の余裕が生まれ、経営企画室に異動したAさん」
読者が「自分にも起こりうる話だ」と感じられる事例を必ず入れること。

━━━ STEP 5: 引用・強調で「声」を残す ━━━
ユーザーの「伝える内容」の中にあるキーフレーズは Markdown 引用ブロック（> ）でそのまま残す。
これが記事に「体温」を与え、AIが自動生成した感を消す。人間の想いが乗った記事に仕上げること。

━━━ STEP 6: 出力前の最終確認 ━━━
以下をすべて満たしてから出力すること：
・見出しの流れは自然か
・文字数はテーマに見合っているか（Tips系200〜500字、読み物系3,000〜5,000字）
・ブランドのトンマナは保ててるか
・読み手に「明日から何かできる」感を残せているか

【出力形式】Markdown（# H1タイトルから始める）。記事本文のみを出力すること。前置き・説明文・ステップの解説は一切含めないこと。"""


class BlogCreatorAgent(BaseAgent):
    def _build_message(self, data: dict) -> str:
        topic = data.get("topic", "")
        keywords = data.get("keywords", "")
        tone = data.get("tone", "標準")

        _wc_num = int(data.get("word_count", 0) or 0)
        _wc_map = {
            100:  "100字以内の超短文。タイトル除き本文は100字を絶対に超えないこと。",
            150:  "150字以内の短文。タイトル除き本文は150字を絶対に超えないこと。",
            200:  "約200字。簡潔にまとめ、200字を大きく超えないこと。",
            300:  "約300字。要点を絞り、300字前後で完結させること。",
            400:  "約400字。400字前後で完結させること。",
            500:  "約500字（短いTips記事）。500字前後で完結させること。",
            1000: "約1000字。読みやすい中編記事として1000字前後で完結させること。",
            1500: "約1500字。1500字前後でしっかりまとめること。",
            2000: "約2000字。2000字前後の読み物記事として構成すること。",
            3000: "約3000字。3000字前後のしっかりした読み物記事として構成すること。",
        }
        word_count = _wc_map.get(_wc_num, "テーマに合わせた適切な長さで書ききること（読み物系は3000〜5000字目安）")
        existing_posts = data.get("existing_posts", [])

        if existing_posts:
            posts_section = "\n\n---\n\n## 【STEP 1参照】直近の既存記事（最大5件）\n"
            for i, post in enumerate(existing_posts, 1):
                preview = post["content"][:600].rstrip()
                posts_section += f"\n### 記事{i}：{post['title']}\n{preview}…\n"
        else:
            posts_section = "\n\n（既存記事なし：デフォルトスタイルで作成）"

        design_prompt = data.get("design_prompt", "")
        design_section = f"\n\n## サイトデザイン・文体指示\n{design_prompt}" if design_prompt else ""

        target_audience = data.get("target_audience", "")
        audience_section = f"\n\n## 想定読者・ターゲット\n{target_audience}" if target_audience else ""

        character_prompt = data.get("character_prompt", "")
        character_section = f"\n\n## ライターのキャラクター・ペルソナ\n{character_prompt}" if character_prompt else ""

        taste = data.get("taste", "standard")
        _taste_map = {
            "standard":     "標準（既存記事のトンマナに準じる）",
            "formal":       "フォーマル — 敬語を徹底し、論理的・丁寧に展開する。ビジネス文書としても通用する格調ある表現を使う。",
            "casual":       "カジュアル — 読者に語りかけるような親しみやすい口調で、堅苦しさを排除する。テンポよく読み進められるようにする。",
            "pop":          "ポップ — 明るく軽快で若々しいエネルギーのある表現を使う。読んでいて楽しい雰囲気を出し、テンポよく展開する。",
            "simple":       "シンプル — 余分な表現は省き、情報を簡潔に伝えることを最優先する。1文を短くし、スキャンしやすい構成にする。",
            "luxury":       "高級感 — 洗練された言葉遣いで読者の上質な感性に訴える。押しつけがましくなく、余白のある落ち着いた表現を心がける。",
            "professional": "プロフェッショナル — 信頼感と権威性を持たせ、データや根拠を重視する。業界のオピニオンリーダーとして語る視点を持つ。",
        }
        taste_label = _taste_map.get(taste, _taste_map["standard"])
        taste_section = f"\n\n## 記事テイスト・スタイル指定\n{taste_label}" if taste != "standard" else ""

        return f"""以下の条件でブログ記事を作成してください。

## テーマ・トピック
{topic}

## 伝える内容（読者に届けたいメッセージ・エピソード）
{keywords}

## 文字数・トーン
{word_count} ／ トーン: {tone}
{posts_section}{design_section}{audience_section}{character_section}{taste_section}

6ステップに従い、Markdown 形式で記事を出力してください。"""

    def stream(self, data: dict):
        yield from self._stream(SYSTEM, self._build_message(data))

    def run(self, data: dict) -> str:
        return self._generate(SYSTEM, self._build_message(data))
