"""image_generator.py — Google Cloud Vertex AI Imagen でブログ用トップ画像を生成する"""
import os
import base64
import requests

_GCP_CREDS_FILE = os.getenv('GCP_SERVICE_ACCOUNT_FILE', 'gcp_service_account.json')
_SCOPES = ['https://www.googleapis.com/auth/cloud-platform']
_MODEL = 'imagen-3.0-generate-001'


def is_configured(project_id: str = '') -> bool:
    """サービスアカウントファイルとプロジェクトIDが揃っているか確認"""
    if not project_id:
        return False
    try:
        from google.oauth2 import service_account  # noqa: F401
    except ImportError:
        return False
    return os.path.exists(_GCP_CREDS_FILE)


def _get_access_token() -> str:
    from google.oauth2 import service_account
    import google.auth.transport.requests

    creds = service_account.Credentials.from_service_account_file(
        _GCP_CREDS_FILE, scopes=_SCOPES
    )
    auth_req = google.auth.transport.requests.Request()
    creds.refresh(auth_req)
    return creds.token


def build_prompt(title: str, topic: str = '', keywords: str = '') -> str:
    """ブログのタイトル・トピック・キーワードから画像生成プロンプトを構築する"""
    parts = [f'Professional blog header image for an article titled "{title}"']
    if topic:
        parts.append(f'Topic: {topic}')
    if keywords:
        parts.append(f'Visual theme: {keywords}')
    parts.append(
        'High quality, modern, clean design. '
        'No text or letters in the image. '
        'Wide 16:9 format, suitable for business blog header.'
    )
    return '. '.join(parts)


def generate_image(
    prompt: str,
    output_path: str,
    project_id: str,
    location: str = 'us-central1',
) -> dict:
    """Vertex AI Imagen で画像を生成し output_path に PNG として保存する。

    Returns:
        {'success': True, 'path': output_path} または
        {'success': False, 'reason': '...'}
    """
    try:
        token = _get_access_token()
    except Exception as e:
        return {'success': False, 'reason': f'GCP認証エラー: {e}'}

    url = (
        f'https://{location}-aiplatform.googleapis.com/v1/'
        f'projects/{project_id}/locations/{location}/'
        f'publishers/google/models/{_MODEL}:predict'
    )
    body = {
        'instances': [{'prompt': prompt}],
        'parameters': {
            'sampleCount': 1,
            'aspectRatio': '16:9',
        },
    }

    try:
        res = requests.post(
            url,
            headers={
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
            },
            json=body,
            timeout=90,
        )
    except requests.exceptions.Timeout:
        return {'success': False, 'reason': '画像生成タイムアウト（90秒）'}
    except Exception as e:
        return {'success': False, 'reason': f'画像生成リクエストエラー: {e}'}

    if res.status_code != 200:
        try:
            err = res.json()
            reason = err.get('error', {}).get('message', res.text[:300])
        except Exception:
            reason = res.text[:300]
        return {
            'success': False,
            'reason': f'Imagen API エラー (HTTP {res.status_code}): {reason}',
        }

    data = res.json()
    predictions = data.get('predictions', [])
    if not predictions:
        return {
            'success': False,
            'reason': '画像が生成されませんでした（安全フィルタでブロックされた可能性があります）',
        }

    image_b64 = predictions[0].get('bytesBase64Encoded', '')
    if not image_b64:
        return {'success': False, 'reason': '画像データが取得できませんでした'}

    parent = os.path.dirname(output_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(output_path, 'wb') as f:
        f.write(base64.b64decode(image_b64))

    return {'success': True, 'path': output_path}
