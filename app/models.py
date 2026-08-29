"""SQLAlchemy Core table definitions, one place."""

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

from app.db import metadata

contacts = Table(
    'contacts',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('name', String(255), nullable=False),
    Column('email', String(255), nullable=False),
    Column('message', Text, nullable=False),
    Column('created_at', DateTime, nullable=False),
)

users = Table(
    'users',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('username', String(150), nullable=False, unique=True),
    Column('email', String(255), nullable=False, unique=True),
    Column('password_hash', String(255), nullable=False),
    Column('created_at', DateTime, nullable=False),
)

app_metadata = Table(
    'app_metadata',
    metadata,
    Column('key', String(100), primary_key=True),
    Column('value', String(255), nullable=False),
)

analytics_projects = Table(
    'analytics_projects',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('user_id', Integer, nullable=False, index=True),
    Column('domain', String(255), nullable=False),
    Column('website_url', String(2048), nullable=True),
    Column('brand_name', String(150), nullable=False),
    Column('industry', String(150), nullable=False, default='General'),
    Column('created_at', DateTime, nullable=False),
    Column('updated_at', DateTime, nullable=False),
)

analytics_runs = Table(
    'analytics_runs',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('project_id', Integer, nullable=False, index=True),
    Column('visibility_score', Integer, nullable=False),
    Column('mention_rate', Integer, nullable=False),
    Column('citation_rate', Integer, nullable=False),
    Column('share_of_voice', Integer, nullable=False),
    Column('summary', Text, nullable=False),
    Column('created_at', DateTime, nullable=False),
)

analytics_engine_metrics = Table(
    'analytics_engine_metrics',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('run_id', Integer, nullable=False, index=True),
    Column('engine', String(80), nullable=False),
    Column('visibility_score', Integer, nullable=False),
    Column('mention_rate', Integer, nullable=False),
    Column('citations', Integer, nullable=False),
    Column('change', Integer, nullable=False),
)

analytics_prompts = Table(
    'analytics_prompts',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('run_id', Integer, nullable=False, index=True),
    Column('prompt', Text, nullable=False),
    Column('intent', String(80), nullable=False),
    Column('position', Integer, nullable=False),
    Column('cited', String(8), nullable=False),
    Column('leading_brand', String(150), nullable=False),
    Column('opportunity', String(255), nullable=False),
)

analytics_audit_jobs = Table(
    'analytics_audit_jobs',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('project_id', Integer, nullable=False, index=True),
    Column('user_id', Integer, nullable=False, index=True),
    Column('job_type', String(40), nullable=False),
    Column('provider', String(40), nullable=True),
    Column('status', String(32), nullable=False),
    Column('progress', Integer, nullable=False, default=0),
    Column('total_items', Integer, nullable=False, default=0),
    Column('completed_items', Integer, nullable=False, default=0),
    Column('error', Text, nullable=True),
    Column('created_at', DateTime, nullable=False),
    Column('started_at', DateTime, nullable=True),
    Column('completed_at', DateTime, nullable=True),
)

analytics_site_audits = Table(
    'analytics_site_audits',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('project_id', Integer, nullable=False, index=True),
    Column('job_id', Integer, nullable=True, index=True),
    Column('status', String(32), nullable=False),
    Column('source_type', String(40), nullable=False, default='website_crawl'),
    Column('start_url', String(2048), nullable=False),
    Column('final_url', String(2048), nullable=True),
    Column('pages_discovered', Integer, nullable=False, default=0),
    Column('pages_audited', Integer, nullable=False, default=0),
    Column('pages_failed', Integer, nullable=False, default=0),
    Column('readiness_score', Integer, nullable=True),
    Column('metadata_score', Integer, nullable=True),
    Column('content_score', Integer, nullable=True),
    Column('crawlability_score', Integer, nullable=True),
    Column('structured_data_score', Integer, nullable=True),
    Column('summary', Text, nullable=False),
    Column('created_at', DateTime, nullable=False),
    Column('completed_at', DateTime, nullable=True),
)

analytics_audit_pages = Table(
    'analytics_audit_pages',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('audit_id', Integer, nullable=False, index=True),
    Column('url', String(2048), nullable=False),
    Column('final_url', String(2048), nullable=True),
    Column('fetched', Boolean, nullable=False, default=False),
    Column('http_status', Integer, nullable=True),
    Column('title', Text, nullable=True),
    Column('description', Text, nullable=True),
    Column('headings_count', Integer, nullable=False, default=0),
    Column('word_count', Integer, nullable=False, default=0),
    Column('schema_blocks', Integer, nullable=False, default=0),
    Column('canonical', String(2048), nullable=True),
    Column('noindex', Boolean, nullable=False, default=False),
    Column('language', String(40), nullable=True),
    Column('internal_links', Integer, nullable=False, default=0),
    Column('external_links', Integer, nullable=False, default=0),
    Column('readiness_score', Integer, nullable=True),
    Column('metadata_score', Integer, nullable=True),
    Column('content_score', Integer, nullable=True),
    Column('crawlability_score', Integer, nullable=True),
    Column('structured_data_score', Integer, nullable=True),
    Column('issues_count', Integer, nullable=False, default=0),
    Column('error', Text, nullable=True),
    Column('fetched_at', DateTime, nullable=False),
    UniqueConstraint('audit_id', 'url', name='uq_analytics_audit_page'),
)

analytics_audit_findings = Table(
    'analytics_audit_findings',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('audit_id', Integer, nullable=False, index=True),
    Column('page_id', Integer, nullable=True, index=True),
    Column('code', String(80), nullable=False),
    Column('area', String(40), nullable=False),
    Column('severity', String(20), nullable=False),
    Column('evidence', Text, nullable=False),
    Column('recommendation', Text, nullable=False),
)

analytics_sitemaps = Table(
    'analytics_sitemaps',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('audit_id', Integer, nullable=False, index=True),
    Column('url', String(2048), nullable=False),
    Column('status', String(32), nullable=False),
    Column('urls_discovered', Integer, nullable=False, default=0),
    Column('error', Text, nullable=True),
)

analytics_rag_documents = Table(
    'analytics_rag_documents',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('project_id', Integer, nullable=False, index=True),
    Column('audit_id', Integer, nullable=False, index=True),
    Column('page_id', Integer, nullable=False, index=True),
    Column('url', String(2048), nullable=False),
    Column('title', Text, nullable=True),
    Column('content_hash', String(64), nullable=False),
    Column('content_text', Text, nullable=False),
    Column('word_count', Integer, nullable=False),
    Column('created_at', DateTime, nullable=False),
    UniqueConstraint('audit_id', 'page_id', name='uq_analytics_rag_document_page'),
)

analytics_rag_chunks = Table(
    'analytics_rag_chunks',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('project_id', Integer, nullable=False, index=True),
    Column('audit_id', Integer, nullable=False, index=True),
    Column('document_id', Integer, nullable=False, index=True),
    Column('chunk_index', Integer, nullable=False),
    Column('content_hash', String(64), nullable=False),
    Column('content_text', Text, nullable=False),
    Column('token_count', Integer, nullable=False),
    Column('created_at', DateTime, nullable=False),
    UniqueConstraint('document_id', 'chunk_index', name='uq_analytics_rag_document_chunk'),
)

analytics_rag_insights = Table(
    'analytics_rag_insights',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('project_id', Integer, nullable=False, index=True),
    Column('audit_id', Integer, nullable=False, index=True),
    Column('question', Text, nullable=False),
    Column('provider', String(80), nullable=False),
    Column('model', String(160), nullable=False),
    Column('status', String(32), nullable=False),
    Column('answer_text', Text, nullable=True),
    Column('evidence_refs', Text, nullable=False),
    Column('retrieved_chunk_count', Integer, nullable=False, default=0),
    Column('error', Text, nullable=True),
    Column('created_at', DateTime, nullable=False),
)

gsc_connections = Table(
    'gsc_connections',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('project_id', Integer, nullable=False, unique=True),
    Column('user_id', Integer, nullable=False, index=True),
    Column('encrypted_refresh_token', Text, nullable=True),
    Column('encrypted_access_token', Text, nullable=True),
    Column('token_expires_at', DateTime, nullable=True),
    Column('granted_scopes', Text, nullable=True),
    Column('selected_property', String(2048), nullable=True),
    Column('status', String(32), nullable=False),
    Column('last_error', Text, nullable=True),
    Column('created_at', DateTime, nullable=False),
    Column('updated_at', DateTime, nullable=False),
)

gsc_properties = Table(
    'gsc_properties',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('connection_id', Integer, nullable=False, index=True),
    Column('site_url', String(2048), nullable=False),
    Column('permission_level', String(80), nullable=False),
    Column('selected', Boolean, nullable=False, default=False),
    UniqueConstraint('connection_id', 'site_url', name='uq_gsc_connection_property'),
)

gsc_sync_runs = Table(
    'gsc_sync_runs',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('project_id', Integer, nullable=False, index=True),
    Column('connection_id', Integer, nullable=False, index=True),
    Column('property_url', String(2048), nullable=False),
    Column('status', String(32), nullable=False),
    Column('start_date', String(10), nullable=False),
    Column('end_date', String(10), nullable=False),
    Column('rows_saved', Integer, nullable=False, default=0),
    Column('data_state', String(20), nullable=False, default='final'),
    Column('error', Text, nullable=True),
    Column('created_at', DateTime, nullable=False),
    Column('completed_at', DateTime, nullable=True),
)

gsc_query_rows = Table(
    'gsc_query_rows',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('sync_run_id', Integer, nullable=False, index=True),
    Column('query', Text, nullable=False),
    Column('page', String(2048), nullable=True),
    Column('clicks', Float, nullable=False),
    Column('impressions', Float, nullable=False),
    Column('ctr', Float, nullable=False),
    Column('position', Float, nullable=False),
)

analytics_topics = Table(
    'analytics_topics',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('project_id', Integer, nullable=False, index=True),
    Column('name', String(180), nullable=False),
    Column('created_at', DateTime, nullable=False),
    UniqueConstraint('project_id', 'name', name='uq_analytics_topic'),
)

analytics_competitors = Table(
    'analytics_competitors',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('project_id', Integer, nullable=False, index=True),
    Column('name', String(180), nullable=False),
    Column('domain', String(255), nullable=True),
    Column('created_at', DateTime, nullable=False),
    UniqueConstraint('project_id', 'name', name='uq_analytics_competitor'),
)

analytics_tracked_prompts = Table(
    'analytics_tracked_prompts',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('project_id', Integer, nullable=False, index=True),
    Column('topic_id', Integer, nullable=True, index=True),
    Column('prompt', Text, nullable=False),
    Column('intent', String(80), nullable=False, default='Discovery'),
    Column('active', Boolean, nullable=False, default=True),
    Column('created_at', DateTime, nullable=False),
    Column('updated_at', DateTime, nullable=False),
)

analytics_prompt_scan_runs = Table(
    'analytics_prompt_scan_runs',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('project_id', Integer, nullable=False, index=True),
    Column('job_id', Integer, nullable=True, index=True),
    Column('provider', String(40), nullable=False),
    Column('model', String(160), nullable=False),
    Column('region', String(8), nullable=True),
    Column('competitor_snapshot', Text, nullable=True),
    Column('status', String(32), nullable=False),
    Column('prompt_count', Integer, nullable=False, default=0),
    Column('completed_count', Integer, nullable=False, default=0),
    Column('mention_rate', Float, nullable=True),
    Column('citation_rate', Float, nullable=True),
    Column('source_presence_rate', Float, nullable=True),
    Column('share_of_voice', Float, nullable=True),
    Column('recommendation_summary', Text, nullable=True),
    Column('error', Text, nullable=True),
    Column('created_at', DateTime, nullable=False),
    Column('completed_at', DateTime, nullable=True),
)

analytics_provider_answers = Table(
    'analytics_provider_answers',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('scan_run_id', Integer, nullable=False, index=True),
    Column('prompt_id', Integer, nullable=False, index=True),
    Column('prompt_text', Text, nullable=True),
    Column('prompt_intent', String(80), nullable=True),
    Column('topic_name', String(180), nullable=True),
    Column('provider', String(40), nullable=False),
    Column('model', String(160), nullable=False),
    Column('status', String(32), nullable=False),
    Column('search_request_id', String(255), nullable=True),
    Column('answer_request_id', String(255), nullable=True),
    Column('answer_text', Text, nullable=True),
    Column('raw_response', Text, nullable=True),
    Column('brand_mentioned', Boolean, nullable=True),
    Column('brand_cited', Boolean, nullable=True),
    Column('source_present', Boolean, nullable=True),
    Column('best_source_rank', Integer, nullable=True),
    Column('latency_ms', Integer, nullable=True),
    Column('error', Text, nullable=True),
    Column('created_at', DateTime, nullable=False),
    Column('completed_at', DateTime, nullable=True),
)

analytics_answer_sources = Table(
    'analytics_answer_sources',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('answer_id', Integer, nullable=False, index=True),
    Column('rank', Integer, nullable=False),
    Column('source_kind', String(32), nullable=False),
    Column('title', Text, nullable=True),
    Column('url', String(2048), nullable=False),
    Column('domain', String(255), nullable=True),
    Column('snippet', Text, nullable=True),
    Column('published_at', String(80), nullable=True),
)

analytics_scan_schedules = Table(
    'analytics_scan_schedules',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('project_id', Integer, nullable=False, unique=True),
    Column('enabled', Boolean, nullable=False, default=False),
    Column('frequency', String(20), nullable=False, default='weekly'),
    Column('region', String(8), nullable=True),
    Column('next_run_at', DateTime, nullable=True),
    Column('last_run_at', DateTime, nullable=True),
    Column('created_at', DateTime, nullable=False),
    Column('updated_at', DateTime, nullable=False),
)

analytics_content_opportunities = Table(
    'analytics_content_opportunities',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('project_id', Integer, nullable=False, index=True),
    Column('scan_run_id', Integer, nullable=True, index=True),
    Column('source', String(80), nullable=False),
    Column('title', String(255), nullable=False),
    Column('rationale', Text, nullable=False),
    Column('evidence_refs', Text, nullable=False),
    Column('priority', String(20), nullable=False),
    Column('created_at', DateTime, nullable=False),
)

prompt_collections = Table(
    'prompt_collections',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('user_id', Integer, nullable=False, index=True),
    Column('name', String(150), nullable=False),
    Column('brand_name', String(150), nullable=False),
    Column('website', String(255), nullable=True),
    Column('created_at', DateTime, nullable=False),
    Column('updated_at', DateTime, nullable=False),
)

prompt_queries = Table(
    'prompt_queries',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('collection_id', Integer, nullable=False, index=True),
    Column('prompt', Text, nullable=False),
    Column('engine', String(80), nullable=False),
    Column('intent', String(80), nullable=False),
    Column('created_at', DateTime, nullable=False),
    Column('updated_at', DateTime, nullable=False),
)

prompt_query_results = Table(
    'prompt_query_results',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('query_id', Integer, nullable=False, index=True),
    Column('visibility_score', Integer, nullable=False),
    Column('brand_position', Integer, nullable=False),
    Column('cited', String(8), nullable=False),
    Column('leading_brand', String(150), nullable=False),
    Column('answer_summary', Text, nullable=False),
    Column('recommendation', Text, nullable=False),
    Column('created_at', DateTime, nullable=False),
)

visibility_watchlists = Table(
    'visibility_watchlists',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('user_id', Integer, nullable=False, index=True),
    Column('name', String(150), nullable=False),
    Column('brand_name', String(150), nullable=False),
    Column('website', String(255), nullable=True),
    Column('topic', String(150), nullable=False),
    Column('created_at', DateTime, nullable=False),
    Column('updated_at', DateTime, nullable=False),
)

visibility_scans = Table(
    'visibility_scans',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('watchlist_id', Integer, nullable=False, index=True),
    Column('visibility_score', Integer, nullable=False),
    Column('mentions_found', Integer, nullable=False),
    Column('citations_found', Integer, nullable=False),
    Column('competitor_mentions', Integer, nullable=False),
    Column('summary', Text, nullable=False),
    Column('created_at', DateTime, nullable=False),
)

visibility_engine_results = Table(
    'visibility_engine_results',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('scan_id', Integer, nullable=False, index=True),
    Column('engine', String(80), nullable=False),
    Column('visibility_score', Integer, nullable=False),
    Column('mentions', Integer, nullable=False),
    Column('citations', Integer, nullable=False),
    Column('change', Integer, nullable=False),
)

visibility_mentions = Table(
    'visibility_mentions',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('scan_id', Integer, nullable=False, index=True),
    Column('engine', String(80), nullable=False),
    Column('query', Text, nullable=False),
    Column('appearance', String(80), nullable=False),
    Column('sentiment', String(30), nullable=False),
    Column('cited', String(8), nullable=False),
    Column('competitor', String(150), nullable=False),
    Column('action', Text, nullable=False),
)

content_documents = Table(
    'content_documents',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('user_id', Integer, nullable=False, index=True),
    Column('title', String(200), nullable=False),
    Column('brand_name', String(150), nullable=False),
    Column('keyword', String(200), nullable=False),
    Column('content_type', String(80), nullable=False),
    Column('tone', String(80), nullable=False),
    Column('content', Text, nullable=False, default=''),
    Column('seo_title', String(200), nullable=False, default=''),
    Column('meta_description', String(320), nullable=False, default=''),
    Column('outline', Text, nullable=False, default=''),
    Column('recommendations', Text, nullable=False, default=''),
    Column('status', String(40), nullable=False, default='Draft'),
    Column('version', Integer, nullable=False, default=0),
    Column('created_at', DateTime, nullable=False),
    Column('updated_at', DateTime, nullable=False),
)

master_workspaces = Table(
    'master_workspaces',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('user_id', Integer, nullable=False, unique=True),
    Column('brand_name', String(150), nullable=False),
    Column('domain', String(255), nullable=False),
    Column('industry', String(150), nullable=False),
    Column('topic', String(200), nullable=False),
    Column('goal', String(200), nullable=False),
    Column('analytics_project_id', Integer, nullable=False),
    Column('visibility_watchlist_id', Integer, nullable=False),
    Column('prompt_collection_id', Integer, nullable=False),
    Column('content_document_id', Integer, nullable=False),
    Column('created_at', DateTime, nullable=False),
    Column('updated_at', DateTime, nullable=False),
)
