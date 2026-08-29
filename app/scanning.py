"""Prompt scan execution and provider answer persistence."""

from datetime import date, datetime, timedelta
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
import json
import os
import time

from app.config import PERPLEXITY_MAX_PROMPTS_PER_SCAN
from app.db import engine
from app.engines.perplexity import call_perplexity_answer, call_perplexity_search, normalise_perplexity_sources, perplexity_answer_citations, perplexity_answer_text
from app.extraction.mentions import domain_matches, project_brand_aliases, text_mentions_alias
from app.http_client import ProviderAPIError
from app.jobs import update_analytics_job
from app.llm import open_model_settings
from app.metrics import provider_evidence_rows
from app.models import analytics_answer_sources, analytics_audit_jobs, analytics_competitors, analytics_content_opportunities, analytics_projects, analytics_prompt_scan_runs, analytics_provider_answers, analytics_scan_schedules, analytics_topics, analytics_tracked_prompts
from app.recommendations import open_model_evidence_opportunities, rule_based_opportunities
from app.routes.pages import index

def next_schedule_time(frequency, from_time=None):
    from_time = from_time or datetime.utcnow()
    return from_time + {'daily': timedelta(days=1), 'weekly': timedelta(days=7), 'monthly': timedelta(days=30)}[frequency]

def persist_provider_answer(scan_id, prompt, project, search_payload, answer_payload, errors, latency_ms):
    answer_text = perplexity_answer_text(answer_payload or {})
    sources = normalise_perplexity_sources(search_payload or {}, answer_payload or {})
    aliases = project_brand_aliases(project)
    citations = perplexity_answer_citations(answer_payload or {})
    citation_urls = [item if isinstance(item, str) else item.get('url') for item in citations if isinstance(item, (str, dict))]
    search_results = (search_payload or {}).get('results') or []
    search_urls = [item.get('url') for item in search_results if isinstance(item, dict)]
    answer_available = bool(answer_payload is not None and answer_text)
    brand_mentioned = text_mentions_alias(answer_text, aliases) if answer_available else None
    brand_cited = any(domain_matches(url, project['domain']) for url in citation_urls if url) if answer_available else None
    source_ranks = [rank for rank, url in enumerate(search_urls, 1) if url and domain_matches(url, project['domain'])]
    source_present = bool(source_ranks) if search_payload is not None else None
    status = 'succeeded' if search_payload is not None and answer_available else ('partial' if search_payload is not None or answer_available else 'failed')
    raw_response = json.dumps({'search': search_payload, 'answer': answer_payload}, ensure_ascii=False)
    now = datetime.utcnow()
    answer_model = (answer_payload or {}).get('model') or f"preset:{os.environ.get('PERPLEXITY_AGENT_PRESET', 'low')}"
    with engine.begin() as conn:
        result = conn.execute(insert(analytics_provider_answers).values(
            scan_run_id=scan_id, prompt_id=prompt['id'],
            prompt_text=prompt['prompt'], prompt_intent=prompt.get('intent'), topic_name=prompt.get('topic_name'),
            provider='Perplexity',
            model=str(answer_model)[:160], status=status,
            search_request_id=(search_payload or {}).get('id'), answer_request_id=(answer_payload or {}).get('id'),
            answer_text=answer_text or None, raw_response=raw_response,
            brand_mentioned=brand_mentioned, brand_cited=brand_cited, source_present=source_present,
            best_source_rank=min(source_ranks) if source_ranks else None, latency_ms=latency_ms,
            error='; '.join(errors)[:2000] if errors else None,
            created_at=now, completed_at=now,
        ))
        answer_id = result.inserted_primary_key[0]
        if sources:
            conn.execute(insert(analytics_answer_sources), [dict(source, answer_id=answer_id) for source in sources])
    return answer_id

def run_prompt_scan_job(job_id):
    with engine.begin() as conn:
        job = conn.execute(select(analytics_audit_jobs).where(
            analytics_audit_jobs.c.id == job_id
        )).mappings().first()
        if not job or job['status'] not in {'queued', 'failed_retryable'}:
            return
        claimed = conn.execute(update(analytics_audit_jobs).where(
            (analytics_audit_jobs.c.id == job_id) &
            (analytics_audit_jobs.c.status.in_(['queued', 'failed_retryable']))
        ).values(status='running', started_at=datetime.utcnow(), completed_at=None, error=None))
        if claimed.rowcount != 1:
            return
        project = conn.execute(select(analytics_projects).where(
            analytics_projects.c.id == job['project_id']
        )).mappings().first()
        prompts = conn.execute(select(
            analytics_tracked_prompts,
            analytics_topics.c.name.label('topic_name'),
        ).outerjoin(
            analytics_topics, analytics_tracked_prompts.c.topic_id == analytics_topics.c.id
        ).where(
            (analytics_tracked_prompts.c.project_id == job['project_id']) &
            (analytics_tracked_prompts.c.active.is_(True))
        ).order_by(analytics_tracked_prompts.c.id)).mappings().all()
        competitors = conn.execute(select(analytics_competitors).where(
            analytics_competitors.c.project_id == job['project_id']
        )).mappings().all()
    if not project or not prompts:
        update_analytics_job(job_id, status='failed_terminal', progress=100, error='Add at least one active tracked prompt first.', completed_at=datetime.utcnow())
        return
    if len(prompts) > PERPLEXITY_MAX_PROMPTS_PER_SCAN:
        update_analytics_job(
            job_id, status='failed_terminal', progress=100,
            error=f'Pause prompts until no more than {PERPLEXITY_MAX_PROMPTS_PER_SCAN} are active for one provider scan.',
            completed_at=datetime.utcnow(),
        )
        return
    if not os.environ.get('PERPLEXITY_API_KEY'):
        update_analytics_job(job_id, status='failed_terminal', progress=100, error='PERPLEXITY_API_KEY is not configured.', completed_at=datetime.utcnow())
        return

    with engine.connect() as conn:
        completed_run = conn.execute(select(
            analytics_prompt_scan_runs.c.status,
            analytics_prompt_scan_runs.c.prompt_count,
            analytics_prompt_scan_runs.c.completed_count,
            analytics_prompt_scan_runs.c.completed_at,
        ).where(analytics_prompt_scan_runs.c.job_id == job_id)
            .order_by(desc(analytics_prompt_scan_runs.c.created_at)).limit(1)).mappings().first()
    if completed_run and completed_run['completed_at'] and completed_run['status'] in {'succeeded', 'partial', 'failed'}:
        job_status = 'succeeded' if completed_run['status'] in {'succeeded', 'partial'} else 'failed_terminal'
        update_analytics_job(
            job_id, status=job_status, progress=100,
            completed_items=completed_run['completed_count'], total_items=completed_run['prompt_count'],
            error=None if job_status == 'succeeded' else 'The saved provider scan did not complete successfully.',
            completed_at=datetime.utcnow(),
        )
        return

    project = dict(project)
    prompts = [dict(row) for row in prompts]
    competitors = [dict(row) for row in competitors]
    competitor_snapshot = json.dumps([
        {'name': competitor['name'], 'domain': competitor.get('domain')}
        for competitor in competitors
    ], ensure_ascii=False)
    region = None
    with engine.connect() as conn:
        schedule = conn.execute(select(analytics_scan_schedules).where(
            analytics_scan_schedules.c.project_id == project['id']
        )).mappings().first()
    if schedule:
        region = schedule['region']
    now = datetime.utcnow()
    with engine.begin() as conn:
        existing_run = conn.execute(select(analytics_prompt_scan_runs.c.id).where(
            analytics_prompt_scan_runs.c.job_id == job_id
        )).scalar_one_or_none()
        if existing_run:
            scan_id = existing_run
            existing_answer_ids = [row[0] for row in conn.execute(select(analytics_provider_answers.c.id).where(
                analytics_provider_answers.c.scan_run_id == scan_id
            )).all()]
            if existing_answer_ids:
                conn.execute(analytics_answer_sources.delete().where(
                    analytics_answer_sources.c.answer_id.in_(existing_answer_ids)
                ))
            conn.execute(analytics_provider_answers.delete().where(
                analytics_provider_answers.c.scan_run_id == scan_id
            ))
            conn.execute(analytics_content_opportunities.delete().where(
                analytics_content_opportunities.c.scan_run_id == scan_id
            ))
            conn.execute(update(analytics_prompt_scan_runs).where(
                analytics_prompt_scan_runs.c.id == scan_id
            ).values(
                status='running', prompt_count=len(prompts), completed_count=0,
                competitor_snapshot=competitor_snapshot,
                mention_rate=None, citation_rate=None, source_presence_rate=None,
                share_of_voice=None, recommendation_summary=None, error=None,
                completed_at=None,
            ))
        else:
            result = conn.execute(insert(analytics_prompt_scan_runs).values(
                project_id=project['id'], job_id=job_id, provider='Perplexity',
                model=f"preset:{os.environ.get('PERPLEXITY_AGENT_PRESET', 'low')}"[:160], region=region,
                competitor_snapshot=competitor_snapshot,
                status='running', prompt_count=len(prompts), completed_count=0,
                mention_rate=None, citation_rate=None, source_presence_rate=None,
                share_of_voice=None, recommendation_summary=None, error=None,
                created_at=now, completed_at=None,
            ))
            scan_id = result.inserted_primary_key[0]
        conn.execute(update(analytics_audit_jobs).where(analytics_audit_jobs.c.id == job_id).values(
            total_items=len(prompts), completed_items=0, progress=0,
        ))

    failures = []
    for index, prompt in enumerate(prompts, 1):
        started = time.monotonic()
        search_payload = None
        answer_payload = None
        errors = []
        try:
            search_payload = call_perplexity_search(prompt['prompt'], region)
        except ProviderAPIError as error:
            errors.append(f'Search API: {error}')
        try:
            answer_payload = call_perplexity_answer(prompt['prompt'])
            if not perplexity_answer_text(answer_payload):
                errors.append('Agent API: completed without answer text')
        except ProviderAPIError as error:
            errors.append(f'Agent API: {error}')
        if errors:
            failures.append(f"Prompt {prompt['id']}: {'; '.join(errors)}")
        persist_provider_answer(
            scan_id, prompt, project, search_payload, answer_payload, errors,
            round((time.monotonic() - started) * 1000),
        )
        progress = round(index / len(prompts) * 100)
        update_analytics_job(job_id, completed_items=index, total_items=len(prompts), progress=progress)
        if index < len(prompts):
            time.sleep(max(0, min(float(os.environ.get('PERPLEXITY_REQUEST_DELAY_SECONDS', '0.2')), 3)))

    evidence_rows = provider_evidence_rows(scan_id)
    returned_models = list(dict.fromkeys(row['model'] for row in evidence_rows if row.get('model')))
    answer_measured = [row for row in evidence_rows if row.get('answer_text')]
    source_measured = [row for row in evidence_rows if row.get('source_present') is not None]
    mention_rate = round(sum(bool(row['brand_mentioned']) for row in answer_measured) / len(answer_measured) * 100, 2) if answer_measured else None
    citation_rate = round(sum(bool(row['brand_cited']) for row in answer_measured) / len(answer_measured) * 100, 2) if answer_measured else None
    source_presence_rate = round(sum(bool(row['source_present']) for row in source_measured) / len(source_measured) * 100, 2) if source_measured else None

    brand_occurrences = sum(text_mentions_alias(row.get('answer_text'), project_brand_aliases(project)) for row in answer_measured)
    competitor_occurrences = 0
    for competitor in competitors:
        aliases = [competitor['name'], competitor.get('domain')]
        competitor_occurrences += sum(text_mentions_alias(row.get('answer_text'), aliases) for row in answer_measured)
    voice_denominator = brand_occurrences + competitor_occurrences
    share_of_voice = round(brand_occurrences / voice_denominator * 100, 2) if voice_denominator else None

    rule_opportunities = rule_based_opportunities(project, evidence_rows)
    opportunity_source = 'stored-evidence rules'
    opportunities = rule_opportunities
    open_model = open_model_settings()
    if open_model['configured']:
        try:
            model_opportunities = open_model_evidence_opportunities(project, evidence_rows)
            if model_opportunities:
                opportunities = model_opportunities
                opportunity_source = f"{open_model['provider']} · {open_model['model']}"
        except (ProviderAPIError, json.JSONDecodeError) as error:
            failures.append(f'Open-model recommendation layer: {error}')
    with engine.begin() as conn:
        conn.execute(analytics_content_opportunities.delete().where(
            analytics_content_opportunities.c.scan_run_id == scan_id
        ))
        if opportunities:
            conn.execute(insert(analytics_content_opportunities), [
                {
                    'project_id': project['id'], 'scan_run_id': scan_id, 'source': opportunity_source,
                    'title': item['title'][:255], 'rationale': item['rationale'],
                    'evidence_refs': item['evidence_refs'], 'priority': item['priority'],
                    'created_at': datetime.utcnow(),
                }
                for item in opportunities
            ])
        completed_count = len([row for row in evidence_rows if row['status'] in {'succeeded', 'partial'}])
        scan_status = 'succeeded' if completed_count == len(prompts) and not failures else ('partial' if completed_count else 'failed')
        summary = '; '.join(item['title'] for item in opportunities[:3]) if opportunities else None
        conn.execute(update(analytics_prompt_scan_runs).where(analytics_prompt_scan_runs.c.id == scan_id).values(
            status=scan_status, completed_count=completed_count,
            model=(' / '.join(returned_models)[:160] if returned_models else f"preset:{os.environ.get('PERPLEXITY_AGENT_PRESET', 'low')}"),
            mention_rate=mention_rate, citation_rate=citation_rate,
            source_presence_rate=source_presence_rate, share_of_voice=share_of_voice,
            recommendation_summary=summary, error='\n'.join(failures)[:5000] if failures else None,
            completed_at=datetime.utcnow(),
        ))
    final_job_status = 'succeeded' if evidence_rows and any(row['status'] in {'succeeded', 'partial'} for row in evidence_rows) else 'failed_terminal'
    update_analytics_job(
        job_id, status=final_job_status, progress=100, completed_items=len(prompts), total_items=len(prompts),
        error='\n'.join(failures)[:2000] if final_job_status != 'succeeded' else None,
        completed_at=datetime.utcnow(),
    )
