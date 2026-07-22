import os
import secrets
import threading
import uuid
from datetime import date, datetime
from flask import Flask, render_template, request, jsonify, Response, stream_with_context, redirect, url_for
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import database
import sheets
import wordpress
import mailer
import pipeline
import poster
from agents.blog_creator import BlogCreatorAgent
from agents.content_checker import ContentCheckerAgent
from agents.legal_checker import LegalCheckerAgent
from agents.final_creator import FinalCreatorAgent

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", os.getenv("SECRET_KEY", "dev-secret-key"))

@app.template_filter("to_jst")
def _to_jst(dt):
    """UTC naive datetime を JST (UTC+9) に変換する"""
    if dt is None:
        return dt
    from datetime import timedelta
    return dt + timedelta(hours=9)

# ── MySQL + SQLAlchemy (新システム) ──────────────────────────────────────────
try:
    from config import Config
    from models import db
    from auth import login_manager
    from routes import designer_bp

    app.config.from_object(Config)
    db.init_app(app)
    login_manager.init_app(app)
    app.register_blueprint(designer_bp)

    with app.app_context():
        db.create_all()          # テーブルが存在しなければ作成

    from db_migrate import auto_migrate
    auto_migrate(app, db)        # モデルと差分があるカラムを自動追加

    # 料金プランの初期データをシード（プラットフォームタイプ単位で未登録のもののみ追加）
    with app.app_context():
        from models import PricingPlan as _PP
        from pricing import PLANS
        for pt, plans in PLANS.items():
            if _PP.query.filter_by(platform_type=pt).count() == 0:
                last = _PP.query.order_by(_PP.sort_order.desc()).first()
                order = (last.sort_order + 1) if last else 0
                for p in plans:
                    db.session.add(_PP(platform_type=pt, monthly_posts=p["posts"], monthly_fee=p["fee"], sort_order=order))
                    order += 1
                db.session.commit()

    # 料金プランをすべてのテンプレートに自動注入
    @app.context_processor
    def _inject_pricing():
        try:
            from models import PricingPlan as _PP
            plans: dict = {}
            for p in _PP.query.order_by(_PP.platform_type, _PP.sort_order).all():
                plans.setdefault(p.platform_type, []).append(
                    {"id": p.id, "posts": p.monthly_posts, "fee": p.monthly_fee}
                )
            return {"pricing_plans": plans}
        except Exception:
            return {"pricing_plans": {}}
except Exception as _mysql_err:
    import logging
    logging.warning(f"MySQL 接続スキップ（既存 SQLite 機能は継続）: {_mysql_err}")

# ── SQLite (既存システム) ─────────────────────────────────────────────────────
database.init_db()

TEMPLATE_UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads", "templates")

# パイプライン実行状態（in-memory）
_pipeline_runs = {}


# ===== Pages =====

@app.route("/")
def index():
    return redirect("/designer/clients")


@app.route("/lp")
def lp():
    sent = request.args.get("sent") == "1"
    error = request.args.get("error") == "1"
    return render_template("lp.html", sent=sent, error=error)


@app.route("/lp/contact", methods=["POST"])
def lp_contact():
    name    = request.form.get("name", "").strip()
    company = request.form.get("company", "").strip()
    email   = request.form.get("email", "").strip()
    phone   = request.form.get("phone", "").strip()
    message = request.form.get("message", "").strip()
    if not name or not email or not message:
        return redirect("/lp?error=1#contact")
    mailer.send_contact_email(name, company, email, phone, message)
    return redirect("/lp?sent=1#contact")


@app.route("/create")
def create():
    company_id = request.args.get("company_id", type=int)
    company = database.get_company(company_id) if company_id else None
    return render_template(
        "create.html",
        company=company,
        mail_configured=mailer.is_configured(),
    )


@app.route("/view/<int:post_id>")
def view(post_id):
    post = database.get_post(post_id)
    if not post:
        return "記事が見つかりません", 404
    return render_template("view.html", post=post)


@app.route("/delete/<int:post_id>", methods=["POST"])
def delete_post(post_id):
    database.delete_post(post_id)
    return jsonify({"success": True})


# ===== Company pages =====

@app.route("/companies")
def companies():
    return redirect("/designer/clients")


@app.route("/companies/new", methods=["GET", "POST"])
def company_new():
    if request.method == "POST":
        days = ",".join(request.form.getlist("schedule_days"))
        database.create_company(
            name=request.form["name"],
            blog_url=request.form.get("blog_url", ""),
            spreadsheet_url=request.form.get("spreadsheet_url", ""),
            tone=request.form.get("tone", "標準"),
            word_count=request.form.get("word_count", "1200"),
            schedule_enabled=1 if request.form.get("schedule_enabled") else 0,
            schedule_days=days,
            schedule_time=request.form.get("schedule_time", "09:00"),
            wp_url=request.form.get("wp_url", ""),
            wp_username=request.form.get("wp_username", ""),
            wp_password=request.form.get("wp_password", ""),
            wp_status=request.form.get("wp_status", "draft"),
            review_email=request.form.get("review_email", ""),
            gcp_project_id=request.form.get("gcp_project_id", ""),
            gcp_location=request.form.get("gcp_location", "us-central1"),
            image_generation_enabled=1 if request.form.get("image_generation_enabled") else 0,
        )
        return redirect(url_for("companies"))
    return render_template("company_form.html", company=None)


@app.route("/companies/<int:company_id>")
def company_detail(company_id):
    company = database.get_company(company_id)
    if not company:
        return "会社が見つかりません", 404
    posts = database.get_posts_by_company(company_id)
    sites_list = database.get_sites_by_company(company_id)
    return render_template("company_detail.html", company=company, posts=posts, sites_list=sites_list)


@app.route("/companies/<int:company_id>/edit", methods=["GET", "POST"])
def company_edit(company_id):
    company = database.get_company(company_id)
    if not company:
        return "会社が見つかりません", 404
    if request.method == "POST":
        days = ",".join(request.form.getlist("schedule_days"))
        database.update_company(
            company_id=company_id,
            name=request.form["name"],
            blog_url=request.form.get("blog_url", ""),
            spreadsheet_url=request.form.get("spreadsheet_url", ""),
            tone=request.form.get("tone", "標準"),
            word_count=request.form.get("word_count", "1200"),
            is_active=1 if request.form.get("is_active") else 0,
            schedule_enabled=1 if request.form.get("schedule_enabled") else 0,
            schedule_days=days,
            schedule_time=request.form.get("schedule_time", "09:00"),
            wp_url=request.form.get("wp_url", ""),
            wp_username=request.form.get("wp_username", ""),
            wp_password=request.form.get("wp_password", ""),
            wp_status=request.form.get("wp_status", "draft"),
            review_email=request.form.get("review_email", ""),
            gcp_project_id=request.form.get("gcp_project_id", ""),
            gcp_location=request.form.get("gcp_location", "us-central1"),
            image_generation_enabled=1 if request.form.get("image_generation_enabled") else 0,
        )
        return redirect(url_for("company_detail", company_id=company_id))
    return render_template("company_form.html", company=company)


@app.route("/companies/<int:company_id>/delete", methods=["POST"])
def company_delete(company_id):
    database.delete_company(company_id)
    return jsonify({"success": True})


@app.route("/companies/<int:company_id>/upload-template", methods=["POST"])
def upload_template(company_id):
    company = database.get_company(company_id)
    if not company:
        return "会社が見つかりません", 404

    f = request.files.get("template_file")
    if not f or f.filename == "":
        return redirect(url_for("company_detail", company_id=company_id))

    # 既存テンプレートを削除
    old_path = company.get("template_file") or ""
    if old_path and os.path.exists(old_path):
        os.remove(old_path)

    os.makedirs(TEMPLATE_UPLOAD_DIR, exist_ok=True)
    filename = f"company_{company_id}.html"
    save_path = os.path.join(TEMPLATE_UPLOAD_DIR, filename)
    f.save(save_path)

    database.set_company_template(company_id, save_path)
    return redirect(url_for("company_detail", company_id=company_id))


@app.route("/companies/<int:company_id>/delete-template", methods=["POST"])
def delete_template(company_id):
    company = database.get_company(company_id)
    if not company:
        return jsonify({"success": False}), 404

    path = company.get("template_file") or ""
    if path and os.path.exists(path):
        os.remove(path)

    database.set_company_template(company_id, "")
    return jsonify({"success": True})


@app.route("/api/template-content")
def template_content():
    company_id = request.args.get("company_id", type=int)
    if not company_id:
        return jsonify({"has_template": False})
    company = database.get_company(company_id)
    if not company or not company.get("template_file"):
        return jsonify({"has_template": False})
    try:
        with open(company["template_file"], encoding="utf-8") as fp:
            return jsonify({"has_template": True, "template": fp.read()})
    except FileNotFoundError:
        return jsonify({"has_template": False})


# ===== Agent API =====

@app.route("/api/create-draft", methods=["POST"])
def create_draft():
    data = request.get_json()
    data["existing_posts"] = database.get_recent_posts(5)

    def generate():
        yield from BlogCreatorAgent().stream(data)

    return Response(stream_with_context(generate()), mimetype="text/plain; charset=utf-8")


@app.route("/api/check-content", methods=["POST"])
def check_content():
    data = request.get_json()

    def generate():
        yield from ContentCheckerAgent().stream(data)

    return Response(stream_with_context(generate()), mimetype="text/plain; charset=utf-8")


@app.route("/api/check-legal", methods=["POST"])
def check_legal():
    data = request.get_json()

    def generate():
        yield from LegalCheckerAgent().stream(data)

    return Response(stream_with_context(generate()), mimetype="text/plain; charset=utf-8")


@app.route("/api/create-final", methods=["POST"])
def create_final():
    data = request.get_json()

    def generate():
        yield from FinalCreatorAgent().stream(data)

    return Response(stream_with_context(generate()), mimetype="text/plain; charset=utf-8")


@app.route("/api/save-article", methods=["POST"])
def save_article():
    data = request.get_json()
    post_id = database.create_post(
        title=data.get("title", "無題"),
        content=data.get("content", ""),
        content_check=data.get("content_check", ""),
        legal_check=data.get("legal_check", ""),
        tags=data.get("tags", ""),
        company_id=data.get("company_id") or None,
    )
    return jsonify({"success": True, "post_id": post_id})



# ===== WordPress API =====

@app.route("/api/wp-post", methods=["POST"])
def wp_post():
    data = request.get_json()
    company_id = data.get("company_id")
    if not company_id:
        return jsonify({"success": False, "reason": "company_id が指定されていません"}), 400

    company = database.get_company(int(company_id))
    if not company or not wordpress.is_configured(company):
        return jsonify({"success": False, "reason": "WordPress の接続情報が設定されていません"}), 400

    result = wordpress.post_article(
        company=company,
        title=data.get("title", "無題"),
        content_md=data.get("content", ""),
    )
    return jsonify(result)


@app.route("/api/wp-test", methods=["POST"])
def wp_test():
    data = request.get_json()
    company_id = data.get("company_id")
    if not company_id:
        return jsonify({"success": False, "reason": "company_id が指定されていません"}), 400

    company = database.get_company(int(company_id))
    if not company or not wordpress.is_configured(company):
        return jsonify({"success": False, "reason": "接続情報が未設定です"}), 400

    result = wordpress.test_connection(company)
    return jsonify(result)


# ===== Review & Approve flow =====

@app.route("/api/send-review-email", methods=["POST"])
def send_review_email():
    data = request.get_json()
    post_id    = data.get("post_id")
    company_id = data.get("company_id")

    if not post_id:
        return jsonify({"success": False, "reason": "post_id が指定されていません"}), 400

    post    = database.get_post(post_id)
    company = database.get_company(int(company_id)) if company_id else None

    if not post:
        return jsonify({"success": False, "reason": "記事が見つかりません"}), 404

    token = secrets.token_urlsafe(32)
    database.set_wp_token(post_id, token)

    base_url    = os.getenv("BASE_URL", "http://localhost:5000")
    approve_url = f"{base_url}/approve/{token}"
    company_name = (company or {}).get("name", "（会社未設定）")

    # 本文の最初の300文字を抜粋
    excerpt = post["content"][:300].replace("\n", "<br>")
    if len(post["content"]) > 300:
        excerpt += "…"

    to_email = (company or {}).get("review_email", "")
    result = mailer.send_review_email(
        to_email=to_email,
        company_name=company_name,
        title=post["title"],
        excerpt=excerpt,
        approve_url=approve_url,
    )
    return jsonify({**result, "approve_url": approve_url, "token": token})


@app.route("/approve/<token>")
def approve_page(token):
    post = database.get_post_by_token(token)
    if not post:
        return render_template("approve.html", error="このリンクは無効または期限切れです。"), 404
    company = database.get_company(post["company_id"]) if post.get("company_id") else None
    return render_template("approve.html", post=post, company=company, token=token)


@app.route("/api/wp-approve", methods=["POST"])
def wp_approve():
    data  = request.get_json()
    token = data.get("token")

    post = database.get_post_by_token(token)
    if not post:
        return jsonify({"success": False, "reason": "無効なトークンです"}), 400
    if post.get("wp_posted"):
        return jsonify({"success": False, "reason": "この記事はすでに投稿済みです"})

    company = database.get_company(post["company_id"]) if post.get("company_id") else None
    if not company or not wordpress.is_configured(company):
        return jsonify({"success": False, "reason": "WordPress の接続情報が設定されていません"}), 400

    result = wordpress.post_article(company, post["title"], post["content"])
    if result["success"]:
        database.mark_wp_posted(post["id"], result.get("link", ""))

    return jsonify(result)


# ===== Site CRUD =====

@app.route("/companies/<int:company_id>/sites/new", methods=["GET", "POST"])
def site_new(company_id):
    company = database.get_company(company_id)
    if not company:
        return "会社が見つかりません", 404

    if request.method == "POST":
        template_file_path = ""
        uploaded = request.files.get("template_file")
        if uploaded and uploaded.filename:
            site_id_tmp = "new"
            os.makedirs(TEMPLATE_UPLOAD_DIR, exist_ok=True)
            # 仮保存してから後でsite_idで上書き
            tmp_filename = f"site_tmp_{secrets.token_hex(8)}.html"
            tmp_path = os.path.join(TEMPLATE_UPLOAD_DIR, tmp_filename)
            uploaded.save(tmp_path)
            template_file_path = tmp_path

        new_site_id = database.create_site(
            company_id=company_id,
            name=request.form.get("name", ""),
            site_type=request.form.get("site_type", "wordpress"),
            template_file=template_file_path,
            wp_url=request.form.get("wp_url", ""),
            wp_username=request.form.get("wp_username", ""),
            wp_password=request.form.get("wp_password", ""),
            wp_status=request.form.get("wp_status", "draft"),
            api_endpoint=request.form.get("api_endpoint", ""),
            api_key=request.form.get("api_key", ""),
            api_headers=request.form.get("api_headers", ""),
            api_body_template=request.form.get("api_body_template", ""),
            output_dir=request.form.get("output_dir", ""),
            deploy_command=request.form.get("deploy_command", ""),
            output_extension=request.form.get("output_extension", ".html"),
        )

        # テンプレートを正式なファイル名でリネーム
        if template_file_path and os.path.exists(template_file_path):
            final_filename = f"site_{new_site_id}.html"
            final_path = os.path.join(TEMPLATE_UPLOAD_DIR, final_filename)
            os.rename(template_file_path, final_path)
            database.set_site_template(new_site_id, final_path)

        return redirect(url_for("company_detail", company_id=company_id))

    return render_template("site_form.html", site=None, company_id=company_id)


@app.route("/sites/<int:site_id>/edit", methods=["GET", "POST"])
def site_edit(site_id):
    site = database.get_site(site_id)
    if not site:
        return "サイトが見つかりません", 404
    company_id = site["company_id"]

    if request.method == "POST":
        database.update_site(
            site_id=site_id,
            name=request.form.get("name", ""),
            site_type=request.form.get("site_type", "wordpress"),
            template_file=site.get("template_file", ""),
            wp_url=request.form.get("wp_url", ""),
            wp_username=request.form.get("wp_username", ""),
            wp_password=request.form.get("wp_password", ""),
            wp_status=request.form.get("wp_status", "draft"),
            api_endpoint=request.form.get("api_endpoint", ""),
            api_key=request.form.get("api_key", ""),
            api_headers=request.form.get("api_headers", ""),
            api_body_template=request.form.get("api_body_template", ""),
            output_dir=request.form.get("output_dir", ""),
            deploy_command=request.form.get("deploy_command", ""),
            output_extension=request.form.get("output_extension", ".html"),
            is_active=1 if request.form.get("is_active") else 0,
        )

        # テンプレートファイルのアップロード処理
        uploaded = request.files.get("template_file")
        if uploaded and uploaded.filename:
            old_path = site.get("template_file") or ""
            if old_path and os.path.exists(old_path):
                os.remove(old_path)
            os.makedirs(TEMPLATE_UPLOAD_DIR, exist_ok=True)
            filename = f"site_{site_id}.html"
            save_path = os.path.join(TEMPLATE_UPLOAD_DIR, filename)
            uploaded.save(save_path)
            database.set_site_template(site_id, save_path)

        return redirect(url_for("company_detail", company_id=company_id))

    return render_template("site_form.html", site=site, company_id=company_id)


@app.route("/sites/<int:site_id>/delete", methods=["POST"])
def site_delete(site_id):
    site = database.get_site(site_id)
    if not site:
        return jsonify({"success": False, "reason": "サイトが見つかりません"}), 404

    # テンプレートファイルを削除
    template_path = site.get("template_file") or ""
    if template_path and os.path.exists(template_path):
        try:
            os.remove(template_path)
        except OSError:
            pass

    database.delete_site(site_id)
    return jsonify({"success": True})


@app.route("/sites/<int:site_id>/upload-template", methods=["POST"])
def site_upload_template(site_id):
    site = database.get_site(site_id)
    if not site:
        return "サイトが見つかりません", 404

    f = request.files.get("template_file")
    if not f or f.filename == "":
        return redirect(url_for("site_edit", site_id=site_id))

    # 既存テンプレートを削除
    old_path = site.get("template_file") or ""
    if old_path and os.path.exists(old_path):
        try:
            os.remove(old_path)
        except OSError:
            pass

    os.makedirs(TEMPLATE_UPLOAD_DIR, exist_ok=True)
    filename = f"site_{site_id}.html"
    save_path = os.path.join(TEMPLATE_UPLOAD_DIR, filename)
    f.save(save_path)

    database.set_site_template(site_id, save_path)
    return redirect(url_for("site_edit", site_id=site_id))


# ===== Pipeline API =====

@app.route("/api/run-pipeline", methods=["POST"])
def run_pipeline_api():
    data = request.get_json() or {}
    company_id = data.get("company_id")
    site_id = data.get("site_id")

    if not company_id:
        return jsonify({"success": False, "reason": "company_id が指定されていません"}), 400

    run_id = str(uuid.uuid4())
    _pipeline_runs[run_id] = {
        "status": "running",
        "step": "starting",
        "company_id": company_id,
        "site_id": site_id,
        "result": None,
    }

    def _run():
        def on_step(step):
            _pipeline_runs[run_id]["step"] = step

        try:
            result = pipeline.run(
                int(company_id),
                int(site_id) if site_id else None,
                on_step=on_step,
            )
            _pipeline_runs[run_id]["result"] = result
            _pipeline_runs[run_id]["step"] = result.get("step", "done")
            _pipeline_runs[run_id]["status"] = "success" if result.get("success") else "error"
        except Exception as e:
            _pipeline_runs[run_id]["status"] = "error"
            _pipeline_runs[run_id]["step"] = "error"
            _pipeline_runs[run_id]["result"] = {
                "success": False,
                "reason": str(e),
            }

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    return jsonify({"run_id": run_id})


@app.route("/api/pipeline-status/<run_id>")
def pipeline_status(run_id):
    run = _pipeline_runs.get(run_id)
    if not run:
        return jsonify({"error": "run_id が見つかりません"}), 404
    return jsonify(run)


@app.cli.command("publish-scheduled")
def publish_scheduled_cmd():
    """予約済みInstagram/独自HP投稿を自動実行（cron から毎分呼び出す）"""
    try:
        from scheduled_publisher import publish_due_posts
        from models import db as _db
        n = publish_due_posts(app, _db)
        print(f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC] Published {n} posts")
    except Exception as e:
        print(f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC] ERROR: {e}")
        import sys
        sys.exit(1)


@app.cli.command("run-monthly-ideas")
def run_monthly_ideas_cmd():
    """稼働企業ごとに月間投稿数分のネタを生成（毎月1日 cron から呼び出す）"""
    try:
        from batch_monthly import run_monthly_ideas_batch
        from models import db as _db
        ts = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{ts} UTC] ネタ生成バッチ開始")
        result = run_monthly_ideas_batch(app, _db)
        ts = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{ts} UTC] 完了: 企業={result['clients']}, ネタ={result['topics']}")
        if result["errors"]:
            for e in result["errors"]:
                print(f"  ERROR: {e}")
    except Exception as e:
        print(f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC] FATAL: {e}")
        import sys
        sys.exit(1)



@app.cli.command("run-monthly-billing")
def run_monthly_billing_cmd():
    """稼働中企業のデザイナーに請求書を自動作成・送付（毎月1日 cron から呼び出す）"""
    try:
        from batch_monthly import run_monthly_billing_batch
        from models import db as _db
        ts = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{ts} UTC] 請求書バッチ開始")
        result = run_monthly_billing_batch(app, _db)
        ts = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{ts} UTC] 完了: 請求書={result['invoices']}件")
        if result["errors"]:
            for e in result["errors"]:
                print(f"  ERROR: {e}")
    except Exception as e:
        print(f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC] FATAL: {e}")
        import sys
        sys.exit(1)


if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "False").lower() == "true"
    app.run(debug=debug, port=5000)
