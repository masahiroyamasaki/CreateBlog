"""
pipeline.py — フルパイプライン実行モジュール

1. スプレッドシートからトピック・キーワード取得
2. BlogCreatorAgent → draft
3. ContentCheckerAgent → content_check
4. LegalCheckerAgent → legal_check
5. FinalCreatorAgent → final_content
6. site_idからサイトプロファイル取得
7. poster.post_to_site() で投稿
8. D列を今日の日付で更新
9. pipeline_logsに記録
10. 結果通知メール送信
"""
import os
import re
import concurrent.futures
from datetime import date

import database
import sheets
import poster
import mailer
import image_generator
from agents.blog_creator import BlogCreatorAgent
from agents.content_checker import ContentCheckerAgent
from agents.legal_checker import LegalCheckerAgent
from agents.final_creator import FinalCreatorAgent


def run(company_id: int, site_id: int = None, on_step=None) -> dict:
    """パイプラインを実行し結果dictを返す。各ステップのエラーは適切にキャッチする。
    on_step(step_name): ステップ変化のたびに呼ばれるコールバック（省略可）
    """

    result = {
        'success': False,
        'company_id': company_id,
        'site_id': site_id,
        'step': None,
        'reason': None,
        'title': None,
        'post_id': None,
        'post_result': None,
    }

    def _step(s):
        result['step'] = s
        if on_step:
            try:
                on_step(s)
            except Exception:
                pass

    # ── 会社情報取得 ──────────────────────────────────────
    _step('init')
    company = database.get_company(company_id)
    if not company:
        result['reason'] = f'会社ID {company_id} が見つかりません'
        _log(company_id, site_id, None, 'error', result['reason'])
        return result

    company_name = company.get('name', '')
    review_email = company.get('review_email', '')
    spreadsheet_id = company.get('spreadsheet_id', '')

    # ── Step 1: スプレッドシートから取得 ─────────────────
    _step('sheets_read')
    topic = ''
    keywords = ''
    sheet_row = None

    if spreadsheet_id and sheets.is_configured():
        try:
            sheet_data = sheets.get_row_for_next_article(spreadsheet_id)
            topic = sheet_data.get('topic', '')
            keywords = sheet_data.get('keywords', '')
            sheet_row = sheet_data.get('row')
        except Exception as e:
            result['reason'] = f'スプレッドシート読み取りエラー: {str(e)}'
            _log(company_id, site_id, None, 'error', result['reason'])
            return result
    else:
        result['reason'] = 'スプレッドシートが設定されていないか、Google認証情報がありません'
        _log(company_id, site_id, None, 'error', result['reason'])
        return result

    if not topic:
        result['reason'] = 'スプレッドシートに未処理のトピックが見つかりません'
        _log(company_id, site_id, None, 'error', result['reason'])
        return result

    # ── Step 2: BlogCreatorAgent → draft ─────────────────
    _step('blog_creator')
    try:
        existing_posts = database.get_recent_posts(5)
        agent_data = {
            'topic': topic,
            'keywords': keywords,
            'tone': company.get('tone', '標準'),
            'word_count': company.get('word_count', '1200'),
            'existing_posts': existing_posts,
        }
        draft = ''.join(BlogCreatorAgent().stream(agent_data))
    except Exception as e:
        result['reason'] = f'記事生成エラー: {str(e)}'
        _log(company_id, site_id, None, 'error', result['reason'])
        return result

    if not draft.strip():
        result['reason'] = '記事生成結果が空でした'
        _log(company_id, site_id, None, 'error', result['reason'])
        return result

    # タイトルを1行目から抽出
    title = _extract_title(draft) or topic

    # ── Step 3: ContentCheckerAgent → content_check ───────
    _step('content_checker')
    try:
        content_check = ''.join(ContentCheckerAgent().stream({'draft': draft}))
    except Exception as e:
        content_check = f'コンテンツチェックエラー: {str(e)}'

    # ── Step 4: LegalCheckerAgent → legal_check ───────────
    _step('legal_checker')
    try:
        legal_check = ''.join(LegalCheckerAgent().stream({'draft': draft}))
    except Exception as e:
        legal_check = f'法務チェックエラー: {str(e)}'

    # ── Step 5: FinalCreatorAgent → final_content ──────────
    _step('final_creator')
    try:
        final_data = {
            'draft': draft,
            'content_check': content_check,
            'legal_check': legal_check,
            'topic': topic,
            'keywords': keywords,
            'tone': company.get('tone', '標準'),
            'word_count': company.get('word_count', '1200'),
        }
        final_content = ''.join(FinalCreatorAgent().stream(final_data))
    except Exception as e:
        result['reason'] = f'最終記事生成エラー: {str(e)}'
        _log(company_id, site_id, None, 'error', result['reason'])
        return result

    if not final_content.strip():
        result['reason'] = '最終記事生成結果が空でした'
        _log(company_id, site_id, None, 'error', result['reason'])
        return result

    # タイトルを最終版から再抽出
    final_title = _extract_title(final_content) or title

    # ── Step 5.5: DBに記事保存 ────────────────────────────
    _step('save_post')
    try:
        post_id = database.create_post(
            title=final_title,
            content=final_content,
            content_check=content_check,
            legal_check=legal_check,
            tags='',
            company_id=company_id,
        )
        result['post_id'] = post_id
        result['title'] = final_title
    except Exception as e:
        result['reason'] = f'記事保存エラー: {str(e)}'
        _log(company_id, site_id, None, 'error', result['reason'])
        return result

    # ── Step 5.6: トップ画像生成 ──────────────────────────
    image_path = None
    gcp_project = company.get('gcp_project_id', '').strip()
    if company.get('image_generation_enabled') and image_generator.is_configured(gcp_project):
        _step('image_gen')
        try:
            img_prompt = image_generator.build_prompt(final_title, topic, keywords)
            img_out = os.path.join('generated_images', f'{post_id}.png')
            img_result = image_generator.generate_image(
                prompt=img_prompt,
                output_path=img_out,
                project_id=gcp_project,
                location=company.get('gcp_location', 'us-central1') or 'us-central1',
            )
            if img_result.get('success'):
                image_path = img_result['path']
            else:
                _log(company_id, site_id, post_id, 'warning',
                     f'画像生成スキップ: {img_result.get("reason")}')
        except Exception as e:
            _log(company_id, site_id, post_id, 'warning', f'画像生成例外（続行）: {e}')

    # ── Step 6: サイトプロファイル取得 ───────────────────
    _step('get_site')
    site = None
    if site_id:
        site = database.get_site(site_id)
        if not site:
            result['reason'] = f'サイトID {site_id} が見つかりません'
            _log(company_id, site_id, post_id, 'error', result['reason'])
            _notify(review_email, company_name, final_title, result, None)
            return result
    else:
        # site_idが指定されていない場合は会社のWordPress設定にフォールバック
        wp_url  = company.get('wp_url', '')
        wp_user = company.get('wp_username', '')
        wp_pass = company.get('wp_password', '')
        if not (wp_url and wp_user and wp_pass):
            result['step'] = 'get_site'
            result['reason'] = (
                '投稿先サイトが選択されていません。'
                '会社詳細ページの「パイプライン実行」でサイトを選択してから実行してください。'
            )
            _log(company_id, None, None, 'error', result['reason'])
            return result
        site = {
            'site_type': 'wordpress',
            'name': company_name,
            'template_file': company.get('template_file', ''),
            'wp_url': wp_url,
            'wp_username': wp_user,
            'wp_password': wp_pass,
            'wp_status': company.get('wp_status', 'draft'),
        }

    # ── Step 7: 投稿 ──────────────────────────────────────
    _step('post')

    # 設定済みフィールドからサイトタイプを自動判定（site_type設定に依存しない）
    detected_type = poster.detect_site_type(site or {})

    if not detected_type:
        result['reason'] = (
            '投稿先の設定が不完全です。\n'
            'サイト編集画面でWordPress情報・APIエンドポイント・出力ディレクトリの'
            'いずれかを設定してください。'
        )
        _log(company_id, site_id, post_id, 'error', result['reason'])
        return result

    # 自動判定したタイプに応じて進捗ステップを更新
    if detected_type == 'wordpress':
        _step('post_converting')
        _step('post_wp_api')
    elif detected_type == 'api_cms':
        _step('post_converting')
        _step('post_cms_api')
    elif detected_type == 'static':
        _step('post_file_write')
        if (site or {}).get('deploy_command', '').strip():
            _step('post_deploying')

    try:
        post_result = poster.post_to_site(site, final_title, final_content, image_path=image_path)
        result['post_result'] = post_result
    except Exception as e:
        post_result = {'success': False, 'reason': f'投稿処理エラー: {str(e)}'}
        result['post_result'] = post_result

    if post_result.get('success'):
        # WordPress の場合は投稿済みフラグを更新
        if detected_type == 'wordpress':
            link = post_result.get('link', '')
            try:
                database.mark_wp_posted(post_id, link)
            except Exception:
                pass
        _log(company_id, site_id, post_id, 'success', f'投稿成功: {final_title}')

        # ── Step 8: スプレッドシートのD列を更新（成功時のみ）────
        _step('sheets_write')
        if sheet_row and spreadsheet_id and sheets.is_configured():
            today_str = date.today().strftime('%Y/%m/%d')
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(sheets.write_to_d_column, sheet_row, today_str, spreadsheet_id)
                try:
                    future.result(timeout=30)
                except concurrent.futures.TimeoutError:
                    _log(company_id, site_id, post_id, 'warning',
                         'スプレッドシート更新タイムアウト（30秒）')
                except Exception as e:
                    _log(company_id, site_id, post_id, 'warning',
                         f'スプレッドシート更新エラー: {str(e)}')

    else:
        # 投稿失敗 → スプレッドシート更新はスキップして即エラー通知
        _log(company_id, site_id, post_id, 'post_error',
             post_result.get('reason', '不明なエラー'))

        _step('notify')
        site_name = site.get('name', '') if site else None
        _notify(review_email, company_name, final_title, post_result, site_name)

        _step('done')
        result['success'] = False
        result['reason'] = post_result.get('reason', '投稿に失敗しました')
        # 静的サイトの場合はファイルパスも付与
        if post_result.get('file'):
            result['file'] = post_result['file']
        return result

    # ── Step 9: 結果通知メール ────────────────────────────
    _step('notify')
    site_name = site.get('name', '') if site else None
    _notify(review_email, company_name, final_title, post_result, site_name)

    # ── 完了 ──────────────────────────────────────────────
    _step('done')
    result['success'] = True
    return result


# ─── ヘルパー関数 ─────────────────────────────────────────

def _extract_title(content: str) -> str:
    """Markdownコンテンツから# H1タイトルを抽出する"""
    for line in content.splitlines():
        line = line.strip()
        if line.startswith('# '):
            return line[2:].strip()
    return ''


def _log(company_id, site_id, post_id, status, message=''):
    """pipeline_logsにログを記録する（エラーは無視）"""
    try:
        database.add_pipeline_log(company_id, site_id, post_id, status, message)
    except Exception:
        pass


def _notify(to_email, company_name, title, post_result, site_name=None):
    """結果通知メールを送信する（エラーは無視）"""
    if not to_email:
        return
    try:
        mailer.send_result_notification(
            to_email=to_email,
            company_name=company_name,
            title=title,
            post_result=post_result,
            site_name=site_name,
        )
    except Exception:
        pass
