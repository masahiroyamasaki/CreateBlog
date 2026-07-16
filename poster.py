"""poster.py — サイトタイプに応じて記事を投稿するモジュール"""
import os
import re
import sys
import json
import subprocess
import requests
from datetime import date
import wordpress as wp_module


def _run_command(command: str, cwd: str, timeout: int = 60) -> dict:
    """コマンドを実行し、タイムアウト時はプロセスツリーごと強制終了する。
    Windows の capture_output ブロック問題を回避するため Popen + wait を使用。
    """
    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # プロセスツリーごと強制終了
            _kill_tree(proc.pid)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            return {
                'success': False,
                'reason': (
                    f'デプロイコマンドが {timeout} 秒以内に完了しませんでした。\n'
                    f'コマンド: {command}\n'
                    '終了しないコマンド（サーバー起動など）は設定しないでください。'
                ),
            }

        if proc.returncode != 0:
            return {
                'success': False,
                'reason': f'デプロイコマンドが終了コード {proc.returncode} で失敗しました。\nコマンド: {command}',
            }
        return {'success': True}

    except Exception as e:
        return {'success': False, 'reason': f'デプロイ実行エラー: {e}'}


def _kill_tree(pid: int):
    """プロセスとすべての子プロセスを強制終了する。"""
    if sys.platform == 'win32':
        subprocess.run(
            ['taskkill', '/F', '/T', '/PID', str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        try:
            import signal
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except Exception:
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass


def detect_site_type(site: dict) -> str:
    """設定済みフィールドからサイトタイプを自動判定する。
    WordPress認証情報 > APIエンドポイント > 出力ディレクトリ の優先順で判定。
    どれも未設定なら空文字を返す。
    """
    if (site.get('wp_url', '').strip()
            and site.get('wp_username', '').strip()
            and site.get('wp_password', '').strip()):
        return 'wordpress'
    if site.get('api_endpoint', '').strip():
        return 'api_cms'
    if site.get('output_dir', '').strip():
        return 'static'
    return ''


def post_to_site(site: dict, title: str, content_md: str, image_path: str = None) -> dict:
    """サイトタイプを自動判定して記事を投稿する"""
    detected = detect_site_type(site)

    if not detected:
        return {
            'success': False,
            'reason': (
                '投稿先の設定が不完全です。\n'
                '・WordPress → URL・ユーザー名・パスワードをすべて入力\n'
                '・API/CMS → APIエンドポイントを入力\n'
                '・静的サイト → 出力ディレクトリを入力'
            ),
        }

    try:
        if detected == 'wordpress':
            return _post_wordpress(site, title, content_md, image_path)
        elif detected == 'api_cms':
            return _post_api_cms(site, title, content_md)
        elif detected == 'static':
            return _post_static(site, title, content_md, image_path)
    except Exception as e:
        return {'success': False, 'reason': f'投稿中にエラーが発生しました: {str(e)}'}

    return {'success': False, 'reason': f'不明なサイトタイプ: {detected}'}


def _build_content(site: dict, content_md: str) -> str:
    """テンプレート適用 or デフォルト変換"""
    template_path = site.get('template_file') or ''
    site_type = site.get('site_type', 'wordpress')
    if template_path and os.path.exists(template_path):
        return wp_module.apply_template(template_path, content_md)
    elif site_type == 'wordpress':
        return wp_module.markdown_to_blocks(content_md)
    else:
        return wp_module.markdown_to_html(content_md)


def _post_wordpress(site: dict, title: str, content_md: str, image_path: str = None) -> dict:
    """WordPressのREST APIで投稿"""
    if not (site.get('wp_url') and site.get('wp_username') and site.get('wp_password')):
        return {'success': False, 'reason': 'WordPress接続情報が不足しています'}
    return wp_module.post_article(site, title, content_md, image_path=image_path)


def _post_api_cms(site: dict, title: str, content_md: str) -> dict:
    """汎用API/CMS投稿 - Bearer認証、JSON body"""
    endpoint = site.get('api_endpoint', '').strip()
    if not endpoint:
        return {'success': False, 'reason': 'APIエンドポイントが設定されていません'}

    # コンテンツをHTMLに変換
    content_html = _build_content(site, content_md)

    # ヘッダー構築
    headers = {
        'Content-Type': 'application/json',
    }
    api_key = site.get('api_key', '').strip()
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'

    # 追加ヘッダーをJSONからマージ
    api_headers_str = site.get('api_headers', '').strip()
    if api_headers_str:
        try:
            extra_headers = json.loads(api_headers_str)
            if isinstance(extra_headers, dict):
                headers.update(extra_headers)
        except (json.JSONDecodeError, ValueError):
            pass  # 不正なJSONは無視

    # ボディ構築
    api_body_template = site.get('api_body_template', '').strip()
    if api_body_template:
        try:
            body_template = json.loads(api_body_template)
            # dict内の文字列値に{{title}}/{{content}}を置換
            def replace_placeholders(obj):
                if isinstance(obj, str):
                    return obj.replace('{{title}}', title).replace('{{content}}', content_html)
                elif isinstance(obj, dict):
                    return {k: replace_placeholders(v) for k, v in obj.items()}
                elif isinstance(obj, list):
                    return [replace_placeholders(i) for i in obj]
                return obj
            body = replace_placeholders(body_template)
        except (json.JSONDecodeError, ValueError):
            body = {'title': title, 'content': content_html}
    else:
        body = {'title': title, 'content': content_html}

    try:
        res = requests.post(
            endpoint,
            headers=headers,
            json=body,
            timeout=30,
        )
    except requests.exceptions.ConnectionError:
        return {'success': False, 'reason': 'APIエンドポイントに接続できません'}
    except requests.exceptions.Timeout:
        return {'success': False, 'reason': '接続タイムアウト。APIが応答していません'}

    if res.status_code in (200, 201):
        try:
            data = res.json()
        except Exception:
            data = {}
        return {
            'success': True,
            'status_code': res.status_code,
            'response': data,
        }

    try:
        err = res.json()
        reason = err.get('message', res.text[:300])
    except Exception:
        reason = res.text[:300]
    return {'success': False, 'reason': f'HTTP {res.status_code}: {reason}'}


def _format_for_extension(ext: str, title: str, content_md: str, image_filename: str = None) -> str:
    """拡張子に応じたコンテンツ文字列を生成する。image_filename は同ディレクトリの画像ファイル名。"""
    today_str = date.today().strftime('%Y-%m-%d')
    ext = ext.lower()

    img_md  = f'![トップ画像](./{image_filename})\n\n' if image_filename else ''
    img_html = (
        f'<img src="./{image_filename}" alt="{title}" '
        f'style="width:100%;max-height:400px;object-fit:cover;margin-bottom:24px;">\n'
        if image_filename else ''
    )

    if ext in ('.md', '.mdx'):
        return (
            f'---\ntitle: "{title}"\ndate: {today_str}\n---\n\n'
            f'{img_md}'
            f'{content_md}\n'
        )

    if ext in ('.tsx', '.jsx'):
        content_html = wp_module.markdown_to_html(content_md)
        img_jsx = (
            f'      <img src="./{image_filename}" alt={{title}} '
            f'style={{{{width:"100%",maxHeight:"400px",objectFit:"cover",marginBottom:"24px"}}}} />\n'
            if image_filename else ''
        )
        return (
            f'export default function BlogPost() {{\n'
            f'  const title = "{title}";\n'
            f'  return (\n'
            f'    <article>\n'
            f'      <h1>{{title}}</h1>\n'
            f'{img_jsx}'
            f'      <div dangerouslySetInnerHTML={{{{ __html: `{content_html.replace("`", chr(96))}` }}}} />\n'
            f'    </article>\n'
            f'  );\n'
            f'}}\n'
        )

    if ext == '.vue':
        content_html = wp_module.markdown_to_html(content_md)
        img_vue = (
            f'    <img src="./{image_filename}" :alt="title" '
            f'style="width:100%;max-height:400px;object-fit:cover;margin-bottom:24px;" />\n'
            if image_filename else ''
        )
        return (
            f'<template>\n'
            f'  <article>\n'
            f'    <h1>{{{{ title }}}}</h1>\n'
            f'{img_vue}'
            f'    <div v-html="content" />\n'
            f'  </article>\n'
            f'</template>\n\n'
            f'<script setup>\n'
            f'const title = "{title}";\n'
            f'const content = `{content_html.replace("`", chr(96))}`;\n'
            f'</script>\n'
        )

    if ext == '.astro':
        content_html = wp_module.markdown_to_html(content_md)
        img_astro = (
            f'<img src="./{image_filename}" alt={{title}} '
            f'style="width:100%;max-height:400px;object-fit:cover;margin-bottom:24px;" />\n'
            if image_filename else ''
        )
        return (
            f'---\nconst title = "{title}";\nconst date = "{today_str}";\n---\n\n'
            f'<article>\n'
            f'  <h1>{{title}}</h1>\n'
            f'  {img_astro}'
            f'  <div set:html={{`{content_html.replace("`", chr(96))}`}} />\n'
            f'</article>\n'
        )

    if ext == '.svelte':
        content_html = wp_module.markdown_to_html(content_md)
        img_svelte = (
            f'<img src="./{image_filename}" alt={{title}} '
            f'style="width:100%;max-height:400px;object-fit:cover;margin-bottom:24px;" />\n'
            if image_filename else ''
        )
        return (
            f'<script>\n'
            f'  const title = "{title}";\n'
            f'  const content = `{content_html.replace("`", chr(96))}`;\n'
            f'</script>\n\n'
            f'<article>\n'
            f'  <h1>{{title}}</h1>\n'
            f'  {img_svelte}'
            f'  <div>{{@html content}}</div>\n'
            f'</article>\n'
        )

    # デフォルト: HTML
    content_html = wp_module.markdown_to_html(content_md)
    return (
        f'<!DOCTYPE html>\n'
        f'<html lang="ja">\n'
        f'<head>\n'
        f'  <meta charset="UTF-8">\n'
        f'  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f'  <title>{title}</title>\n'
        f'</head>\n'
        f'<body>\n'
        f'<article>\n'
        f'<h1>{title}</h1>\n'
        f'{img_html}'
        f'{content_html}\n'
        f'</article>\n'
        f'</body>\n'
        f'</html>\n'
    )


def _post_static(site: dict, title: str, content_md: str, image_path: str = None) -> dict:
    """静的サイト: 拡張子に合わせたファイル書き出し + デプロイコマンド実行"""
    import shutil

    output_dir = site.get('output_dir', '').strip()
    if not output_dir:
        return {'success': False, 'reason': '出力ディレクトリが設定されていません'}

    ext = (site.get('output_extension') or '.html').strip()
    if not ext.startswith('.'):
        ext = '.' + ext

    # スラッグ生成（英数字とハイフンのみ）
    slug = re.sub(r'[^\w\s-]', '', title.lower())
    slug = re.sub(r'[\s_-]+', '-', slug).strip('-')
    if not slug:
        slug = 'article'

    today_str = date.today().strftime('%Y%m%d')
    filename = f'{today_str}-{slug}{ext}'
    filepath = os.path.join(output_dir, filename)

    # 画像を出力ディレクトリにコピー
    image_filename = None
    if image_path and os.path.exists(image_path):
        image_filename = f'{today_str}-{slug}.png'
        try:
            os.makedirs(output_dir, exist_ok=True)
            shutil.copy2(image_path, os.path.join(output_dir, image_filename))
        except OSError:
            image_filename = None  # コピー失敗時は画像なしで続行

    # 拡張子と画像に応じたコンテンツ生成
    file_content = _format_for_extension(ext, title, content_md, image_filename)

    # ディレクトリ作成・ファイル書き出し
    try:
        os.makedirs(output_dir, exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(file_content)
    except OSError as e:
        return {'success': False, 'reason': f'ファイル書き出しに失敗しました: {str(e)}'}

    # デプロイコマンド実行（プロセスツリー強制終了対応版）
    deploy_command = site.get('deploy_command', '').strip()
    if deploy_command:
        cmd_result = _run_command(deploy_command, cwd=output_dir, timeout=60)
        if not cmd_result['success']:
            return {
                'success': False,
                'reason': cmd_result['reason'],
                'file': filepath,
                'filename': filename,
            }

    return {
        'success': True,
        'file': filepath,
        'filename': filename,
        'image': os.path.join(output_dir, image_filename) if image_filename else None,
    }
