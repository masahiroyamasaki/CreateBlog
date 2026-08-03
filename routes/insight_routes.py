"""routes/insight_routes.py — Instagram 週次インサイト管理"""
import json
from datetime import date, timedelta
from flask import render_template, request, redirect, url_for, flash, jsonify, abort
from flask_login import login_required, current_user
from models import db, Client, WeeklyInsight, WeeklyReport
from routes import designer_bp


def _assert_access(client: Client):
    if not current_user.can_access_client(client.id):
        abort(403)


def _last_monday() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())


# ── 一覧 ─────────────────────────────────────────────────────────────────────

@designer_bp.route("/clients/<int:client_id>/insights")
@login_required
def insight_list(client_id: int):
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    insights = (
        WeeklyInsight.query
        .filter_by(client_id=client_id)
        .order_by(WeeklyInsight.week_start.desc())
        .all()
    )
    return render_template("designer/insights/list.html", client=client, insights=insights)


# ── 新規作成 ─────────────────────────────────────────────────────────────────

@designer_bp.route("/clients/<int:client_id>/insights/new", methods=["GET", "POST"])
@login_required
def insight_new(client_id: int):
    client = Client.query.get_or_404(client_id)
    _assert_access(client)

    if request.method == "POST":
        week_start_str = request.form.get("week_start", "")
        try:
            week_start = date.fromisoformat(week_start_str)
        except ValueError:
            flash("週開始日が不正です", "error")
            return redirect(request.url)

        def _metrics_from_form(prefix):
            return {
                "reach":          _int(request.form.get(f"{prefix}_reach")),
                "profile_views":  _int(request.form.get(f"{prefix}_profile_views")),
                "follows":        _int(request.form.get(f"{prefix}_follows")),
                "website_clicks": _int(request.form.get(f"{prefix}_website_clicks")),
            }

        posts = []
        post_ids = request.form.getlist("post_id[]")
        for i, pid in enumerate(post_ids):
            posts.append({
                "post_id":       pid or f"post_{i+1}",
                "media_type":    request.form.getlist("post_media_type[]")[i] if i < len(request.form.getlist("post_media_type[]")) else "image",
                "caption_summary": request.form.getlist("post_caption[]")[i] if i < len(request.form.getlist("post_caption[]")) else "",
                "reach":    _int(request.form.getlist("post_reach[]")[i] if i < len(request.form.getlist("post_reach[]")) else None),
                "saved":    _int(request.form.getlist("post_saved[]")[i] if i < len(request.form.getlist("post_saved[]")) else None),
                "likes":    _int(request.form.getlist("post_likes[]")[i] if i < len(request.form.getlist("post_likes[]")) else None),
                "comments": _int(request.form.getlist("post_comments[]")[i] if i < len(request.form.getlist("post_comments[]")) else None),
                "shares":   _int(request.form.getlist("post_shares[]")[i] if i < len(request.form.getlist("post_shares[]")) else None),
                "views":    _int(request.form.getlist("post_views[]")[i] if i < len(request.form.getlist("post_views[]")) else None),
            })

        insight = WeeklyInsight(
            client_id=client_id,
            week_start=week_start,
            account_json=json.dumps({
                "industry": client.business_description or "",
                "followers": _int(request.form.get("followers")),
            }, ensure_ascii=False),
            this_week_json=json.dumps(_metrics_from_form("tw"), ensure_ascii=False),
            last_week_json=json.dumps(_metrics_from_form("lw"), ensure_ascii=False),
            four_week_avg_json=json.dumps(_metrics_from_form("fa"), ensure_ascii=False),
            posts_json=json.dumps(posts, ensure_ascii=False),
        )
        db.session.add(insight)
        db.session.commit()
        flash("インサイトデータを保存しました", "success")
        return redirect(url_for("designer.insight_detail", client_id=client_id, insight_id=insight.id))

    return render_template("designer/insights/form.html",
                           client=client,
                           insight=None,
                           default_week_start=_last_monday().isoformat())


# ── 編集 ─────────────────────────────────────────────────────────────────────

@designer_bp.route("/clients/<int:client_id>/insights/<int:insight_id>/edit", methods=["GET", "POST"])
@login_required
def insight_edit(client_id: int, insight_id: int):
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    insight = WeeklyInsight.query.get_or_404(insight_id)
    if insight.client_id != client_id:
        abort(403)

    if request.method == "POST":
        def _metrics_from_form(prefix):
            return {
                "reach":          _int(request.form.get(f"{prefix}_reach")),
                "profile_views":  _int(request.form.get(f"{prefix}_profile_views")),
                "follows":        _int(request.form.get(f"{prefix}_follows")),
                "website_clicks": _int(request.form.get(f"{prefix}_website_clicks")),
            }

        posts = []
        post_ids = request.form.getlist("post_id[]")
        for i, pid in enumerate(post_ids):
            idx = i
            def _fv(key):
                vals = request.form.getlist(key)
                return vals[idx] if idx < len(vals) else None
            posts.append({
                "post_id":       pid or f"post_{i+1}",
                "media_type":    _fv("post_media_type[]") or "image",
                "caption_summary": _fv("post_caption[]") or "",
                "reach":    _int(_fv("post_reach[]")),
                "saved":    _int(_fv("post_saved[]")),
                "likes":    _int(_fv("post_likes[]")),
                "comments": _int(_fv("post_comments[]")),
                "shares":   _int(_fv("post_shares[]")),
                "views":    _int(_fv("post_views[]")),
            })

        insight.account_json = json.dumps({
            "industry": client.business_description or "",
            "followers": _int(request.form.get("followers")),
        }, ensure_ascii=False)
        insight.this_week_json    = json.dumps(_metrics_from_form("tw"), ensure_ascii=False)
        insight.last_week_json    = json.dumps(_metrics_from_form("lw"), ensure_ascii=False)
        insight.four_week_avg_json= json.dumps(_metrics_from_form("fa"), ensure_ascii=False)
        insight.posts_json        = json.dumps(posts, ensure_ascii=False)
        # データ変更時は Stage 1 キャッシュをクリア
        insight.stage1_json = None
        insight.stage1_generated_at = None
        db.session.commit()
        flash("インサイトデータを更新しました", "success")
        return redirect(url_for("designer.insight_detail", client_id=client_id, insight_id=insight_id))

    return render_template("designer/insights/form.html",
                           client=client,
                           insight=insight,
                           default_week_start=insight.week_start.isoformat())


# ── 詳細 ─────────────────────────────────────────────────────────────────────

@designer_bp.route("/clients/<int:client_id>/insights/<int:insight_id>")
@login_required
def insight_detail(client_id: int, insight_id: int):
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    insight = WeeklyInsight.query.get_or_404(insight_id)
    if insight.client_id != client_id:
        abort(403)

    stage1   = json.loads(insight.stage1_json) if insight.stage1_json else None
    designer_report = WeeklyReport.query.filter_by(
        weekly_insight_id=insight_id, report_type="designer"
    ).order_by(WeeklyReport.generated_at.desc()).first()
    designer_result = json.loads(designer_report.result_json) if designer_report and designer_report.result_json else None

    return render_template("designer/insights/detail.html",
                           client=client,
                           insight=insight,
                           stage1=stage1,
                           designer_result=designer_result,
                           posts=json.loads(insight.posts_json or "[]"),
                           this_week=json.loads(insight.this_week_json or "{}"),
                           last_week=json.loads(insight.last_week_json or "{}"),
                           four_week_avg=json.loads(insight.four_week_avg_json or "{}"),
                           account=json.loads(insight.account_json or "{}"))


# ── Stage 1: データ分析（AJAX） ───────────────────────────────────────────────

@designer_bp.route("/clients/<int:client_id>/insights/<int:insight_id>/analyze", methods=["POST"])
@login_required
def insight_analyze(client_id: int, insight_id: int):
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    insight = WeeklyInsight.query.get_or_404(insight_id)
    if insight.client_id != client_id:
        abort(403)

    force = request.json.get("force", False) if request.is_json else False

    # キャッシュ有効時はスキップ
    if insight.stage1_json and not force:
        return jsonify({"success": True, "cached": True, "result": json.loads(insight.stage1_json)})

    try:
        from agents.insight_analyzer import InsightAnalyzerAgent
        from datetime import datetime as _dt
        payload = {
            "account":       json.loads(insight.account_json or "{}"),
            "this_week":     json.loads(insight.this_week_json or "{}"),
            "last_week":     json.loads(insight.last_week_json or "{}"),
            "four_week_avg": json.loads(insight.four_week_avg_json or "{}"),
            "posts":         json.loads(insight.posts_json or "[]"),
        }
        result = InsightAnalyzerAgent().run(payload)
        insight.stage1_json = json.dumps(result, ensure_ascii=False)
        insight.stage1_generated_at = _dt.utcnow()
        db.session.commit()
        return jsonify({"success": True, "cached": False, "result": result})
    except Exception as e:
        return jsonify({"success": False, "reason": str(e)}), 500


# ── Stage 2-A: デザイナー向けレポート（AJAX） ────────────────────────────────

@designer_bp.route("/clients/<int:client_id>/insights/<int:insight_id>/report/designer", methods=["POST"])
@login_required
def insight_report_designer(client_id: int, insight_id: int):
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    insight = WeeklyInsight.query.get_or_404(insight_id)
    if insight.client_id != client_id:
        abort(403)
    if not insight.stage1_json:
        return jsonify({"success": False, "reason": "先に分析（Stage 1）を実行してください"}), 400

    try:
        from agents.insight_reporter_designer import InsightReporterDesignerAgent
        stage1 = json.loads(insight.stage1_json)
        result = InsightReporterDesignerAgent().run(stage1, industry=client.business_description or "")
        report = WeeklyReport(
            weekly_insight_id=insight_id,
            report_type="designer",
            result_json=json.dumps(result, ensure_ascii=False),
        )
        db.session.add(report)
        db.session.commit()
        return jsonify({"success": True, "result": result})
    except Exception as e:
        return jsonify({"success": False, "reason": str(e)}), 500


# ── Stage 2-B: クライアント向け月次サマリー ──────────────────────────────────

@designer_bp.route("/clients/<int:client_id>/insights/monthly-report", methods=["GET", "POST"])
@login_required
def insight_monthly_report(client_id: int):
    client = Client.query.get_or_404(client_id)
    _assert_access(client)

    # 最新4週分（Stage 1 分析済みのもの）
    recent4 = (
        WeeklyInsight.query
        .filter(WeeklyInsight.client_id == client_id,
                WeeklyInsight.stage1_json.isnot(None))
        .order_by(WeeklyInsight.week_start.desc())
        .limit(4).all()
    )

    report_text = None
    if request.method == "POST":
        if len(recent4) == 0:
            flash("分析済みのインサイトデータがありません。まずデータを入力して分析を実行してください。", "error")
            return redirect(url_for("designer.insight_list", client_id=client_id))
        try:
            from agents.insight_reporter_client import InsightReporterClientAgent
            stage1_list = [json.loads(w.stage1_json) for w in recent4]
            designer_name = current_user.name or ""
            report_text = InsightReporterClientAgent().run(
                stage1_list,
                industry=client.business_description or "",
                designer_name=designer_name,
            )
            # 最新 insight に紐付けて保存
            if recent4:
                rep = WeeklyReport(
                    weekly_insight_id=recent4[0].id,
                    report_type="client_monthly",
                    result_text=report_text,
                )
                db.session.add(rep)
                db.session.commit()
        except Exception as e:
            flash(f"レポート生成エラー: {e}", "error")

    # 保存済み最新月次レポートを取得
    if not report_text and recent4:
        saved = WeeklyReport.query.filter_by(
            weekly_insight_id=recent4[0].id, report_type="client_monthly"
        ).order_by(WeeklyReport.generated_at.desc()).first()
        if saved:
            report_text = saved.result_text

    return render_template("designer/insights/monthly_report.html",
                           client=client,
                           recent4=recent4,
                           report_text=report_text)


# ── レポート一覧 ─────────────────────────────────────────────────────────────

@designer_bp.route("/clients/<int:client_id>/reports")
@login_required
def report_list(client_id: int):
    client = Client.query.get_or_404(client_id)
    _assert_access(client)

    # 全WeeklyReportをinsight経由で取得（降順）
    reports = (
        WeeklyReport.query
        .join(WeeklyInsight, WeeklyReport.weekly_insight_id == WeeklyInsight.id)
        .filter(WeeklyInsight.client_id == client_id)
        .order_by(WeeklyReport.generated_at.desc())
        .all()
    )
    return render_template("designer/insights/report_list.html",
                           client=client, reports=reports)


# ── レポート個別閲覧 ──────────────────────────────────────────────────────────

@designer_bp.route("/clients/<int:client_id>/reports/<int:report_id>")
@login_required
def report_view(client_id: int, report_id: int):
    client = Client.query.get_or_404(client_id)
    _assert_access(client)
    report = WeeklyReport.query.get_or_404(report_id)
    if report.insight.client_id != client_id:
        abort(403)

    result_json = json.loads(report.result_json) if report.result_json else None
    return render_template("designer/insights/report_view.html",
                           client=client,
                           report=report,
                           result_json=result_json)


# ── ヘルパー ─────────────────────────────────────────────────────────────────

def _int(val):
    try:
        return int(val)
    except (TypeError, ValueError):
        return None
