"""Master workspace summary.

T5 drops the master_workspaces table; this module goes with it."""

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
from app.metrics import make_analytics_report
from app.models import analytics_engine_metrics, analytics_projects, analytics_prompts, analytics_runs, content_documents, master_workspaces, prompt_collections, prompt_queries, prompt_query_results, visibility_engine_results, visibility_mentions, visibility_scans, visibility_watchlists
from app.ownership import master_workspace_for_user
from app.routes.content import make_content_draft
from app.routes.prompt_intelligence import make_prompt_result
from app.routes.visibility import make_visibility_report
from app.utils import normalise_domain, row_to_dict

master_workspace_bp = Blueprint('master_workspace', __name__)

def master_workspace_response(workspace, status='ready'):
    return {
        'status': status,
        'workspace': row_to_dict(workspace),
        'tools': [
            {'name': 'AI Search Analytics', 'href': '/analytics'},
            {'name': 'AI Visibility Tracking', 'href': '/visibility-tracking'},
            {'name': 'Prompt Intelligence', 'href': '/prompt-intelligence'},
            {'name': 'Content Studio', 'href': '/content-studio'},
        ],
    }

def master_workspace_summary(user_id):
    """Return the latest, linked state for every product in a master workspace."""
    workspace = master_workspace_for_user(user_id)
    if not workspace:
        return None

    with engine.connect() as conn:
        analytics_run = conn.execute(select(analytics_runs).where(
            analytics_runs.c.project_id == workspace['analytics_project_id']
        ).order_by(desc(analytics_runs.c.created_at)).limit(1)).mappings().first()
        visibility_scan = conn.execute(select(visibility_scans).where(
            visibility_scans.c.watchlist_id == workspace['visibility_watchlist_id']
        ).order_by(desc(visibility_scans.c.created_at)).limit(1)).mappings().first()
        queries = conn.execute(select(prompt_queries).where(
            prompt_queries.c.collection_id == workspace['prompt_collection_id']
        ).order_by(desc(prompt_queries.c.created_at))).mappings().all()
        query_ids = [query['id'] for query in queries]
        prompt_results = []
        if query_ids:
            prompt_results = conn.execute(select(prompt_query_results).where(
                prompt_query_results.c.query_id.in_(query_ids)
            ).order_by(desc(prompt_query_results.c.created_at))).mappings().all()
        document = conn.execute(select(content_documents).where(
            content_documents.c.id == workspace['content_document_id']
        )).mappings().first()

    analysed_prompts = len(prompt_results)
    prompt_visibility = round(sum(result['visibility_score'] for result in prompt_results) / analysed_prompts) if analysed_prompts else None
    prompt_citations = round(100 * sum(result['cited'] == 'Yes' for result in prompt_results) / analysed_prompts) if analysed_prompts else None
    latest_prompt_result = prompt_results[0] if prompt_results else None

    return {
        'workspace': row_to_dict(workspace),
        'analytics': {
            'visibility_score': analytics_run['visibility_score'] if analytics_run else None,
            'citation_rate': analytics_run['citation_rate'] if analytics_run else None,
            'mention_rate': analytics_run['mention_rate'] if analytics_run else None,
            'summary': analytics_run['summary'] if analytics_run else 'Run an analytics scan to establish your baseline.',
        },
        'visibility': {
            'visibility_score': visibility_scan['visibility_score'] if visibility_scan else None,
            'mentions_found': visibility_scan['mentions_found'] if visibility_scan else None,
            'citations_found': visibility_scan['citations_found'] if visibility_scan else None,
            'summary': visibility_scan['summary'] if visibility_scan else 'Run a visibility scan to find answer appearances.',
        },
        'prompts': {
            'tracked_prompts': len(queries),
            'analysed_prompts': analysed_prompts,
            'average_visibility': prompt_visibility,
            'citation_rate': prompt_citations,
            'recommendation': latest_prompt_result['recommendation'] if latest_prompt_result else 'Add a prompt to begin building your answer landscape.',
        },
        'content': {
            'title': document['title'] if document else 'No content brief yet',
            'status': document['status'] if document else 'Not started',
            'seo_title': document['seo_title'] if document else '',
            'keyword': document['keyword'] if document else workspace['topic'],
        },
    }

@master_workspace_bp.route('/api/master-workspace/summary')
def master_workspace_summary_endpoint():
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    summary = master_workspace_summary(user_id)
    return jsonify({'workspace': None} if not summary else summary)

@master_workspace_bp.route('/api/master-workspace', methods=['GET', 'POST'])
def master_workspace_endpoint():
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    existing = master_workspace_for_user(user_id)
    if request.method == 'GET':
        return jsonify(master_workspace_response(existing, 'existing') if existing else {'workspace': None})
    if existing:
        return jsonify(master_workspace_response(existing, 'existing'))

    data = request.get_json(silent=True) or {}
    brand_name = (data.get('brand_name') or '').strip()
    domain = normalise_domain(data.get('domain'))
    industry = (data.get('industry') or '').strip()
    topic = (data.get('topic') or '').strip()
    goal = (data.get('goal') or '').strip()
    if not brand_name or not domain or not industry or not topic or not goal:
        return jsonify({'error': 'Enter your brand, a valid website, industry, topic, and primary goal.'}), 400

    now = datetime.utcnow()
    with engine.begin() as conn:
        # AI Search Analytics: project and its initial benchmark report.
        project_result = conn.execute(insert(analytics_projects).values(
            user_id=user_id, domain=domain, brand_name=brand_name[:150], industry=industry[:150],
            created_at=now, updated_at=now,
        ))
        project_id = project_result.inserted_primary_key[0]
        project = {'id': project_id, 'domain': domain, 'brand_name': brand_name[:150], 'industry': industry[:150]}
        analytics_report_data = make_analytics_report(project, 1)
        analytics_run_result = conn.execute(insert(analytics_runs).values(
            project_id=project_id, created_at=now, visibility_score=analytics_report_data['visibility_score'],
            mention_rate=analytics_report_data['mention_rate'], citation_rate=analytics_report_data['citation_rate'],
            share_of_voice=analytics_report_data['share_of_voice'], summary=analytics_report_data['summary'],
        ))
        analytics_run_id = analytics_run_result.inserted_primary_key[0]
        conn.execute(insert(analytics_engine_metrics), [dict(metric, run_id=analytics_run_id) for metric in analytics_report_data['engines']])
        conn.execute(insert(analytics_prompts), [dict(prompt, run_id=analytics_run_id) for prompt in analytics_report_data['prompts']])

        # Visibility Tracking: dedicated watchlist and its first scan.
        watchlist_result = conn.execute(insert(visibility_watchlists).values(
            user_id=user_id, name=f'{brand_name} visibility', brand_name=brand_name[:150], website=domain,
            topic=topic[:150], created_at=now, updated_at=now,
        ))
        watchlist_id = watchlist_result.inserted_primary_key[0]
        watchlist = {'id': watchlist_id, 'brand_name': brand_name[:150], 'topic': topic[:150]}
        visibility_report_data = make_visibility_report(watchlist, 1)
        scan_result = conn.execute(insert(visibility_scans).values(
            watchlist_id=watchlist_id, created_at=now, visibility_score=visibility_report_data['visibility_score'],
            mentions_found=visibility_report_data['mentions_found'], citations_found=visibility_report_data['citations_found'],
            competitor_mentions=visibility_report_data['competitor_mentions'], summary=visibility_report_data['summary'],
        ))
        scan_id = scan_result.inserted_primary_key[0]
        conn.execute(insert(visibility_engine_results), [dict(item, scan_id=scan_id) for item in visibility_report_data['engines']])
        conn.execute(insert(visibility_mentions), [dict(item, scan_id=scan_id) for item in visibility_report_data['mentions']])

        # Prompt Intelligence: collection plus three analysed starting prompts.
        collection_result = conn.execute(insert(prompt_collections).values(
            user_id=user_id, name=f'{brand_name} prompt opportunities', brand_name=brand_name[:150], website=domain,
            created_at=now, updated_at=now,
        ))
        collection_id = collection_result.inserted_primary_key[0]
        starter_prompts = [
            (f'What are the best {topic} solutions?', 'ChatGPT', 'Commercial'),
            (f'How does {brand_name} compare for {topic}?', 'Perplexity', 'Comparison'),
            (f'How do teams achieve {goal.lower()} with {topic}?', 'Claude', 'Informational'),
        ]
        for prompt_text, prompt_engine, prompt_intent in starter_prompts:
            query_result = conn.execute(insert(prompt_queries).values(
                collection_id=collection_id, prompt=prompt_text, engine=prompt_engine, intent=prompt_intent,
                created_at=now, updated_at=now,
            ))
            query_id = query_result.inserted_primary_key[0]
            query = {'id': query_id, 'prompt': prompt_text, 'engine': prompt_engine, 'intent': prompt_intent}
            prompt_result_data = make_prompt_result(query, brand_name[:150], 1)
            conn.execute(insert(prompt_query_results).values(
                query_id=query_id, created_at=now, **prompt_result_data
            ))

        # Content Studio: a generated, editable initial draft connected to the same topic.
        content_title = f'How to achieve {goal} with {topic}'[:200]
        content_result = conn.execute(insert(content_documents).values(
            user_id=user_id, title=content_title, brand_name=brand_name[:150], keyword=topic[:200],
            content_type='Blog post', tone='Expert', content='', seo_title='', meta_description='', outline='',
            recommendations='', status='Brief', version=0, created_at=now, updated_at=now,
        ))
        content_document_id = content_result.inserted_primary_key[0]
        document = {'id': content_document_id, 'title': content_title, 'brand_name': brand_name[:150],
                    'keyword': topic[:200], 'content_type': 'Blog post', 'tone': 'Expert'}
        content_draft = make_content_draft(document)
        conn.execute(content_documents.update().where(content_documents.c.id == content_document_id).values(
            **content_draft, status='Draft', version=1, updated_at=now
        ))

        workspace_result = conn.execute(insert(master_workspaces).values(
            user_id=user_id, brand_name=brand_name[:150], domain=domain, industry=industry[:150], topic=topic[:200],
            goal=goal[:200], analytics_project_id=project_id, visibility_watchlist_id=watchlist_id,
            prompt_collection_id=collection_id, content_document_id=content_document_id,
            created_at=now, updated_at=now,
        ))
        workspace_id = workspace_result.inserted_primary_key[0]

    workspace = master_workspace_for_user(user_id)
    return jsonify(master_workspace_response(workspace or {'id': workspace_id}, 'ready')), 201
