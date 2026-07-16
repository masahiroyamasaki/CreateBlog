import os
import re
import base64
import requests


def is_configured(company: dict) -> bool:
    return bool(
        company.get("wp_url")
        and company.get("wp_username")
        and company.get("wp_password")
    )


def _inline(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    return text.strip()


def markdown_to_blocks(md: str) -> str:
    """Markdown を Gutenberg ブロック形式の HTML に変換する。"""
    chunks = re.split(r"\n{2,}", md.strip())
    blocks = []

    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue

        lines = chunk.split("\n")
        first = lines[0]

        if first.startswith("# "):
            t = _inline(first[2:])
            blocks.append(
                f'<!-- wp:heading {{"level":1}} -->\n'
                f'<h1 class="wp-block-heading">{t}</h1>\n'
                f"<!-- /wp:heading -->"
            )
        elif first.startswith("## "):
            t = _inline(first[3:])
            blocks.append(
                f'<!-- wp:heading {{"level":2}} -->\n'
                f'<h2 class="wp-block-heading">{t}</h2>\n'
                f"<!-- /wp:heading -->"
            )
        elif first.startswith("### "):
            t = _inline(first[4:])
            blocks.append(
                f'<!-- wp:heading {{"level":3}} -->\n'
                f'<h3 class="wp-block-heading">{t}</h3>\n'
                f"<!-- /wp:heading -->"
            )
        elif first.startswith("> "):
            t = _inline(first[2:])
            blocks.append(
                f"<!-- wp:quote -->\n"
                f'<blockquote class="wp-block-quote"><p>{t}</p></blockquote>\n'
                f"<!-- /wp:quote -->"
            )
        elif all(ln.startswith("- ") or ln.startswith("* ") for ln in lines if ln.strip()):
            items = "".join(
                f"<li>{_inline(ln[2:])}</li>"
                for ln in lines if ln.strip()
            )
            blocks.append(
                f"<!-- wp:list -->\n"
                f'<ul class="wp-block-list">{items}</ul>\n'
                f"<!-- /wp:list -->"
            )
        elif all(re.match(r"^\d+\.\s", ln) for ln in lines if ln.strip()):
            _ol_pat = r'^\d+\.\s*'
            items = "".join(
                "<li>" + _inline(re.sub(_ol_pat, '', ln)) + "</li>"
                for ln in lines if ln.strip()
            )
            blocks.append(
                f"<!-- wp:list {{\"ordered\":true}} -->\n"
                f'<ol class="wp-block-list">{items}</ol>\n'
                f"<!-- /wp:list -->"
            )
        else:
            # 複数行の場合は <br> でつなぐ
            text = "<br>".join(_inline(ln) for ln in lines if ln.strip())
            if text:
                blocks.append(
                    f"<!-- wp:paragraph -->\n"
                    f"<p>{text}</p>\n"
                    f"<!-- /wp:paragraph -->"
                )

    return "\n\n".join(blocks)


def markdown_to_html(md: str) -> str:
    """Markdown を純粋な HTML に変換する（カスタムテンプレート使用時）。"""
    chunks = re.split(r"\n{2,}", md.strip())
    parts = []

    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        lines = chunk.split("\n")
        first = lines[0]

        if first.startswith("# "):
            parts.append(f"<h1>{_inline(first[2:])}</h1>")
        elif first.startswith("## "):
            parts.append(f"<h2>{_inline(first[3:])}</h2>")
        elif first.startswith("### "):
            parts.append(f"<h3>{_inline(first[4:])}</h3>")
        elif first.startswith("> "):
            parts.append(f"<blockquote><p>{_inline(first[2:])}</p></blockquote>")
        elif all(ln.startswith("- ") or ln.startswith("* ") for ln in lines if ln.strip()):
            items = "".join(f"<li>{_inline(ln[2:])}</li>" for ln in lines if ln.strip())
            parts.append(f"<ul>{items}</ul>")
        elif all(re.match(r"^\d+\.\s", ln) for ln in lines if ln.strip()):
            _ol_pat = r'^\d+\.\s*'
            items = "".join(
                "<li>" + _inline(re.sub(_ol_pat, '', ln)) + "</li>"
                for ln in lines if ln.strip()
            )
            parts.append(f"<ol>{items}</ol>")
        else:
            text = "<br>".join(_inline(ln) for ln in lines if ln.strip())
            if text:
                parts.append(f"<p>{text}</p>")

    return "\n".join(parts)


def apply_template(template_path: str, content_md: str) -> str:
    """テンプレートファイルを読み込み、{{content}} を記事 HTML に置換する。"""
    try:
        with open(template_path, encoding="utf-8") as f:
            template = f.read()
        html = markdown_to_html(content_md)
        return template.replace("{{content}}", html)
    except FileNotFoundError:
        return markdown_to_blocks(content_md)


def upload_media(site: dict, image_path: str) -> int | None:
    """画像ファイルをWordPressメディアライブラリにアップロードし、media_idを返す。
    失敗時は None を返す（呼び出し元は続行可）。
    """
    wp_url = site["wp_url"].rstrip("/")
    token = base64.b64encode(
        f"{site['wp_username']}:{site['wp_password']}".encode()
    ).decode()

    filename = os.path.basename(image_path)
    try:
        with open(image_path, "rb") as f:
            data = f.read()
        res = requests.post(
            f"{wp_url}/wp-json/wp/v2/media",
            headers={
                "Authorization": f"Basic {token}",
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Type": "image/png",
            },
            data=data,
            timeout=(15, 60),
        )
        if res.status_code in (200, 201):
            return res.json().get("id")
    except Exception:
        pass
    return None


def post_article(company: dict, title: str, content_md: str, image_path: str = None) -> dict:
    """記事を WordPress に投稿する。成功時は post_id と link を返す。
    image_path が指定されている場合はメディアにアップロードしてアイキャッチ画像に設定する。
    """
    wp_url = company["wp_url"].rstrip("/")
    token = base64.b64encode(
        f"{company['wp_username']}:{company['wp_password']}".encode()
    ).decode()

    template_path = company.get("template_file") or ""
    if template_path:
        block_content = apply_template(template_path, content_md)
    else:
        block_content = markdown_to_blocks(content_md)
    status = company.get("wp_status") or "draft"

    # アイキャッチ画像のアップロード
    featured_media_id = None
    if image_path and os.path.exists(image_path):
        featured_media_id = upload_media(company, image_path)

    post_body = {"title": title, "content": block_content, "status": status}
    if featured_media_id:
        post_body["featured_media"] = featured_media_id

    try:
        res = requests.post(
            f"{wp_url}/wp-json/wp/v2/posts",
            headers={
                "Authorization": f"Basic {token}",
                "Content-Type": "application/json",
            },
            json=post_body,
            timeout=(15, 90),  # (接続タイムアウト, 読み取りタイムアウト)
        )
    except requests.exceptions.ConnectionError:
        return {"success": False, "reason": "WordPress サイトに接続できません。URL を確認してください。"}
    except requests.exceptions.Timeout:
        return {"success": False, "reason": "タイムアウト。サイトの応答が遅いか、記事が大きすぎる可能性があります。(90秒超過)"}

    if res.status_code in (200, 201):
        data = res.json()
        return {
            "success": True,
            "wp_post_id": data.get("id"),
            "link": data.get("link"),
            "status": status,
            "featured_image": bool(featured_media_id),
        }

    # エラー詳細を返す
    try:
        err = res.json()
        reason = err.get("message", res.text[:300])
    except Exception:
        reason = res.text[:300]
    return {"success": False, "reason": f"HTTP {res.status_code}: {reason}"}


def test_connection(company: dict) -> dict:
    """接続テスト（認証確認）。"""
    wp_url = company["wp_url"].rstrip("/")
    token = base64.b64encode(
        f"{company['wp_username']}:{company['wp_password']}".encode()
    ).decode()
    try:
        res = requests.get(
            f"{wp_url}/wp-json/wp/v2/users/me",
            headers={"Authorization": f"Basic {token}"},
            timeout=10,
        )
        if res.status_code == 200:
            data = res.json()
            return {"success": True, "name": data.get("name", "")}
        return {"success": False, "reason": f"認証失敗 (HTTP {res.status_code})"}
    except requests.exceptions.ConnectionError:
        return {"success": False, "reason": "接続できません。URL を確認してください。"}
    except requests.exceptions.Timeout:
        return {"success": False, "reason": "タイムアウト"}
