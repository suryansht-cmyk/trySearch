"""Report assembly over stored evidence."""

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
import hashlib
import json

from app.crawler.fetch import normalise_site_host
from app.crawler.scoring import fetch_website_snapshot, score_website_snapshot
from app.db import engine, metadata
from app.extraction.mentions import domain_matches, project_brand_aliases, text_mentions_alias
from app.jobs import latest_site_audit
from app.models import analytics_answer_sources, analytics_content_opportunities, workspaces, analytics_prompt_scan_runs, analytics_provider_answers, analytics_topics, analytics_tracked_prompts
from app.tenancy import workspace_for_member
from app.utils import row_to_dict


def analytics_report(workspace_id, user_id):
    project = workspace_for_member(workspace_id, user_id)
    if not project:
        return None
    site_audit = latest_site_audit(workspace_id)
    if site_audit:
        audit_run = site_audit['run']
        compatibility_run = {
            'id': audit_run['id'], 'workspace_id': workspace_id,
            'visibility_score': audit_run['readiness_score'],
            'mention_rate': audit_run['metadata_score'],
            'citation_rate': audit_run['content_score'],
            'share_of_voice': audit_run['crawlability_score'],
            'summary': audit_run['summary'], 'status': audit_run['status'],
            'created_at': audit_run['created_at'], 'source_type': 'website_crawl',
        }
        compatibility_engines = [
            {'engine': 'Metadata', 'visibility_score': audit_run['metadata_score'], 'mention_rate': audit_run['metadata_score'], 'citations': 0, 'change': 0},
            {'engine': 'Content', 'visibility_score': audit_run['content_score'], 'mention_rate': audit_run['content_score'], 'citations': 0, 'change': 0},
            {'engine': 'Crawlability', 'visibility_score': audit_run['crawlability_score'], 'mention_rate': audit_run['crawlability_score'], 'citations': 0, 'change': 0},
            {'engine': 'Structured data', 'visibility_score': audit_run['structured_data_score'], 'mention_rate': audit_run['structured_data_score'], 'citations': 0, 'change': 0},
        ]
        compatibility_prompts = [
            {
                'prompt': finding['code'].replace('_', ' ').title(), 'intent': finding['area'],
                'position': 0, 'cited': finding['severity'].title(),
                'leading_brand': finding['evidence'], 'opportunity': finding['recommendation'],
            }
            for finding in site_audit['findings'][:20]
        ]
        compatibility_history = [
            {'visibility_score': item['readiness_score'], 'created_at': item['created_at'], 'status': item['status']}
            for item in site_audit['history']
        ]
        return {
            'project': row_to_dict(project), 'run': compatibility_run,
            'engines': compatibility_engines, 'prompts': compatibility_prompts,
            'history': compatibility_history, 'audit': site_audit,
        }
    # No site audit yet. analytics_runs held the legacy fallback report and was
    # dropped in T5, so there is no second source - this is the honest
    # 'not yet run' state rather than a fabricated zero.
    return {'project': row_to_dict(project), 'run': None, 'engines': [], 'prompts': [],
            'history': [], 'audit': None}

def provider_evidence_rows(scan_id):
    with engine.connect() as conn:
        rows = conn.execute(select(
            analytics_provider_answers,
            func.coalesce(analytics_provider_answers.c.prompt_text, analytics_tracked_prompts.c.prompt).label('resolved_prompt'),
            func.coalesce(analytics_provider_answers.c.prompt_intent, analytics_tracked_prompts.c.intent).label('resolved_intent'),
            func.coalesce(analytics_provider_answers.c.topic_name, analytics_topics.c.name).label('resolved_topic_name'),
        ).outerjoin(
            analytics_tracked_prompts, analytics_provider_answers.c.prompt_id == analytics_tracked_prompts.c.id
        ).outerjoin(
            analytics_topics, analytics_tracked_prompts.c.topic_id == analytics_topics.c.id
        ).where(analytics_provider_answers.c.scan_run_id == scan_id)
            .order_by(analytics_provider_answers.c.id)).mappings().all()
    evidence = []
    for row in rows:
        item = row_to_dict(row)
        item['prompt'] = item.pop('resolved_prompt')
        item['intent'] = item.pop('resolved_intent')
        item['topic_name'] = item.pop('resolved_topic_name')
        evidence.append(item)
    return evidence

def latest_prompt_evidence(workspace_id, run_id=None):
    with engine.connect() as conn:
        project = conn.execute(select(workspaces).where(
            workspaces.c.id == workspace_id
        )).mappings().first()
        statement = select(analytics_prompt_scan_runs).where(
            analytics_prompt_scan_runs.c.workspace_id == workspace_id
        )
        if run_id:
            statement = statement.where(analytics_prompt_scan_runs.c.id == run_id)
        scan = conn.execute(statement.order_by(desc(analytics_prompt_scan_runs.c.created_at)).limit(1)).mappings().first()
        if not scan:
            return {'run': None, 'answers': [], 'opportunities': [], 'history': []}
        scan = dict(scan)
        try:
            scan['competitor_set'] = json.loads(scan.get('competitor_snapshot') or '[]')
        except json.JSONDecodeError:
            scan['competitor_set'] = []
        scan.pop('competitor_snapshot', None)
        answer_rows = provider_evidence_rows(scan['id'])
        answer_ids = [row['id'] for row in answer_rows]
        sources_by_answer = {answer_id: [] for answer_id in answer_ids}
        if answer_ids:
            source_rows = conn.execute(select(analytics_answer_sources).where(
                analytics_answer_sources.c.answer_id.in_(answer_ids)
            ).order_by(analytics_answer_sources.c.answer_id, analytics_answer_sources.c.rank)).mappings().all()
            for source in source_rows:
                sources_by_answer[source['answer_id']].append(row_to_dict(source))
        opportunities = [row_to_dict(row) for row in conn.execute(select(analytics_content_opportunities).where(
            analytics_content_opportunities.c.scan_run_id == scan['id']
        ).order_by(analytics_content_opportunities.c.priority, analytics_content_opportunities.c.id)).mappings().all()]
        history = [row_to_dict(row) for row in conn.execute(select(
            analytics_prompt_scan_runs.c.id, analytics_prompt_scan_runs.c.status,
            analytics_prompt_scan_runs.c.provider, analytics_prompt_scan_runs.c.model,
            analytics_prompt_scan_runs.c.region, analytics_prompt_scan_runs.c.competitor_snapshot,
            analytics_prompt_scan_runs.c.prompt_count, analytics_prompt_scan_runs.c.completed_count,
            analytics_prompt_scan_runs.c.mention_rate, analytics_prompt_scan_runs.c.citation_rate,
            analytics_prompt_scan_runs.c.source_presence_rate, analytics_prompt_scan_runs.c.share_of_voice,
            analytics_prompt_scan_runs.c.created_at, analytics_prompt_scan_runs.c.completed_at,
        ).where(analytics_prompt_scan_runs.c.workspace_id == workspace_id)
            .order_by(desc(analytics_prompt_scan_runs.c.created_at)).limit(12)).mappings().all()]
        history_ids = [item['id'] for item in history]
        historical_answers = []
        if history_ids:
            historical_answers = [row_to_dict(row) for row in conn.execute(select(
                analytics_provider_answers.c.scan_run_id,
                analytics_provider_answers.c.prompt_id,
                analytics_provider_answers.c.prompt_text,
                analytics_provider_answers.c.answer_text,
                analytics_provider_answers.c.best_source_rank,
            ).where(
                analytics_provider_answers.c.scan_run_id.in_(history_ids)
            )).mappings().all()]
    for answer in answer_rows:
        answer['sources'] = sources_by_answer.get(answer['id'], [])
        answer.pop('raw_response', None)

    # Enrich every run with metrics that can be reproduced from its saved
    # provider-answer cohort. These values drive the overview charts; they are
    # never inferred by the RAG recommendation layer.
    historical_answers_by_run = {history_id: [] for history_id in history_ids}
    for answer in historical_answers:
        historical_answers_by_run.setdefault(answer['scan_run_id'], []).append(answer)
    for item in history:
        run_answers = historical_answers_by_run.get(item['id'], [])
        rank_values = [
            int(answer['best_source_rank']) for answer in run_answers
            if answer.get('best_source_rank') is not None and int(answer['best_source_rank']) > 0
        ]
        # Preserve multiplicity: two identical tracked prompts are two measured
        # observations and therefore are not the same cohort as one prompt.
        prompt_snapshot = sorted([
            (answer.get('prompt_text') or f"prompt:{answer.get('prompt_id')}").strip()
            for answer in run_answers
        ])
        try:
            competitor_snapshot = json.loads(item.get('competitor_snapshot') or '[]')
        except json.JSONDecodeError:
            competitor_snapshot = []
        cohort_payload = {
            'prompts': prompt_snapshot,
            'competitors': sorted(
                [
                    {
                        'name': (competitor.get('name') or '').strip(),
                        'domain': normalise_site_host(competitor.get('domain') or ''),
                    }
                    for competitor in competitor_snapshot if isinstance(competitor, dict)
                ],
                key=lambda competitor: (competitor['name'].casefold(), competitor['domain']),
            ),
            'region': item.get('region') or None,
        }
        item['cohort_id'] = hashlib.sha256(
            json.dumps(cohort_payload, ensure_ascii=False, sort_keys=True).encode('utf-8')
        ).hexdigest()[:12]
        item['answer_measured_count'] = sum(bool(answer.get('answer_text')) for answer in run_answers)
        item['ranked_appearance_count'] = len(rank_values)
        item['average_source_position'] = round(sum(rank_values) / len(rank_values), 2) if rank_values else None
        item.pop('competitor_snapshot', None)

    latest_history = next((item for item in history if item['id'] == scan['id']), None)
    if latest_history:
        for field in ('cohort_id', 'answer_measured_count', 'ranked_appearance_count', 'average_source_position'):
            scan[field] = latest_history[field]

    # Rank the tracked brand and the scan-time competitor snapshot from the
    # latest saved answers. A mention is counted at most once per answer, and
    # average source rank uses only stored Perplexity Search result positions.
    brand_rankings = []
    if project:
        project = dict(project)
        brand_definitions = [{
            'name': project['brand_name'],
            'domain': project['domain'],
            'aliases': project_brand_aliases(project),
            'tracked': True,
        }]
        for competitor in scan.get('competitor_set') or []:
            if not isinstance(competitor, dict) or not (competitor.get('name') or competitor.get('domain')):
                continue
            brand_definitions.append({
                'name': competitor.get('name') or competitor.get('domain'),
                'domain': competitor.get('domain'),
                'aliases': [competitor.get('name'), competitor.get('domain')],
                'tracked': False,
            })
        measured_answers = [answer for answer in answer_rows if answer.get('answer_text')]
        for brand in brand_definitions:
            mention_count = sum(
                text_mentions_alias(answer.get('answer_text'), brand['aliases'])
                for answer in measured_answers
            )
            source_positions = []
            if brand.get('domain'):
                for answer in answer_rows:
                    ranks = [
                        int(source['rank']) for source in sources_by_answer.get(answer['id'], [])
                        if source.get('source_kind') == 'search_result' and
                        source.get('url') and domain_matches(source['url'], brand['domain'])
                    ]
                    if ranks:
                        source_positions.append(min(ranks))
                    elif brand['tracked'] and answer.get('best_source_rank'):
                        # Backward-compatible fallback for evidence saved before
                        # normalized search-result rows were persisted.
                        source_positions.append(int(answer['best_source_rank']))
            brand_rankings.append({
                'name': brand['name'], 'domain': brand.get('domain'), 'tracked': brand['tracked'],
                'mention_count': mention_count, 'answer_count': len(measured_answers),
                'visibility': round(mention_count / len(measured_answers) * 100, 2) if measured_answers else None,
                'source_appearance_count': len(source_positions),
                'average_source_position': round(sum(source_positions) / len(source_positions), 2) if source_positions else None,
            })
        total_mentions = sum(item['mention_count'] for item in brand_rankings)
        for item in brand_rankings:
            item['share_of_voice'] = round(item['mention_count'] / total_mentions * 100, 2) if total_mentions else None
        brand_rankings.sort(key=lambda item: (
            item['visibility'] is None,
            -(item['visibility'] or 0),
            not item['tracked'],
            item['name'].casefold(),
        ))
        for rank, item in enumerate(brand_rankings, 1):
            item['rank'] = rank
    history.reverse()
    return {
        'run': row_to_dict(scan), 'answers': answer_rows, 'opportunities': opportunities,
        'history': history, 'brand_rankings': brand_rankings,
        'measurement': {
            'source': 'stored_provider_evidence',
            'provider': scan.get('provider'), 'model': scan.get('model'),
            'cohort_id': scan.get('cohort_id'), 'region': scan.get('region'),
            'prompt_count': scan.get('prompt_count'), 'completed_count': scan.get('completed_count'),
            'measured_at': scan.get('completed_at') or scan.get('created_at'),
        },
    }
