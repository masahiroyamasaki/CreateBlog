import re
import sqlite3
from datetime import datetime

DB_PATH = "blog.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                content_check TEXT,
                legal_check TEXT,
                tags TEXT,
                created_at TEXT NOT NULL,
                company_id INTEGER,
                wp_token TEXT DEFAULT '',
                wp_posted INTEGER DEFAULT 0,
                wp_posted_url TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                blog_url TEXT DEFAULT '',
                spreadsheet_url TEXT DEFAULT '',
                spreadsheet_id TEXT DEFAULT '',
                tone TEXT DEFAULT '標準',
                word_count TEXT DEFAULT '1200',
                is_active INTEGER DEFAULT 1,
                schedule_enabled INTEGER DEFAULT 0,
                schedule_days TEXT DEFAULT '',
                schedule_time TEXT DEFAULT '09:00',
                wp_url TEXT DEFAULT '',
                wp_username TEXT DEFAULT '',
                wp_password TEXT DEFAULT '',
                wp_status TEXT DEFAULT 'draft',
                template_file TEXT DEFAULT '',
                review_email TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                site_type TEXT NOT NULL DEFAULT 'wordpress',
                template_file TEXT DEFAULT '',
                wp_url TEXT DEFAULT '',
                wp_username TEXT DEFAULT '',
                wp_password TEXT DEFAULT '',
                wp_status TEXT DEFAULT 'draft',
                api_endpoint TEXT DEFAULT '',
                api_key TEXT DEFAULT '',
                api_headers TEXT DEFAULT '',
                api_body_template TEXT DEFAULT '',
                output_dir TEXT DEFAULT '',
                deploy_command TEXT DEFAULT '',
                is_active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pipeline_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER,
                site_id INTEGER,
                post_id INTEGER,
                status TEXT NOT NULL,
                message TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        # 既存テーブルへの列追加（移行）
        _migrate(conn)
        conn.commit()


def _migrate(conn):
    migrations = [
        ("posts",     "company_id",        "INTEGER"),
        ("posts",     "wp_token",          "TEXT DEFAULT ''"),
        ("posts",     "wp_posted",         "INTEGER DEFAULT 0"),
        ("posts",     "wp_posted_url",     "TEXT DEFAULT ''"),
        ("companies", "schedule_enabled", "INTEGER DEFAULT 0"),
        ("companies", "schedule_days",    "TEXT DEFAULT ''"),
        ("companies", "schedule_time",    "TEXT DEFAULT '09:00'"),
        ("companies", "wp_url",           "TEXT DEFAULT ''"),
        ("companies", "wp_username",      "TEXT DEFAULT ''"),
        ("companies", "wp_password",      "TEXT DEFAULT ''"),
        ("companies", "wp_status",        "TEXT DEFAULT 'draft'"),
        ("companies", "template_file",    "TEXT DEFAULT ''"),
        ("companies", "review_email",              "TEXT DEFAULT ''"),
        ("companies", "gcp_project_id",           "TEXT DEFAULT ''"),
        ("companies", "gcp_location",             "TEXT DEFAULT 'us-central1'"),
        ("companies", "image_generation_enabled", "INTEGER DEFAULT 0"),
        ("sites",     "output_extension",         "TEXT DEFAULT '.html'"),
    ]
    for table, col, typedef in migrations:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}")
        except Exception:
            pass


# ===== Posts =====

def get_all_posts():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, tags, created_at, company_id FROM posts ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_posts_by_company(company_id: int):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, tags, created_at FROM posts WHERE company_id=? ORDER BY created_at DESC",
            (company_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_post(post_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
    return dict(row) if row else None


def create_post(title, content, content_check, legal_check, tags, company_id=None):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO posts (title,content,content_check,legal_check,tags,created_at,company_id) VALUES (?,?,?,?,?,?,?)",
            (title, content, content_check, legal_check, tags, datetime.now().isoformat(), company_id),
        )
        conn.commit()
    return cur.lastrowid


def get_recent_posts(limit: int = 5):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT title, content FROM posts ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def set_wp_token(post_id: int, token: str):
    with get_conn() as conn:
        conn.execute("UPDATE posts SET wp_token=? WHERE id=?", (token, post_id))
        conn.commit()


def get_post_by_token(token: str):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM posts WHERE wp_token=?", (token,)).fetchone()
    return dict(row) if row else None


def mark_wp_posted(post_id: int, url: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE posts SET wp_posted=1, wp_posted_url=? WHERE id=?", (url, post_id)
        )
        conn.commit()


def delete_post(post_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM posts WHERE id=?", (post_id,))
        conn.commit()


# ===== Companies =====

def extract_spreadsheet_id(url_or_id: str) -> str:
    if not url_or_id:
        return ""
    m = re.search(r"/d/([a-zA-Z0-9_-]+)", url_or_id)
    return m.group(1) if m else url_or_id.strip()


def get_all_companies():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM companies ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def get_company(company_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()
    return dict(row) if row else None


def create_company(name, blog_url, spreadsheet_url, tone, word_count,
                   schedule_enabled=0, schedule_days="", schedule_time="09:00",
                   wp_url="", wp_username="", wp_password="", wp_status="draft",
                   review_email="",
                   gcp_project_id="", gcp_location="us-central1",
                   image_generation_enabled=0):
    sid = extract_spreadsheet_id(spreadsheet_url)
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO companies
               (name,blog_url,spreadsheet_url,spreadsheet_id,tone,word_count,
                schedule_enabled,schedule_days,schedule_time,
                wp_url,wp_username,wp_password,wp_status,review_email,
                gcp_project_id,gcp_location,image_generation_enabled,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (name, blog_url, spreadsheet_url, sid, tone, word_count,
             schedule_enabled, schedule_days, schedule_time,
             wp_url, wp_username, wp_password, wp_status, review_email,
             gcp_project_id, gcp_location, image_generation_enabled,
             datetime.now().isoformat()),
        )
        conn.commit()
    return cur.lastrowid


def update_company(company_id, name, blog_url, spreadsheet_url, tone, word_count, is_active,
                   schedule_enabled=0, schedule_days="", schedule_time="09:00",
                   wp_url="", wp_username="", wp_password="", wp_status="draft",
                   review_email="",
                   gcp_project_id="", gcp_location="us-central1",
                   image_generation_enabled=0):
    sid = extract_spreadsheet_id(spreadsheet_url)
    with get_conn() as conn:
        conn.execute(
            """UPDATE companies SET
               name=?,blog_url=?,spreadsheet_url=?,spreadsheet_id=?,tone=?,word_count=?,is_active=?,
               schedule_enabled=?,schedule_days=?,schedule_time=?,
               wp_url=?,wp_username=?,wp_password=?,wp_status=?,review_email=?,
               gcp_project_id=?,gcp_location=?,image_generation_enabled=?
               WHERE id=?""",
            (name, blog_url, spreadsheet_url, sid, tone, word_count, is_active,
             schedule_enabled, schedule_days, schedule_time,
             wp_url, wp_username, wp_password, wp_status, review_email,
             gcp_project_id, gcp_location, image_generation_enabled,
             company_id),
        )
        conn.commit()


def set_company_template(company_id: int, template_file: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE companies SET template_file=? WHERE id=?", (template_file, company_id)
        )
        conn.commit()


def delete_company(company_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM companies WHERE id=?", (company_id,))
        conn.commit()


# ===== Sites =====

def get_sites_by_company(company_id: int) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM sites WHERE company_id=? ORDER BY created_at DESC",
            (company_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_site(site_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
    return dict(row) if row else None


def create_site(
    company_id: int,
    name: str,
    site_type: str,
    template_file: str = '',
    wp_url: str = '',
    wp_username: str = '',
    wp_password: str = '',
    wp_status: str = 'draft',
    api_endpoint: str = '',
    api_key: str = '',
    api_headers: str = '',
    api_body_template: str = '',
    output_dir: str = '',
    deploy_command: str = '',
    output_extension: str = '.html',
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO sites
               (company_id, name, site_type, template_file,
                wp_url, wp_username, wp_password, wp_status,
                api_endpoint, api_key, api_headers, api_body_template,
                output_dir, deploy_command, output_extension, is_active, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
            (company_id, name, site_type, template_file,
             wp_url, wp_username, wp_password, wp_status,
             api_endpoint, api_key, api_headers, api_body_template,
             output_dir, deploy_command, output_extension,
             datetime.now().isoformat()),
        )
        conn.commit()
    return cur.lastrowid


def update_site(
    site_id: int,
    name: str,
    site_type: str,
    template_file: str = '',
    wp_url: str = '',
    wp_username: str = '',
    wp_password: str = '',
    wp_status: str = 'draft',
    api_endpoint: str = '',
    api_key: str = '',
    api_headers: str = '',
    api_body_template: str = '',
    output_dir: str = '',
    deploy_command: str = '',
    output_extension: str = '.html',
    is_active: int = 1,
):
    with get_conn() as conn:
        conn.execute(
            """UPDATE sites SET
               name=?, site_type=?, template_file=?,
               wp_url=?, wp_username=?, wp_password=?, wp_status=?,
               api_endpoint=?, api_key=?, api_headers=?, api_body_template=?,
               output_dir=?, deploy_command=?, output_extension=?, is_active=?
               WHERE id=?""",
            (name, site_type, template_file,
             wp_url, wp_username, wp_password, wp_status,
             api_endpoint, api_key, api_headers, api_body_template,
             output_dir, deploy_command, output_extension, is_active,
             site_id),
        )
        conn.commit()


def delete_site(site_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM sites WHERE id=?", (site_id,))
        conn.commit()


def set_site_template(site_id: int, template_file: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE sites SET template_file=? WHERE id=?", (template_file, site_id)
        )
        conn.commit()


# ===== Pipeline Logs =====

def add_pipeline_log(
    company_id: int,
    site_id: int,
    post_id: int,
    status: str,
    message: str = '',
):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO pipeline_logs
               (company_id, site_id, post_id, status, message, created_at)
               VALUES (?,?,?,?,?,?)""",
            (company_id, site_id, post_id, status, message, datetime.now().isoformat()),
        )
        conn.commit()


def get_pipeline_logs(company_id: int, limit: int = 20) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT pl.*, s.name as site_name
               FROM pipeline_logs pl
               LEFT JOIN sites s ON pl.site_id = s.id
               WHERE pl.company_id=?
               ORDER BY pl.created_at DESC
               LIMIT ?""",
            (company_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]
