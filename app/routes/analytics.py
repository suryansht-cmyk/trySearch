"""Analytics projects, scans and reports."""

from flask import Blueprint
from datetime import date, datetime, timedelta
from flask import Flask, jsonify, request, send_from_directory, abort, session, redirect
from sqlalchemy import (
    create_engine,
    MetaData,
    Table,
    Column,
    Boolean,
    Float,
    Integer,
    String,
    Text,
    DateTime,
    UniqueConstraint,
    select,
    insert,
    update,
    desc,
    func,
    text,
)

from app.auth import analytics_user_id
from app.db import engine
from app.metrics import analytics_report, make_analytics_report
from app.models import analytics_answer_sources, analytics_audit_findings, analytics_audit_jobs, analytics_audit_pages, analytics_competitors, analytics_content_opportunities, analytics_engine_metrics, analytics_projects, analytics_prompt_scan_runs, analytics_prompts, analytics_provider_answers, analytics_rag_chunks, analytics_rag_documents, analytics_rag_insights, analytics_runs, analytics_scan_schedules, analytics_site_audits, analytics_sitemaps, analytics_topics, analytics_tracked_prompts, gsc_connections, gsc_properties, gsc_query_rows, gsc_sync_runs
from app.ownership import is_legacy_mock_analytics_run, project_for_user
from app.utils import normalise_domain, normalise_website_url, row_to_dict, to_iso

analytics_bp = Blueprint('analytics', __name__)

@analytics_bp.route('/api/analytics/projects', methods=['GET', 'POST'])
def analytics_projects_endpoint():
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error

    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        raw_website = data.get('domain')
        domain = normalise_domain(raw_website)
        website_url = normalise_website_url(raw_website)
        brand_name = (data.get('brand_name') or '').strip()
        industry = (data.get('industry') or 'General').strip()[:150]
        if not domain or not brand_name:
            return jsonify({'error': 'Enter a valid website domain and brand name.'}), 400
        now = datetime.utcnow()
        with engine.begin() as conn:
            result = conn.execute(insert(analytics_projects).values(
                user_id=user_id, domain=domain, website_url=website_url, brand_name=brand_name[:150], industry=industry or 'General',
                created_at=now, updated_at=now,
            ))
            project_id = result.inserted_primary_key[0]
        project = project_for_user(project_id, user_id)
        return jsonify({'status': 'success', 'project': row_to_dict(project)}), 201

    with engine.connect() as conn:
        project_rows = conn.execute(
            select(analytics_projects).where(analytics_projects.c.user_id == user_id)
            .order_by(desc(analytics_projects.c.updated_at))
        ).mappings().all()
        projects = []
        for row in project_rows:
            project = dict(row)
            latest_site = conn.execute(
                select(
                    analytics_site_audits.c.id,
                    analytics_site_audits.c.readiness_score,
                    analytics_site_audits.c.status,
                    analytics_site_audits.c.created_at,
                ).where(analytics_site_audits.c.project_id == project['id'])
                .order_by(desc(analytics_site_audits.c.created_at)).limit(1)
            ).mappings().first()
            latest_legacy = conn.execute(
                select(analytics_runs.c.id, analytics_runs.c.visibility_score, analytics_runs.c.created_at, analytics_runs.c.summary)
                .where(analytics_runs.c.project_id == project['id'])
                .order_by(desc(analytics_runs.c.created_at)).limit(1)
            ).mappings().first()
            if latest_site:
                project['latest_run'] = {
                    'id': latest_site['id'], 'visibility_score': latest_site['readiness_score'],
                    'status': latest_site['status'], 'created_at': to_iso(latest_site['created_at']),
                    'source_type': 'website_crawl',
                }
            else:
                project['latest_run'] = row_to_dict(latest_legacy) if latest_legacy and not is_legacy_mock_analytics_run(latest_legacy) else None
            projects.append(row_to_dict(project))
    return jsonify({'projects': projects})

@analytics_bp.route('/api/analytics/projects/<int:project_id>', methods=['DELETE'])
def delete_analytics_project(project_id):
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    project = project_for_user(project_id, user_id)
    if not project:
        return jsonify({'error': 'Project not found.'}), 404
    with engine.begin() as conn:
        audit_ids = [row[0] for row in conn.execute(select(analytics_site_audits.c.id).where(
            analytics_site_audits.c.project_id == project_id
        )).all()]
        if audit_ids:
            conn.execute(analytics_rag_insights.delete().where(analytics_rag_insights.c.audit_id.in_(audit_ids)))
            conn.execute(analytics_rag_chunks.delete().where(analytics_rag_chunks.c.audit_id.in_(audit_ids)))
            conn.execute(analytics_rag_documents.delete().where(analytics_rag_documents.c.audit_id.in_(audit_ids)))
            page_ids = [row[0] for row in conn.execute(select(analytics_audit_pages.c.id).where(
                analytics_audit_pages.c.audit_id.in_(audit_ids)
            )).all()]
            if page_ids:
                conn.execute(analytics_audit_findings.delete().where(analytics_audit_findings.c.page_id.in_(page_ids)))
            conn.execute(analytics_audit_findings.delete().where(analytics_audit_findings.c.audit_id.in_(audit_ids)))
            conn.execute(analytics_audit_pages.delete().where(analytics_audit_pages.c.audit_id.in_(audit_ids)))
            conn.execute(analytics_sitemaps.delete().where(analytics_sitemaps.c.audit_id.in_(audit_ids)))
            conn.execute(analytics_site_audits.delete().where(analytics_site_audits.c.id.in_(audit_ids)))

        prompt_scan_ids = [row[0] for row in conn.execute(select(analytics_prompt_scan_runs.c.id).where(
            analytics_prompt_scan_runs.c.project_id == project_id
        )).all()]
        if prompt_scan_ids:
            answer_ids = [row[0] for row in conn.execute(select(analytics_provider_answers.c.id).where(
                analytics_provider_answers.c.scan_run_id.in_(prompt_scan_ids)
            )).all()]
            if answer_ids:
                conn.execute(analytics_answer_sources.delete().where(analytics_answer_sources.c.answer_id.in_(answer_ids)))
            conn.execute(analytics_provider_answers.delete().where(analytics_provider_answers.c.scan_run_id.in_(prompt_scan_ids)))
            conn.execute(analytics_content_opportunities.delete().where(analytics_content_opportunities.c.scan_run_id.in_(prompt_scan_ids)))
            conn.execute(analytics_prompt_scan_runs.delete().where(analytics_prompt_scan_runs.c.id.in_(prompt_scan_ids)))

        connection = conn.execute(select(gsc_connections.c.id).where(gsc_connections.c.project_id == project_id)).scalar_one_or_none()
        if connection:
            sync_ids = [row[0] for row in conn.execute(select(gsc_sync_runs.c.id).where(
                gsc_sync_runs.c.connection_id == connection
            )).all()]
            if sync_ids:
                conn.execute(gsc_query_rows.delete().where(gsc_query_rows.c.sync_run_id.in_(sync_ids)))
            conn.execute(gsc_sync_runs.delete().where(gsc_sync_runs.c.connection_id == connection))
            conn.execute(gsc_properties.delete().where(gsc_properties.c.connection_id == connection))
            conn.execute(gsc_connections.delete().where(gsc_connections.c.id == connection))

        conn.execute(analytics_topics.delete().where(analytics_topics.c.project_id == project_id))
        conn.execute(analytics_competitors.delete().where(analytics_competitors.c.project_id == project_id))
        conn.execute(analytics_tracked_prompts.delete().where(analytics_tracked_prompts.c.project_id == project_id))
        conn.execute(analytics_scan_schedules.delete().where(analytics_scan_schedules.c.project_id == project_id))
        conn.execute(analytics_content_opportunities.delete().where(analytics_content_opportunities.c.project_id == project_id))
        conn.execute(analytics_audit_jobs.delete().where(analytics_audit_jobs.c.project_id == project_id))
        run_ids = [row[0] for row in conn.execute(select(analytics_runs.c.id).where(analytics_runs.c.project_id == project_id)).all()]
        if run_ids:
            conn.execute(analytics_engine_metrics.delete().where(analytics_engine_metrics.c.run_id.in_(run_ids)))
            conn.execute(analytics_prompts.delete().where(analytics_prompts.c.run_id.in_(run_ids)))
        conn.execute(analytics_runs.delete().where(analytics_runs.c.project_id == project_id))
        conn.execute(analytics_projects.delete().where(analytics_projects.c.id == project_id))
    return jsonify({'status': 'success'})

@analytics_bp.route('/api/analytics/projects/<int:project_id>/scan', methods=['POST'])
def scan_analytics_project(project_id):
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    project = project_for_user(project_id, user_id)
    if not project:
        return jsonify({'error': 'Project not found.'}), 404
    with engine.connect() as conn:
        run_number = len(conn.execute(
            select(analytics_runs.c.id).where(analytics_runs.c.project_id == project_id)
        ).all()) + 1
    report = make_analytics_report(project, max(run_number, 1))
    now = datetime.utcnow()
    with engine.begin() as conn:
        result = conn.execute(insert(analytics_runs).values(
            project_id=project_id, created_at=now,
            visibility_score=report['visibility_score'], mention_rate=report['mention_rate'],
            citation_rate=report['citation_rate'], share_of_voice=report['share_of_voice'], summary=report['summary'],
        ))
        run_id = result.inserted_primary_key[0]
        conn.execute(insert(analytics_engine_metrics), [dict(metric, run_id=run_id) for metric in report['engines']])
        conn.execute(insert(analytics_prompts), [dict(prompt, run_id=run_id) for prompt in report['prompts']])
        conn.execute(analytics_projects.update().where(analytics_projects.c.id == project_id).values(updated_at=now))
    return jsonify({'status': 'success', 'report': analytics_report(project_id, user_id)})

@analytics_bp.route('/api/analytics/projects/<int:project_id>/report', methods=['GET'])
def analytics_report_endpoint(project_id):
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    report = analytics_report(project_id, user_id)
    if not report:
        return jsonify({'error': 'Project not found.'}), 404
    return jsonify(report)
