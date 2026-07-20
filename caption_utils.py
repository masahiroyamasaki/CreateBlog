"""caption_utils.py — IG キャプション後処理ユーティリティ"""
import re


def strip_account_prefix(caption: str, client_name: str = "") -> str:
    """冒頭に混入した企業名・@メンションを段落・行レベルで除去する。

    対応パターン:
      - @ハンドル 行
      - 企業名と完全一致する段落/行
      - 短い段落（≤60文字）に企業名が含まれる（装飾込み: 【RKパートナーズ】など）
      - 企業名で始まる行（「RKパートナーズです！」など）
    """
    paragraphs = re.split(r'\n\s*\n', caption.strip())
    if not paragraphs:
        return ""

    def _is_account_header(text: str) -> bool:
        text = text.strip()
        if not text:
            return False
        # @メンション行を含む
        if any(line.strip().startswith("@") for line in text.splitlines()):
            return True
        if not client_name:
            return False
        # 企業名と完全一致
        if text == client_name:
            return True
        # 60文字以下の短い段落に企業名が含まれる → ヘッダー行と判定
        if len(text) <= 60 and client_name in text:
            return True
        return False

    # 最初の段落がヘッダーなら除去（残りの段落がある場合のみ）
    if _is_account_header(paragraphs[0]) and len(paragraphs) > 1:
        paragraphs = paragraphs[1:]

    # 残った先頭段落の各行を先頭から順にチェック
    if paragraphs:
        lines = paragraphs[0].splitlines()
        new_lines = []
        for line in lines:
            s = line.strip()
            if not new_lines:
                if not s:
                    continue
                if s.startswith("@"):
                    continue
                if client_name:
                    if s == client_name:
                        continue
                    if s.startswith(client_name):
                        trimmed = s[len(client_name):].lstrip(" 　・／/")
                        if not trimmed:
                            continue
                        line = trimmed
            new_lines.append(line)
        paragraphs[0] = "\n".join(new_lines)

    paragraphs = [p for p in paragraphs if p.strip()]
    return "\n\n".join(paragraphs).strip()
