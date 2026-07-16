import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

MAIL_SERVER   = os.getenv("MAIL_SERVER", "smtp.gmail.com")
MAIL_PORT     = int(os.getenv("MAIL_PORT", "587"))
MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
MAIL_FROM     = os.getenv("MAIL_FROM", "yamasaki@rk-rpa.com")
BASE_URL      = os.getenv("BASE_URL", "http://localhost:5000")


def is_configured() -> bool:
    """SMTP 認証情報が設定されているかチェック。宛先は会社ごとに設定するため確認不要。"""
    return bool(MAIL_USERNAME and MAIL_PASSWORD)


def send_review_email(
    to_email: str,
    company_name: str,
    title: str,
    excerpt: str,
    approve_url: str,
) -> dict:
    if not is_configured():
        return {
            "success": False,
            "reason": "メール設定が未完了です。.env に MAIL_USERNAME / MAIL_PASSWORD を設定してください。",
        }
    if not to_email:
        return {
            "success": False,
            "reason": "送信先メールアドレスが設定されていません。会社設定で確認用メールアドレスを登録してください。",
        }

    html_body = f"""
<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f4f4f8;font-family:'Helvetica Neue',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f8;padding:40px 0;">
  <tr><td align="center">
    <table width="600" cellpadding="0" cellspacing="0"
           style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.08);">

      <!-- Header -->
      <tr>
        <td style="background:#6366f1;padding:28px 36px;">
          <p style="margin:0;color:rgba(255,255,255,.8);font-size:13px;">AI ブログエージェント</p>
          <h1 style="margin:6px 0 0;color:#fff;font-size:22px;font-weight:700;">記事の確認依頼</h1>
        </td>
      </tr>

      <!-- Body -->
      <tr>
        <td style="padding:32px 36px;">
          <p style="margin:0 0 6px;color:#64748b;font-size:13px;">会社名</p>
          <p style="margin:0 0 24px;font-size:16px;font-weight:700;color:#1e293b;">{company_name}</p>

          <p style="margin:0 0 6px;color:#64748b;font-size:13px;">記事タイトル</p>
          <p style="margin:0 0 24px;font-size:18px;font-weight:700;color:#1e293b;">{title}</p>

          <p style="margin:0 0 8px;color:#64748b;font-size:13px;">記事の冒頭</p>
          <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;
                      padding:16px 20px;color:#334155;font-size:14px;line-height:1.8;
                      margin-bottom:32px;">
            {excerpt}
          </div>

          <p style="margin:0 0 16px;font-size:14px;color:#475569;">
            以下のボタンをクリックして記事の全文を確認し、WordPress への投稿を承認してください。
          </p>

          <div style="text-align:center;margin:28px 0;">
            <a href="{approve_url}"
               style="display:inline-block;background:#6366f1;color:#fff;
                      text-decoration:none;font-size:16px;font-weight:700;
                      padding:16px 48px;border-radius:10px;">
              記事を確認・承認する →
            </a>
          </div>

          <p style="margin:24px 0 0;font-size:12px;color:#94a3b8;">
            ボタンが押せない場合はこちらの URL をブラウザに貼り付けてください：<br>
            <a href="{approve_url}" style="color:#6366f1;word-break:break-all;">{approve_url}</a>
          </p>
        </td>
      </tr>

      <!-- Footer -->
      <tr>
        <td style="background:#f8fafc;border-top:1px solid #e2e8f0;
                   padding:20px 36px;text-align:center;">
          <p style="margin:0;color:#94a3b8;font-size:12px;">
            AI ブログエージェント — RKパートナーズ
          </p>
        </td>
      </tr>

    </table>
  </td></tr>
</table>
</body>
</html>
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"【確認依頼】{company_name} のブログ記事: {title}"
    msg["From"]    = f"RKパートナーズ <{MAIL_FROM}>"
    msg["To"]      = to_email
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(MAIL_SERVER, MAIL_PORT) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(MAIL_USERNAME, MAIL_PASSWORD)
            smtp.send_message(msg)
        return {"success": True, "to": to_email}
    except smtplib.SMTPAuthenticationError:
        return {"success": False, "reason": "メール認証失敗。MAIL_USERNAME / MAIL_PASSWORD を確認してください。"}
    except Exception as e:
        return {"success": False, "reason": str(e)}


def send_result_notification(
    to_email: str,
    company_name: str,
    title: str,
    post_result: dict,
    site_name: str = None,
) -> dict:
    """投稿結果（成功/失敗）の通知メール"""
    if not is_configured():
        return {
            "success": False,
            "reason": "メール設定が未完了です。.env に MAIL_USERNAME / MAIL_PASSWORD を設定してください。",
        }
    if not to_email:
        return {
            "success": False,
            "reason": "送信先メールアドレスが設定されていません。",
        }

    is_success = post_result.get("success", False)
    site_label = f" — {site_name}" if site_name else ""

    if is_success:
        header_color = "#10b981"
        header_title = "投稿完了のお知らせ"
        subject_prefix = "【投稿完了】"
        link = post_result.get("link", "")
        link_html = ""
        if link:
            link_html = f"""
          <p style="margin:16px 0 6px;color:#64748b;font-size:13px;">投稿URL</p>
          <p style="margin:0 0 24px;">
            <a href="{link}" style="color:#10b981;word-break:break-all;">{link} ↗</a>
          </p>"""
        body_html = f"""
          <p style="margin:0 0 6px;color:#64748b;font-size:13px;">会社名</p>
          <p style="margin:0 0 24px;font-size:16px;font-weight:700;color:#1e293b;">{company_name}{site_label}</p>

          <p style="margin:0 0 6px;color:#64748b;font-size:13px;">記事タイトル</p>
          <p style="margin:0 0 24px;font-size:18px;font-weight:700;color:#1e293b;">{title}</p>

          <div style="background:rgba(16,185,129,.08);border:1px solid rgba(16,185,129,.3);border-radius:8px;
                      padding:16px 20px;color:#065f46;font-size:14px;margin-bottom:24px;">
            記事が正常に投稿されました。
          </div>
          {link_html}"""
    else:
        header_color = "#ef4444"
        header_title = "投稿失敗のお知らせ"
        subject_prefix = "【投稿失敗】"
        reason = post_result.get("reason", "不明なエラー")
        body_html = f"""
          <p style="margin:0 0 6px;color:#64748b;font-size:13px;">会社名</p>
          <p style="margin:0 0 24px;font-size:16px;font-weight:700;color:#1e293b;">{company_name}{site_label}</p>

          <p style="margin:0 0 6px;color:#64748b;font-size:13px;">記事タイトル</p>
          <p style="margin:0 0 24px;font-size:18px;font-weight:700;color:#1e293b;">{title}</p>

          <p style="margin:0 0 8px;color:#64748b;font-size:13px;">エラー理由</p>
          <div style="background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.3);border-radius:8px;
                      padding:16px 20px;color:#991b1b;font-size:14px;line-height:1.8;margin-bottom:24px;">
            {reason}
          </div>

          <p style="margin:0 0 0;font-size:13px;color:#475569;">
            管理画面からパイプラインを再実行するか、設定を確認してください。
          </p>"""

    html_body = f"""
<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f4f4f8;font-family:'Helvetica Neue',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f8;padding:40px 0;">
  <tr><td align="center">
    <table width="600" cellpadding="0" cellspacing="0"
           style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.08);">

      <!-- Header -->
      <tr>
        <td style="background:{header_color};padding:28px 36px;">
          <p style="margin:0;color:rgba(255,255,255,.8);font-size:13px;">AI ブログエージェント</p>
          <h1 style="margin:6px 0 0;color:#fff;font-size:22px;font-weight:700;">{header_title}</h1>
        </td>
      </tr>

      <!-- Body -->
      <tr>
        <td style="padding:32px 36px;">
          {body_html}
        </td>
      </tr>

      <!-- Footer -->
      <tr>
        <td style="background:#f8fafc;border-top:1px solid #e2e8f0;
                   padding:20px 36px;text-align:center;">
          <p style="margin:0;color:#94a3b8;font-size:12px;">
            AI ブログエージェント — RKパートナーズ
          </p>
        </td>
      </tr>

    </table>
  </td></tr>
</table>
</body>
</html>
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{subject_prefix}{company_name} のブログ記事: {title}"
    msg["From"]    = f"RKパートナーズ <{MAIL_FROM}>"
    msg["To"]      = to_email
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(MAIL_SERVER, MAIL_PORT) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(MAIL_USERNAME, MAIL_PASSWORD)
            smtp.send_message(msg)
        return {"success": True, "to": to_email}
    except smtplib.SMTPAuthenticationError:
        return {"success": False, "reason": "メール認証失敗。MAIL_USERNAME / MAIL_PASSWORD を確認してください。"}
    except Exception as e:
        return {"success": False, "reason": str(e)}
