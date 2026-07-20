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
        word_count = data.get("word_count", "テーマに合わせた適切な長さで書ききること（読み物系は3000〜5000字目安）")
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

        return f"""以下の条件でブログ記事を作成してください。

## テーマ・トピック
{topic}

## 伝える内容（読者に届けたいメッセージ・エピソード）
{keywords}

## 文字数・トーン
{word_count} ／ トーン: {tone}
{posts_section}{design_section}

6ステップに従い、Markdown 形式で記事を出力してください。"""

    def stream(self, data: dict):
        yield from self._stream(SYSTEM, self._build_message(data))

    def run(self, data: dict) -> str:
        return self._generate(SYSTEM, self._build_message(data))
