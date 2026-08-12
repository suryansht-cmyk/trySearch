import os
import uuid
import hashlib
import ipaddress
import json
import re
import secrets
import socket
import ssl
import threading
import time
import xml.etree.ElementTree as ET
from collections import deque
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
from urllib.robotparser import RobotFileParser
from datetime import date, datetime, timedelta

from flask import Flask, jsonify, request, send_from_directory, abort, session, redirect
from werkzeug.security import generate_password_hash, check_password_hash
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
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
SQLITE_PATH = os.path.join(BASE_DIR, 'searchable.db')

APP_ENV = os.environ.get('APP_ENV', 'development').lower()
IS_PRODUCTION = APP_ENV == 'production'


def normalize_database_url(database_url):
    """Use SQLAlchemy's psycopg 3 dialect with common Postgres URL formats."""
    if database_url.startswith('postgres://'):
        return database_url.replace('postgres://', 'postgresql+psycopg://', 1)
    if database_url.startswith('postgresql://'):
        return database_url.replace('postgresql://', 'postgresql+psycopg://', 1)
    return database_url


database_url = os.environ.get('DATABASE_URL')
if not database_url:
    if IS_PRODUCTION:
        raise RuntimeError('DATABASE_URL must be set when APP_ENV=production.')
    database_url = f'sqlite:///{SQLITE_PATH}'

DB_URL = normalize_database_url(database_url)

# Keep a small, resilient connection pool for managed Postgres. SQLite remains
# the zero-config local-development fallback.
engine_options = {'future': True, 'pool_pre_ping': True}
if DB_URL.startswith('postgresql'):
    engine_options.update({'pool_size': 5, 'max_overflow': 10, 'pool_recycle': 1800})
engine = create_engine(DB_URL, **engine_options)
metadata = MetaData()

# Table definitions (SQLAlchemy Core) - compatible with Postgres and SQLite
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

# This value is stored in the database itself. It makes it possible to pin a
# deployment to one specific database instance and fail safely if a deployment
# is accidentally configured with a different, empty DATABASE_URL.
app_metadata = Table(
    'app_metadata',
    metadata,
    Column('key', String(100), primary_key=True),
    Column('value', String(255), nullable=False),
)

# AI Search Analytics data is deliberately kept separate from account data so a
# user can track more than one client domain without sharing reports.
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

# Source-specific AI Search Analytics tables. The original analytics tables
# above are retained for backwards compatibility, but their columns were used
# for the first single-page technical audit. New evidence is stored with names
# that describe what was actually measured.
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

# Prompt Intelligence is a separate workflow: teams curate the questions they
# care about, then retain each prompt's benchmark result over time.
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

# Visibility Tracking keeps a focussed watchlist and its individual brand
# appearances separate from broad analytics projects and prompt collections.
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

# Content Studio documents keep the brief, generated starter draft, and the
# user's edits together so content work survives page refreshes and sessions.
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

# One master workspace ties the four product modules together from a single
# brand brief, preventing clients from having to repeat their onboarding data.
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

# Create tables if they don't exist
metadata.create_all(engine)


def ensure_database_column(table_name, column_name, column_type):
    """Apply the one additive migration required by older deployed databases."""
    from sqlalchemy import inspect
    existing_columns = {column['name'] for column in inspect(engine).get_columns(table_name)}
    if column_name not in existing_columns:
        try:
            with engine.begin() as conn:
                conn.execute(text(f'ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}'))
        except SQLAlchemyError:
            # Two Gunicorn workers can initialize at the same time. Ignore only
            # the race where the other worker successfully added this column.
            refreshed = {column['name'] for column in inspect(engine).get_columns(table_name)}
            if column_name not in refreshed:
                raise


ensure_database_column('analytics_projects', 'website_url', 'VARCHAR(2048)')
ensure_database_column('analytics_provider_answers', 'prompt_text', 'TEXT')
ensure_database_column('analytics_provider_answers', 'prompt_intent', 'VARCHAR(80)')
ensure_database_column('analytics_provider_answers', 'topic_name', 'VARCHAR(180)')
ensure_database_column('analytics_prompt_scan_runs', 'competitor_snapshot', 'TEXT')


def get_database_identity():
    """Return the database's permanent application identity, creating it once."""
    with engine.connect() as conn:
        row = conn.execute(
            select(app_metadata.c.value).where(app_metadata.c.key == 'database_identity')
        ).scalar_one_or_none()
    if row:
        return row

    database_identity = str(uuid.uuid4())
    try:
        with engine.begin() as conn:
            conn.execute(insert(app_metadata).values(
                key='database_identity', value=database_identity
            ))
        return database_identity
    except IntegrityError:
        # Another gunicorn worker initialized the row at the same time. Query
        # in a new transaction because PostgreSQL marks the failed one aborted.
        with engine.connect() as conn:
            return conn.execute(
                select(app_metadata.c.value).where(app_metadata.c.key == 'database_identity')
            ).scalar_one()


DATABASE_IDENTITY = get_database_identity()
EXPECTED_DATABASE_IDENTITY = os.environ.get('DATABASE_INSTANCE_ID')
if EXPECTED_DATABASE_IDENTITY and EXPECTED_DATABASE_IDENTITY != DATABASE_IDENTITY:
    raise RuntimeError(
        'DATABASE_INSTANCE_ID does not match the connected database. Refusing to start '
        'against an unexpected database.'
    )

app = Flask(__name__, static_folder='.', static_url_path='')
secret_key = os.environ.get('SECRET_KEY')
if IS_PRODUCTION and not secret_key:
    raise RuntimeError('SECRET_KEY must be set when APP_ENV=production.')
app.secret_key = secret_key or 'dev-secret-key-change-me'
app.permanent_session_lifetime = timedelta(days=30)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=IS_PRODUCTION,
)


def to_iso(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    return dt.isoformat() + 'Z'


def row_to_dict(row):
    d = dict(row)
    for key, value in list(d.items()):
        if isinstance(value, datetime):
            d[key] = to_iso(value)
        elif isinstance(value, date):
            d[key] = value.isoformat()
    return d


@app.route('/')
def index():
    return send_from_directory(BASE_DIR, 'index.html')


@app.route('/<path:path>')
def static_files(path):
    filepath = os.path.join(BASE_DIR, path)
    if os.path.exists(filepath) and os.path.isfile(filepath):
        return send_from_directory(BASE_DIR, path)
    abort(404)


@app.route('/api/contacts', methods=['GET', 'POST'])
def contacts_endpoint():
    if request.method == 'POST':
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'error': 'Invalid JSON payload.'}), 400

        name = (data.get('name') or '').strip()
        email = (data.get('email') or '').strip()
        message = (data.get('message') or '').strip()

        if not name or not email or not message:
            return jsonify({'error': 'Name, email, and message are required.'}), 400

        created_at = datetime.utcnow()
        with engine.begin() as conn:
            conn.execute(
                insert(contacts).values(name=name, email=email, message=message, created_at=created_at)
            )
        return jsonify({'status': 'success', 'message': 'Contact request submitted.'}), 201

    # GET
    with engine.connect() as conn:
        stmt = select(contacts.c.id, contacts.c.name, contacts.c.email, contacts.c.message, contacts.c.created_at).order_by(desc(contacts.c.created_at)).limit(100)
        result = conn.execute(stmt)
        rows = [row_to_dict(r) for r in result.mappings().all()]
    return jsonify({'status': 'success', 'contacts': rows})


@app.route('/api/health', methods=['GET'])
def health():
    try:
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
    except SQLAlchemyError:
        return jsonify({'status': 'error', 'db': engine.url.get_backend_name()}), 503
    return jsonify({
        'status': 'ok',
        'db': engine.url.get_backend_name(),
        'database_identity': DATABASE_IDENTITY,
    })


# Authentication endpoints
@app.route('/api/register', methods=['POST'])
def api_register():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Invalid payload'}), 400
    username = (data.get('username') or '').strip()
    email = (data.get('email') or '').strip()
    password = (data.get('password') or '').strip()

    if not username or not email or not password:
        return jsonify({'error': 'username, email and password required'}), 400

    password_hash = generate_password_hash(password)
    created_at = datetime.utcnow()
    try:
        with engine.begin() as conn:
            conn.execute(
                insert(users).values(username=username, email=email, password_hash=password_hash, created_at=created_at)
            )
    except IntegrityError:
        return jsonify({'error': 'User with that username or email already exists.'}), 400

    return jsonify({'status': 'success', 'message': 'User registered.'}), 201


@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Invalid payload'}), 400
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()
    remember = bool(data.get('remember'))

    if not username or not password:
        return jsonify({'error': 'username and password required'}), 400

    with engine.connect() as conn:
        stmt = select(users.c.id, users.c.username, users.c.password_hash).where((users.c.username == username) | (users.c.email == username)).limit(1)
        row = conn.execute(stmt).mappings().first()
        if not row:
            return jsonify({'error': 'Invalid credentials'}), 401
        user = dict(row)
        if not check_password_hash(user['password_hash'], password):
            return jsonify({'error': 'Invalid credentials'}), 401

    # login success
    session.clear()
    session['user_id'] = user['id']
    session['username'] = user['username']
    session.permanent = remember
    return jsonify({'status': 'success', 'message': 'Logged in', 'username': user['username']})


@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'status': 'success', 'message': 'Logged out'})


@app.route('/api/me', methods=['GET'])
def api_me():
    user_id = session.get('user_id')
    if user_id:
        with engine.connect() as conn:
            stmt = select(users.c.id, users.c.username, users.c.email, users.c.created_at).where(
                users.c.id == user_id
            ).limit(1)
            row = conn.execute(stmt).mappings().first()
        if row:
            return jsonify({'logged_in': True, 'user': row_to_dict(row)})
        session.clear()
    return jsonify({'logged_in': False})


def analytics_user_id():
    """Return the current account id, or an API response for unauthenticated calls."""
    user_id = session.get('user_id')
    if not user_id:
        return None, (jsonify({'error': 'Sign in to use AI Search Analytics.'}), 401)
    return user_id, None


def normalise_domain(value):
    value = (value or '').strip().lower()
    if not value:
        return None
    parsed = urlparse(value if '://' in value else f'https://{value}')
    domain = (parsed.netloc or '').split('@')[-1].split(':')[0].strip('.')
    if not domain or '.' not in domain or any(char.isspace() for char in domain):
        return None
    return domain.removeprefix('www.')


def normalise_website_url(value):
    """Retain an optional public path instead of silently reducing it to a host."""
    value = (value or '').strip()
    if not value:
        return None
    parsed = urlparse(value if '://' in value else f'https://{value}')
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc or any(char.isspace() for char in parsed.netloc):
        return None
    path = parsed.path or '/'
    return parsed._replace(path=path, params='', fragment='').geturl()


class WebsiteAuditParser(HTMLParser):
    """Small dependency-free HTML extractor for factual, on-page audit signals."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts = []
        self.heading_parts = []
        self._active_heading = None
        self._active_heading_parts = []
        self._in_title = False
        self._skip_text = 0
        self.text_parts = []
        self.description = ''
        self.robots = ''
        self.canonical = ''
        self.language = ''
        self.schema_blocks = 0
        self.internal_links = 0
        self.external_links = 0
        self.link_hrefs = []

    def handle_starttag(self, tag, attrs):
        attributes = {key.lower(): (value or '') for key, value in attrs}
        if tag == 'html':
            self.language = attributes.get('lang', '')
        elif tag == 'title':
            self._in_title = True
        elif tag in {'h1', 'h2', 'h3'}:
            self._active_heading = tag
            self._active_heading_parts = []
        elif tag in {'script', 'style', 'noscript'}:
            self._skip_text += 1
            if tag == 'script' and attributes.get('type', '').lower() == 'application/ld+json':
                self.schema_blocks += 1
        elif tag == 'meta':
            name = attributes.get('name', '').lower()
            property_name = attributes.get('property', '').lower()
            content = attributes.get('content', '').strip()
            if name == 'description' or property_name == 'og:description':
                self.description = self.description or content
            elif name == 'robots':
                self.robots = content.lower()
        elif tag == 'link' and 'canonical' in attributes.get('rel', '').lower():
            self.canonical = attributes.get('href', '').strip()
        elif tag == 'a':
            href = attributes.get('href', '').strip()
            if href:
                self.link_hrefs.append(href)
            if href.startswith(('http://', 'https://')):
                self.external_links += 1
            elif href:
                self.internal_links += 1

    def handle_endtag(self, tag):
        if tag == 'title':
            self._in_title = False
        elif tag == self._active_heading:
            heading = ' '.join(self._active_heading_parts).strip()
            if heading:
                self.heading_parts.append(heading)
            self._active_heading = None
            self._active_heading_parts = []
        elif tag in {'script', 'style', 'noscript'} and self._skip_text:
            self._skip_text -= 1

    def handle_data(self, data):
        text_value = ' '.join(data.split())
        if not text_value:
            return
        if self._in_title:
            self.title_parts.append(text_value)
        if self._active_heading:
            self._active_heading_parts.append(text_value)
        if not self._skip_text:
            self.text_parts.append(text_value)


class SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, new_url):
        validate_public_web_url(urljoin(request.full_url, new_url))
        return super().redirect_request(request, fp, code, msg, headers, new_url)


def verified_http_opener(*handlers):
    """Use an explicit CA bundle so TLS verification is consistent on macOS and Linux."""
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        context = ssl.create_default_context()
    return build_opener(*handlers, HTTPSHandler(context=context))


def validate_public_web_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        raise ValueError('Only public HTTP(S) website URLs can be audited.')
    if parsed.username or parsed.password:
        raise ValueError('Website URLs cannot contain credentials.')
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError('The website URL contains an invalid port.') from error
    if port not in {None, 80, 443}:
        raise ValueError('Only standard web ports can be audited.')
    host = parsed.hostname.lower().rstrip('.')
    if host == 'localhost' or host.endswith('.local'):
        raise ValueError('Local network addresses cannot be audited.')
    try:
        default_port = 443 if parsed.scheme == 'https' else 80
        addresses = {item[4][0] for item in socket.getaddrinfo(host, port or default_port, type=socket.SOCK_STREAM)}
    except socket.gaierror as error:
        raise ValueError('The website domain could not be resolved.') from error
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError('Only publicly routable website addresses can be audited.')
    return parsed


AUDIT_USER_AGENT = os.environ.get('AUDIT_USER_AGENT', 'trySearch-Audit/2.0 (+https://trysearch.example/audit)')
AUDIT_MAX_PAGES = max(1, min(int(os.environ.get('AUDIT_MAX_PAGES', '12')), 50))
AUDIT_PAGE_BYTES = max(100_000, min(int(os.environ.get('AUDIT_PAGE_BYTES', '800000')), 2_000_000))
AUDIT_SITEMAP_BYTES = max(200_000, min(int(os.environ.get('AUDIT_SITEMAP_BYTES', '2000000')), 5_000_000))
AUDIT_REQUEST_DELAY_SECONDS = max(0.0, min(float(os.environ.get('AUDIT_REQUEST_DELAY_SECONDS', '0.05')), 2.0))
ANALYTICS_MAX_TRACKED_PROMPTS = max(1, min(int(os.environ.get('ANALYTICS_MAX_TRACKED_PROMPTS', '100')), 500))
PERPLEXITY_MAX_PROMPTS_PER_SCAN = max(1, min(int(os.environ.get('PERPLEXITY_MAX_PROMPTS_PER_SCAN', '25')), 100))


def fetch_public_resource(url, *, max_bytes, accepted_types=None, timeout=12):
    """Fetch one public resource after validating every redirect target."""
    validate_public_web_url(url)
    http_request = Request(url, headers={
        'User-Agent': AUDIT_USER_AGENT,
        'Accept': 'text/html,application/xhtml+xml,application/xml,text/xml,text/plain;q=0.9,*/*;q=0.2',
    })
    opener = verified_http_opener(SafeRedirectHandler())
    with opener.open(http_request, timeout=timeout) as response:
        final_url = response.geturl()
        validate_public_web_url(final_url)
        content_type = response.headers.get_content_type().lower()
        if accepted_types and content_type not in accepted_types:
            raise ValueError(f'The resource returned unsupported content type {content_type}.')
        payload = response.read(max_bytes + 1)
        if len(payload) > max_bytes:
            raise ValueError('The resource is too large to audit safely.')
        return {
            'url': final_url,
            'status': getattr(response, 'status', 200),
            'content_type': content_type,
            'charset': response.headers.get_content_charset() or 'utf-8',
            'body': payload,
        }


def normalise_site_host(host):
    return (host or '').lower().rstrip('.').removeprefix('www.')


def same_site_host(host, allowed_hosts):
    return normalise_site_host(host) in {normalise_site_host(item) for item in allowed_hosts if item}


def canonicalise_crawl_url(base_url, href, allowed_hosts):
    """Return a stable same-site HTTP URL, removing fragments and tracking parameters."""
    if not href or href.startswith(('#', 'mailto:', 'tel:', 'javascript:', 'data:')):
        return None
    candidate = urljoin(base_url, href.strip())
    parsed = urlparse(candidate)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or not same_site_host(parsed.hostname, allowed_hosts):
        return None
    try:
        parsed_port = parsed.port
    except ValueError:
        return None
    tracking_prefixes = ('utm_',)
    tracking_names = {'gclid', 'fbclid', 'msclkid', 'mc_cid', 'mc_eid'}
    query_pairs = [
        (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in tracking_names and not key.lower().startswith(tracking_prefixes)
    ]
    query = urlencode(sorted(query_pairs))
    path = re.sub(r'/+', '/', parsed.path or '/')
    netloc = parsed.hostname.lower()
    if parsed_port and not ((parsed.scheme == 'https' and parsed_port == 443) or (parsed.scheme == 'http' and parsed_port == 80)):
        netloc = f'{netloc}:{parsed_port}'
    return urlunparse((parsed.scheme.lower(), netloc, path, '', query, ''))


def fetch_website_snapshot(website_url):
    """Fetch a public page and return transparent, first-party page evidence.

    These facts are intentionally limited to what the site itself exposes. They
    are not presented as a measurement of third-party AI model results.
    """
    start_url = website_url if website_url.startswith(('http://', 'https://')) else f'https://{website_url}/'
    try:
        resource = fetch_public_resource(
            start_url,
            max_bytes=AUDIT_PAGE_BYTES,
            accepted_types={'text/html', 'application/xhtml+xml'},
        )
        parser = WebsiteAuditParser()
        parser.feed(resource['body'].decode(resource['charset'], errors='replace'))
        parser.close()
        final_host = urlparse(resource['url']).hostname
        internal_links = []
        external_links = 0
        for href in parser.link_hrefs:
            absolute = urljoin(resource['url'], href)
            parsed_link = urlparse(absolute)
            if parsed_link.scheme in {'http', 'https'} and parsed_link.hostname:
                if normalise_site_host(parsed_link.hostname) == normalise_site_host(final_host):
                    internal_links.append(absolute)
                else:
                    external_links += 1
        return {
            'fetched': True,
            'url': resource['url'],
            'http_status': resource['status'],
            'title': ' '.join(parser.title_parts).strip(),
            'description': parser.description,
            'headings': parser.heading_parts[:12],
            'word_count': len(re.findall(r"\b[\w'-]+\b", ' '.join(parser.text_parts))),
            'schema_blocks': parser.schema_blocks,
            'canonical': parser.canonical,
            'noindex': 'noindex' in parser.robots,
            'language': parser.language,
            'internal_links': len(internal_links),
            'external_links': external_links,
            'links': internal_links,
        }
    except HTTPError as error:
        return {'fetched': False, 'url': start_url, 'http_status': error.code, 'error': f'HTTP {error.code}: {error.reason}'}
    except (URLError, TimeoutError, ValueError, OSError) as error:
        return {'fetched': False, 'url': start_url, 'http_status': None, 'error': str(error)}


def score_website_snapshot(snapshot):
    """Score one fetched page and return explicit, page-level findings."""
    if not snapshot.get('fetched'):
        return {
            'readiness_score': None, 'metadata_score': None, 'content_score': None,
            'crawlability_score': None, 'structured_data_score': None,
            'findings': [{
                'code': 'fetch_failed', 'area': 'Access', 'severity': 'high',
                'evidence': snapshot.get('error') or 'The page could not be fetched.',
                'recommendation': 'Confirm the exact public URL, DNS, status code, and crawler access before retrying.',
            }],
        }

    title_present = bool(snapshot.get('title'))
    description_present = bool(snapshot.get('description'))
    metadata_score = (50 if title_present else 0) + (50 if description_present else 0)
    headings_count = len(snapshot.get('headings') or [])
    word_count = snapshot.get('word_count') or 0
    content_score = min(100, round(min(word_count, 900) / 9 * 0.72 + min(headings_count, 6) / 6 * 35))
    structured_data_score = 100 if snapshot.get('schema_blocks') else 0
    if snapshot.get('noindex'):
        crawlability_score = 0
    elif snapshot.get('canonical'):
        crawlability_score = 100
    else:
        crawlability_score = 78
    readiness_score = round((metadata_score + content_score + structured_data_score + crawlability_score) / 4)

    findings = []
    if not title_present:
        findings.append({'code': 'missing_title', 'area': 'On-page', 'severity': 'high', 'evidence': 'No HTML title was found.', 'recommendation': 'Add a unique, descriptive title that names the page topic and brand.'})
    if not description_present:
        findings.append({'code': 'missing_description', 'area': 'On-page', 'severity': 'medium', 'evidence': 'No meta or Open Graph description was found.', 'recommendation': 'Add a concise description of the page answer, offer, and audience.'})
    if headings_count == 0:
        findings.append({'code': 'missing_headings', 'area': 'Content', 'severity': 'high', 'evidence': 'No H1–H3 headings were found.', 'recommendation': 'Organise the page with one clear H1 and question-led supporting headings.'})
    if word_count < 250:
        findings.append({'code': 'thin_content', 'area': 'Content', 'severity': 'medium', 'evidence': f'{word_count} visible words were found.', 'recommendation': 'Add direct answers, original examples, definitions, and supporting evidence for the page topic.'})
    if not snapshot.get('schema_blocks'):
        findings.append({'code': 'missing_schema', 'area': 'Structured data', 'severity': 'medium', 'evidence': 'No JSON-LD blocks were found.', 'recommendation': 'Add valid JSON-LD that accurately describes the organisation, service, product, article, or page.'})
    if snapshot.get('noindex'):
        findings.append({'code': 'noindex', 'area': 'Crawlability', 'severity': 'critical', 'evidence': 'A noindex robots directive was found.', 'recommendation': 'Remove noindex if this page should be discoverable in public search.'})
    if not snapshot.get('canonical'):
        findings.append({'code': 'missing_canonical', 'area': 'Crawlability', 'severity': 'low', 'evidence': 'No canonical link was found.', 'recommendation': 'Add a self-referencing canonical URL when this is the preferred public version.'})
    if not snapshot.get('language'):
        findings.append({'code': 'missing_language', 'area': 'Accessibility', 'severity': 'low', 'evidence': 'The HTML element has no lang attribute.', 'recommendation': 'Set the document language so parsers can interpret the page correctly.'})
    return {
        'readiness_score': readiness_score,
        'metadata_score': metadata_score,
        'content_score': content_score,
        'crawlability_score': crawlability_score,
        'structured_data_score': structured_data_score,
        'findings': findings,
    }


def sitemap_locations(xml_body):
    root = ET.fromstring(xml_body)
    root_name = root.tag.rsplit('}', 1)[-1].lower()
    locations = []
    for element in root.iter():
        if element.tag.rsplit('}', 1)[-1].lower() == 'loc' and element.text:
            locations.append(element.text.strip())
    return root_name, locations


def discover_sitemap_pages(base_url, allowed_hosts, max_candidates):
    """Read robots.txt and bounded sitemap indexes without leaving the site."""
    parsed_base = urlparse(base_url)
    origin = f'{parsed_base.scheme}://{parsed_base.netloc}'
    robots_url = urljoin(origin, '/robots.txt')
    sitemap_queue = deque()
    sitemap_records = []
    robots_parser = RobotFileParser()
    robots_parser.set_url(robots_url)
    try:
        resource = fetch_public_resource(robots_url, max_bytes=500_000, accepted_types={'text/plain', 'text/html'})
        robots_text = resource['body'].decode(resource['charset'], errors='replace')
        robots_parser.parse(robots_text.splitlines())
        for line in robots_text.splitlines():
            match = re.match(r'^\s*sitemap\s*:\s*(\S+)\s*$', line, flags=re.I)
            if match:
                candidate = canonicalise_crawl_url(origin, match.group(1), allowed_hosts)
                if candidate:
                    sitemap_queue.append(candidate)
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        # Absence of robots.txt does not block a public audit.
        robots_parser.parse([])

    default_sitemap = canonicalise_crawl_url(origin, '/sitemap.xml', allowed_hosts)
    if default_sitemap and default_sitemap not in sitemap_queue:
        sitemap_queue.append(default_sitemap)

    page_urls = []
    visited_sitemaps = set()
    while sitemap_queue and len(visited_sitemaps) < 8 and len(page_urls) < max_candidates:
        sitemap_url = sitemap_queue.popleft()
        if sitemap_url in visited_sitemaps:
            continue
        visited_sitemaps.add(sitemap_url)
        record = {'url': sitemap_url, 'status': 'failed', 'urls_discovered': 0, 'error': None}
        try:
            resource = fetch_public_resource(
                sitemap_url,
                max_bytes=AUDIT_SITEMAP_BYTES,
                accepted_types={'application/xml', 'text/xml', 'application/rss+xml', 'text/plain'},
            )
            root_name, locations = sitemap_locations(resource['body'])
            if root_name == 'sitemapindex':
                for location in locations:
                    child = canonicalise_crawl_url(sitemap_url, location, allowed_hosts)
                    if child and child not in visited_sitemaps and len(sitemap_queue) < 16:
                        sitemap_queue.append(child)
            else:
                for location in locations:
                    page_url = canonicalise_crawl_url(sitemap_url, location, allowed_hosts)
                    if page_url and page_url not in page_urls:
                        page_urls.append(page_url)
                        if len(page_urls) >= max_candidates:
                            break
            record.update(status='fetched', urls_discovered=len(locations))
        except (HTTPError, URLError, TimeoutError, ValueError, OSError, ET.ParseError) as error:
            record['error'] = str(error)
        sitemap_records.append(record)
    return page_urls, sitemap_records, robots_parser


def crawl_website(website_url, *, max_pages=None, progress_callback=None):
    """Perform a bounded, same-site crawl seeded by sitemaps and internal links."""
    max_pages = max_pages or AUDIT_MAX_PAGES
    start_url = website_url if website_url.startswith(('http://', 'https://')) else f'https://{website_url}/'
    first_snapshot = fetch_website_snapshot(start_url)
    if not first_snapshot.get('fetched'):
        scored = score_website_snapshot(first_snapshot)
        first_snapshot.update(scored)
        first_snapshot['requested_url'] = start_url
        first_snapshot['fetched_at'] = datetime.utcnow()
        return {
            'status': 'failed', 'start_url': start_url, 'final_url': None,
            'pages_discovered': 1, 'pages_audited': 0, 'pages_failed': 1,
            'pages': [first_snapshot], 'sitemaps': [], 'summary': f"Multi-page audit unavailable: {first_snapshot.get('error', 'The start page could not be fetched.')}",
            'readiness_score': None, 'metadata_score': None, 'content_score': None,
            'crawlability_score': None, 'structured_data_score': None,
        }

    start_host = urlparse(start_url).hostname
    final_host = urlparse(first_snapshot['url']).hostname
    allowed_hosts = {start_host, final_host}
    sitemap_urls, sitemap_records, robots_parser = discover_sitemap_pages(
        first_snapshot['url'], allowed_hosts, max(max_pages * 5, max_pages)
    )

    seed_url = canonicalise_crawl_url(first_snapshot['url'], first_snapshot['url'], allowed_hosts) or first_snapshot['url']
    queue = deque([seed_url])
    for page_url in sitemap_urls:
        if page_url != seed_url:
            queue.append(page_url)
    for href in first_snapshot.get('links', []):
        candidate = canonicalise_crawl_url(first_snapshot['url'], href, allowed_hosts)
        if candidate and candidate not in queue:
            queue.append(candidate)

    seen = set()
    pages = []
    while queue and len(pages) < max_pages:
        requested_url = queue.popleft()
        if requested_url in seen:
            continue
        seen.add(requested_url)
        if requested_url == seed_url:
            snapshot = dict(first_snapshot)
        elif not robots_parser.can_fetch(AUDIT_USER_AGENT, requested_url):
            snapshot = {'fetched': False, 'url': requested_url, 'http_status': None, 'error': 'Blocked by robots.txt for the trySearch audit user agent.'}
        else:
            if AUDIT_REQUEST_DELAY_SECONDS:
                time.sleep(AUDIT_REQUEST_DELAY_SECONDS)
            snapshot = fetch_website_snapshot(requested_url)
        snapshot['requested_url'] = requested_url
        snapshot['fetched_at'] = datetime.utcnow()
        snapshot.update(score_website_snapshot(snapshot))
        pages.append(snapshot)

        if snapshot.get('fetched'):
            for href in snapshot.get('links', []):
                candidate = canonicalise_crawl_url(snapshot['url'], href, allowed_hosts)
                if candidate and candidate not in seen and candidate not in queue and len(queue) < max_pages * 5:
                    queue.append(candidate)
        if progress_callback:
            progress_callback(len(pages), min(max_pages, len(pages) + len(queue)))

    successful = [page for page in pages if page.get('fetched')]
    failed_count = len(pages) - len(successful)
    metric_names = ('readiness_score', 'metadata_score', 'content_score', 'crawlability_score', 'structured_data_score')
    aggregates = {
        metric: round(sum(page[metric] for page in successful) / len(successful)) if successful else None
        for metric in metric_names
    }
    status = 'succeeded' if successful and not failed_count else ('partial' if successful else 'failed')
    discovered_count = max(len(seen) + len(queue), len(sitemap_urls), len(pages))
    summary = (
        f"Audited {len(successful)} of {min(discovered_count, max_pages)} selected pages from {len(sitemap_records)} sitemap source(s). "
        f"The aggregate AI-search readiness score is {aggregates['readiness_score']}% and is derived only from fetched website evidence."
        if successful else 'No public HTML pages could be audited, so no readiness score was calculated.'
    )
    return {
        'status': status,
        'start_url': start_url,
        'final_url': first_snapshot['url'],
        'pages_discovered': discovered_count,
        'pages_audited': len(successful),
        'pages_failed': failed_count,
        'pages': pages,
        'sitemaps': sitemap_records,
        'summary': summary,
        **aggregates,
    }


def make_analytics_report(project, run_number):
    """Compatibility report built from a one-page factual crawl, never mock data."""
    page = fetch_website_snapshot(project.get('website_url') or project['domain'])
    page.update(score_website_snapshot(page))
    if not page.get('fetched'):
        message = f"Live audit unavailable: {page.get('error', 'The page could not be fetched.')}"
        return {
            'visibility_score': 0, 'mention_rate': 0, 'citation_rate': 0, 'share_of_voice': 0,
            'summary': f"{message} No AI visibility score was calculated, so this report contains no estimated model data.",
            'engines': [{'engine': 'Website reachability', 'visibility_score': 0, 'mention_rate': 0, 'citations': 0, 'change': 0}],
            'prompts': [{'prompt': 'Live page fetch', 'intent': 'Access', 'position': 0, 'cited': 'Unavailable', 'leading_brand': page['url'], 'opportunity': message}],
        }
    snapshot = page
    metadata = snapshot['metadata_score']
    content_coverage = snapshot['content_score']
    structured_data = snapshot['structured_data_score']
    crawlability = snapshot['crawlability_score']
    readiness = snapshot['readiness_score']
    audit_rows = [
        ('Structured data', 'Technical', structured_data, 'Present' if snapshot['schema_blocks'] else 'Missing', f"{snapshot['schema_blocks']} JSON-LD block(s)", 'Add valid JSON-LD for your organisation, product or service, and key pages.'),
        ('Metadata', 'On-page', metadata, 'Present' if metadata == 100 else 'Needs work', f"Title: {'yes' if snapshot['title'] else 'no'} · description: {'yes' if snapshot['description'] else 'no'}", 'Write a unique title and concise meta description that clearly state the offer and audience.'),
        ('Content coverage', 'Content', content_coverage, 'Present' if content_coverage >= 70 else 'Needs work', f"{snapshot['word_count']} visible words · {len(snapshot['headings'])} headings", 'Add clear question-led headings, original examples, and direct answers to core buyer questions.'),
        ('Crawlability', 'Technical', crawlability, 'Present' if crawlability == 100 else 'Needs work', 'No noindex directive found' if not snapshot['noindex'] else 'noindex directive detected', 'Allow indexing and add a self-referencing canonical URL on the page.'),
    ]
    engines = [
        {'engine': label, 'visibility_score': score, 'mention_rate': score, 'citations': 1 if status == 'Present' else 0, 'change': 0}
        for label, _area, score, status, _evidence, _action in audit_rows
    ]
    prompts = [
        {'prompt': label, 'intent': area, 'position': score, 'cited': status, 'leading_brand': evidence, 'opportunity': action}
        for label, area, score, status, evidence, action in audit_rows
    ]
    page_name = snapshot['title'] or project['domain']
    summary = (
        f"Live audit of {snapshot['url']} found '{page_name}'. AI-search readiness is {readiness}% based on publicly available page signals "
        f"({snapshot['word_count']} visible words, {len(snapshot['headings'])} headings, and {snapshot['schema_blocks']} JSON-LD blocks). "
        'This is a website-derived technical audit, not a claim about live ChatGPT, Claude, or Perplexity answers.'
    )
    return {
        'visibility_score': readiness,
        'mention_rate': metadata,
        'citation_rate': content_coverage,
        'share_of_voice': crawlability,
        'summary': summary,
        'engines': engines,
        'prompts': prompts,
    }


def create_analytics_job(project, user_id, job_type, provider=None):
    now = datetime.utcnow()
    with engine.begin() as conn:
        result = conn.execute(insert(analytics_audit_jobs).values(
            project_id=project['id'], user_id=user_id, job_type=job_type,
            provider=provider, status='queued', progress=0, total_items=0,
            completed_items=0, error=None, created_at=now,
            started_at=None, completed_at=None,
        ))
    return result.inserted_primary_key[0]


def update_analytics_job(job_id, **values):
    with engine.begin() as conn:
        conn.execute(update(analytics_audit_jobs).where(analytics_audit_jobs.c.id == job_id).values(**values))


def analytics_job_for_user(job_id, user_id):
    with engine.connect() as conn:
        row = conn.execute(select(analytics_audit_jobs).where(
            (analytics_audit_jobs.c.id == job_id) & (analytics_audit_jobs.c.user_id == user_id)
        )).mappings().first()
    return row_to_dict(row) if row else None


def persist_site_audit(project_id, job_id, crawl):
    """Persist the aggregate, every selected page, sitemap, and finding atomically."""
    now = datetime.utcnow()
    with engine.begin() as conn:
        result = conn.execute(insert(analytics_site_audits).values(
            project_id=project_id, job_id=job_id, status=crawl['status'],
            source_type='website_crawl', start_url=crawl['start_url'][:2048],
            final_url=(crawl.get('final_url') or '')[:2048] or None,
            pages_discovered=crawl['pages_discovered'], pages_audited=crawl['pages_audited'],
            pages_failed=crawl['pages_failed'], readiness_score=crawl['readiness_score'],
            metadata_score=crawl['metadata_score'], content_score=crawl['content_score'],
            crawlability_score=crawl['crawlability_score'],
            structured_data_score=crawl['structured_data_score'], summary=crawl['summary'],
            created_at=now, completed_at=now,
        ))
        audit_id = result.inserted_primary_key[0]

        for sitemap in crawl.get('sitemaps', []):
            conn.execute(insert(analytics_sitemaps).values(
                audit_id=audit_id, url=sitemap['url'][:2048], status=sitemap['status'],
                urls_discovered=sitemap.get('urls_discovered', 0), error=sitemap.get('error'),
            ))

        for page in crawl.get('pages', []):
            score = page.get('readiness_score')
            page_result = conn.execute(insert(analytics_audit_pages).values(
                audit_id=audit_id, url=page.get('requested_url', page.get('url', ''))[:2048],
                final_url=(page.get('url') or '')[:2048] or None,
                fetched=bool(page.get('fetched')), http_status=page.get('http_status'),
                title=page.get('title'), description=page.get('description'),
                headings_count=len(page.get('headings') or []), word_count=page.get('word_count', 0),
                schema_blocks=page.get('schema_blocks', 0), canonical=(page.get('canonical') or '')[:2048] or None,
                noindex=bool(page.get('noindex')), language=(page.get('language') or '')[:40] or None,
                internal_links=page.get('internal_links', 0), external_links=page.get('external_links', 0),
                readiness_score=score, metadata_score=page.get('metadata_score'),
                content_score=page.get('content_score'), crawlability_score=page.get('crawlability_score'),
                structured_data_score=page.get('structured_data_score'),
                issues_count=len(page.get('findings') or []), error=page.get('error'),
                fetched_at=page.get('fetched_at') or now,
            ))
            page_id = page_result.inserted_primary_key[0]
            for finding in page.get('findings', []):
                conn.execute(insert(analytics_audit_findings).values(
                    audit_id=audit_id, page_id=page_id, code=finding['code'], area=finding['area'],
                    severity=finding['severity'], evidence=finding['evidence'],
                    recommendation=finding['recommendation'],
                ))

        conn.execute(update(analytics_projects).where(analytics_projects.c.id == project_id).values(updated_at=now))
    return audit_id


def latest_site_audit(project_id):
    with engine.connect() as conn:
        audit = conn.execute(select(analytics_site_audits).where(
            analytics_site_audits.c.project_id == project_id
        ).order_by(desc(analytics_site_audits.c.created_at)).limit(1)).mappings().first()
        if not audit:
            return None
        audit = dict(audit)
        pages = [row_to_dict(row) for row in conn.execute(select(analytics_audit_pages).where(
            analytics_audit_pages.c.audit_id == audit['id']
        ).order_by(desc(analytics_audit_pages.c.readiness_score), analytics_audit_pages.c.url)).mappings().all()]
        findings = [row_to_dict(row) for row in conn.execute(select(analytics_audit_findings).where(
            analytics_audit_findings.c.audit_id == audit['id']
        ).order_by(analytics_audit_findings.c.severity, analytics_audit_findings.c.id)).mappings().all()]
        sitemaps = [row_to_dict(row) for row in conn.execute(select(analytics_sitemaps).where(
            analytics_sitemaps.c.audit_id == audit['id']
        ).order_by(analytics_sitemaps.c.id)).mappings().all()]
        history = [row_to_dict(row) for row in conn.execute(select(
            analytics_site_audits.c.id, analytics_site_audits.c.status,
            analytics_site_audits.c.readiness_score, analytics_site_audits.c.pages_audited,
            analytics_site_audits.c.created_at,
        ).where(analytics_site_audits.c.project_id == project_id)
            .order_by(desc(analytics_site_audits.c.created_at)).limit(12)).mappings().all()]
    history.reverse()
    return {'run': row_to_dict(audit), 'pages': pages, 'findings': findings, 'sitemaps': sitemaps, 'history': history}


def run_site_audit_job(job_id):
    """Claim and execute one durable crawl job. A cron worker can retry queued jobs."""
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
        existing_audit = conn.execute(select(
            analytics_site_audits.c.status, analytics_site_audits.c.pages_audited,
        ).where(analytics_site_audits.c.job_id == job_id)
            .order_by(desc(analytics_site_audits.c.created_at)).limit(1)).mappings().first()
    if not project:
        update_analytics_job(job_id, status='failed_terminal', error='Project no longer exists.', completed_at=datetime.utcnow())
        return
    if existing_audit:
        succeeded = existing_audit['status'] != 'failed'
        update_analytics_job(
            job_id, status='succeeded' if succeeded else 'failed_terminal', progress=100,
            completed_items=existing_audit['pages_audited'], total_items=existing_audit['pages_audited'],
            error=None if succeeded else 'The saved website audit did not complete successfully.',
            completed_at=datetime.utcnow(),
        )
        return

    def progress(completed, total):
        percent = min(99, round(completed / max(total, 1) * 100))
        update_analytics_job(job_id, completed_items=completed, total_items=total, progress=percent)

    try:
        project = dict(project)
        crawl = crawl_website(project.get('website_url') or project['domain'], progress_callback=progress)
        persist_site_audit(project['id'], job_id, crawl)
        update_analytics_job(
            job_id, status='succeeded' if crawl['status'] != 'failed' else 'failed_terminal',
            progress=100, completed_items=len(crawl['pages']), total_items=len(crawl['pages']),
            error=None if crawl['status'] != 'failed' else crawl['summary'], completed_at=datetime.utcnow(),
        )
    except Exception as error:  # A durable status is more useful than a dropped worker traceback.
        update_analytics_job(job_id, status='failed_retryable', error=str(error)[:2000], completed_at=datetime.utcnow())


_analytics_threads = set()
_analytics_threads_lock = threading.Lock()


def start_background_analytics_job(job_id, target):
    """Start low-volume on-demand work; the CLI worker remains the recovery path."""
    def runner():
        try:
            target(job_id)
        except Exception as error:
            update_analytics_job(
                job_id, status='failed_retryable', error=str(error)[:2000],
                completed_at=datetime.utcnow(),
            )
        finally:
            with _analytics_threads_lock:
                _analytics_threads.discard(threading.current_thread())

    worker = threading.Thread(target=runner, name=f'analytics-job-{job_id}', daemon=True)
    with _analytics_threads_lock:
        _analytics_threads.add(worker)
    worker.start()


def is_legacy_mock_analytics_run(run):
    """Hide pre-evidence mock rows and legacy fetch failures from current metrics."""
    summary = run.get('summary') or ''
    return 'benchmarked AI responses' in summary or summary.startswith('Live audit unavailable:')


def project_for_user(project_id, user_id):
    with engine.connect() as conn:
        row = conn.execute(
            select(analytics_projects).where(
                (analytics_projects.c.id == project_id) & (analytics_projects.c.user_id == user_id)
            )
        ).mappings().first()
    return dict(row) if row else None


@app.route('/analytics')
def analytics_page():
    if not session.get('user_id'):
        return redirect('/login')
    return send_from_directory(BASE_DIR, 'analytics.html')


@app.route('/api/analytics/projects', methods=['GET', 'POST'])
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


@app.route('/api/analytics/projects/<int:project_id>', methods=['DELETE'])
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


@app.route('/api/analytics/projects/<int:project_id>/audits', methods=['POST'])
def start_site_audit(project_id):
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    project = project_for_user(project_id, user_id)
    if not project:
        return jsonify({'error': 'Project not found.'}), 404
    with engine.connect() as conn:
        active = conn.execute(select(analytics_audit_jobs).where(
            (analytics_audit_jobs.c.project_id == project_id) &
            (analytics_audit_jobs.c.job_type == 'site_audit') &
            (analytics_audit_jobs.c.status.in_(['queued', 'running']))
        ).order_by(desc(analytics_audit_jobs.c.created_at)).limit(1)).mappings().first()
    if active:
        return jsonify({'status': 'accepted', 'job': row_to_dict(active)}), 202
    job_id = create_analytics_job(project, user_id, 'site_audit')
    start_background_analytics_job(job_id, run_site_audit_job)
    return jsonify({'status': 'accepted', 'job_id': job_id}), 202


@app.route('/api/analytics/jobs/<int:job_id>', methods=['GET'])
def analytics_job_status(job_id):
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    job = analytics_job_for_user(job_id, user_id)
    if not job:
        return jsonify({'error': 'Analytics job not found.'}), 404
    return jsonify({'job': job})


@app.route('/api/analytics/projects/<int:project_id>/audit', methods=['GET'])
def site_audit_report_endpoint(project_id):
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    project = project_for_user(project_id, user_id)
    if not project:
        return jsonify({'error': 'Project not found.'}), 404
    audit = latest_site_audit(project_id)
    with engine.connect() as conn:
        active_job = conn.execute(select(analytics_audit_jobs).where(
            (analytics_audit_jobs.c.project_id == project_id) &
            (analytics_audit_jobs.c.job_type == 'site_audit') &
            (analytics_audit_jobs.c.status.in_(['queued', 'running']))
        ).order_by(desc(analytics_audit_jobs.c.created_at)).limit(1)).mappings().first()
    return jsonify({
        'project': row_to_dict(project), 'audit': audit,
        'active_job': row_to_dict(active_job) if active_job else None,
    })


@app.route('/api/analytics/projects/<int:project_id>/scan', methods=['POST'])
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


def analytics_report(project_id, user_id):
    project = project_for_user(project_id, user_id)
    if not project:
        return None
    site_audit = latest_site_audit(project_id)
    if site_audit:
        audit_run = site_audit['run']
        compatibility_run = {
            'id': audit_run['id'], 'project_id': project_id,
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
    with engine.connect() as conn:
        run = conn.execute(select(analytics_runs).where(analytics_runs.c.project_id == project_id)
            .order_by(desc(analytics_runs.c.created_at)).limit(1)).mappings().first()
        if not run:
            return {'project': row_to_dict(project), 'run': None, 'engines': [], 'prompts': [], 'history': [], 'audit': None}
        run = dict(run)
        if is_legacy_mock_analytics_run(run):
            return {'project': row_to_dict(project), 'run': None, 'engines': [], 'prompts': [], 'history': [], 'audit': None}
        engines = [dict(row) for row in conn.execute(select(analytics_engine_metrics).where(
            analytics_engine_metrics.c.run_id == run['id']).order_by(desc(analytics_engine_metrics.c.visibility_score))).mappings().all()]
        prompts = [dict(row) for row in conn.execute(select(analytics_prompts).where(
            analytics_prompts.c.run_id == run['id']).order_by(analytics_prompts.c.position)).mappings().all()]
        history_rows = conn.execute(select(
            analytics_runs.c.visibility_score, analytics_runs.c.created_at, analytics_runs.c.summary
        ).where(analytics_runs.c.project_id == project_id).order_by(desc(analytics_runs.c.created_at)).limit(12)).mappings().all()
        history = [row_to_dict(row) for row in history_rows if not is_legacy_mock_analytics_run(row)]
    history.reverse()
    return {'project': row_to_dict(project), 'run': row_to_dict(run), 'engines': engines, 'prompts': prompts, 'history': history, 'audit': None}


@app.route('/api/analytics/projects/<int:project_id>/report', methods=['GET'])
def analytics_report_endpoint(project_id):
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    report = analytics_report(project_id, user_id)
    if not report:
        return jsonify({'error': 'Project not found.'}), 404
    return jsonify(report)


class ProviderAPIError(RuntimeError):
    def __init__(self, message, status=None, payload=None):
        super().__init__(message)
        self.status = status
        self.payload = payload


def external_json_request(url, *, method='GET', payload=None, form=None, headers=None, timeout=30):
    """Call a fixed third-party API without ever exposing its credential to the browser."""
    request_headers = {'Accept': 'application/json', **(headers or {})}
    body = None
    if payload is not None:
        request_headers['Content-Type'] = 'application/json'
        body = json.dumps(payload).encode('utf-8')
    elif form is not None:
        request_headers['Content-Type'] = 'application/x-www-form-urlencoded'
        body = urlencode(form).encode('utf-8')
    api_request = Request(url, data=body, headers=request_headers, method=method)
    try:
        with verified_http_opener().open(api_request, timeout=timeout) as response:
            raw = response.read(5_000_001)
            if len(raw) > 5_000_000:
                raise ProviderAPIError('The provider response exceeded the safe size limit.')
            return json.loads(raw.decode('utf-8')) if raw else {}
    except HTTPError as error:
        raw = error.read(200_000).decode('utf-8', errors='replace')
        try:
            error_payload = json.loads(raw)
        except json.JSONDecodeError:
            error_payload = {'message': raw}
        nested_error = error_payload.get('error')
        nested_message = nested_error.get('message') if isinstance(nested_error, dict) else None
        message = error_payload.get('error_description') or nested_message or error_payload.get('message')
        if not message and isinstance(nested_error, str):
            message = nested_error
        message = message or f'Provider returned HTTP {error.code}.'
        raise ProviderAPIError(str(message), status=error.code, payload=error_payload) from error
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        raise ProviderAPIError(f'Provider request failed: {error}') from error


def oauth_token_cipher():
    key = os.environ.get('OAUTH_TOKEN_ENCRYPTION_KEY')
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(key.encode('utf-8'))
    except (ImportError, ValueError) as error:
        raise RuntimeError('OAUTH_TOKEN_ENCRYPTION_KEY must be a valid Fernet key.') from error


def encrypt_oauth_token(token):
    if not token:
        return None
    cipher = oauth_token_cipher()
    if not cipher:
        raise RuntimeError('OAuth token encryption is not configured.')
    return cipher.encrypt(token.encode('utf-8')).decode('utf-8')


def decrypt_oauth_token(token):
    if not token:
        return None
    cipher = oauth_token_cipher()
    if not cipher:
        raise RuntimeError('OAuth token encryption is not configured.')
    try:
        return cipher.decrypt(token.encode('utf-8')).decode('utf-8')
    except Exception as error:
        raise RuntimeError('The stored OAuth token could not be decrypted.') from error


GOOGLE_WEBMASTERS_SCOPE = 'https://www.googleapis.com/auth/webmasters.readonly'


def google_search_console_configured():
    has_settings = bool(
        os.environ.get('GOOGLE_CLIENT_ID') and
        os.environ.get('GOOGLE_CLIENT_SECRET') and
        os.environ.get('OAUTH_TOKEN_ENCRYPTION_KEY')
    )
    if not has_settings:
        return False
    try:
        return oauth_token_cipher() is not None
    except RuntimeError:
        return False


def gsc_connection_for_project(project_id, user_id):
    with engine.connect() as conn:
        row = conn.execute(select(gsc_connections).where(
            (gsc_connections.c.project_id == project_id) & (gsc_connections.c.user_id == user_id)
        )).mappings().first()
    return dict(row) if row else None


def google_redirect_uri():
    return os.environ.get('GOOGLE_OAUTH_REDIRECT_URI') or request.url_root.rstrip('/') + '/api/analytics/integrations/google/callback'


def refresh_google_access_token(connection):
    expires_at = connection.get('token_expires_at')
    if connection.get('encrypted_access_token') and expires_at and expires_at > datetime.utcnow() + timedelta(minutes=5):
        return decrypt_oauth_token(connection['encrypted_access_token'])
    refresh_token = decrypt_oauth_token(connection.get('encrypted_refresh_token'))
    if not refresh_token:
        raise ProviderAPIError('Google authorization has no refresh token. Reconnect Search Console.')
    token_payload = external_json_request(
        'https://oauth2.googleapis.com/token', method='POST', form={
            'client_id': os.environ['GOOGLE_CLIENT_ID'],
            'client_secret': os.environ['GOOGLE_CLIENT_SECRET'],
            'refresh_token': refresh_token,
            'grant_type': 'refresh_token',
        },
    )
    access_token = token_payload.get('access_token')
    if not access_token:
        raise ProviderAPIError('Google did not return an access token.')
    expires = datetime.utcnow() + timedelta(seconds=max(int(token_payload.get('expires_in', 3600)) - 30, 60))
    with engine.begin() as conn:
        conn.execute(update(gsc_connections).where(gsc_connections.c.id == connection['id']).values(
            encrypted_access_token=encrypt_oauth_token(access_token), token_expires_at=expires,
            status='connected', last_error=None, updated_at=datetime.utcnow(),
        ))
    return access_token


def gsc_report(project_id, user_id):
    connection = gsc_connection_for_project(project_id, user_id)
    if not connection:
        return {
            'configured': google_search_console_configured(), 'status': 'disconnected',
            'property': None, 'properties': [], 'last_sync': None, 'metrics': None, 'queries': [],
        }
    with engine.connect() as conn:
        properties = [row_to_dict(row) for row in conn.execute(select(gsc_properties).where(
            gsc_properties.c.connection_id == connection['id']
        ).order_by(desc(gsc_properties.c.selected), gsc_properties.c.site_url)).mappings().all()]
        sync = conn.execute(select(gsc_sync_runs).where(
            gsc_sync_runs.c.connection_id == connection['id']
        ).order_by(desc(gsc_sync_runs.c.created_at)).limit(1)).mappings().first()
        rows = []
        metric_rows = []
        if sync and sync['status'] == 'succeeded':
            rows = [row_to_dict(row) for row in conn.execute(select(gsc_query_rows).where(
                gsc_query_rows.c.sync_run_id == sync['id']
            ).order_by(desc(gsc_query_rows.c.clicks), desc(gsc_query_rows.c.impressions)).limit(100)).mappings().all()]
            metric_rows = conn.execute(select(
                gsc_query_rows.c.clicks, gsc_query_rows.c.impressions, gsc_query_rows.c.position,
            ).where(gsc_query_rows.c.sync_run_id == sync['id'])).mappings().all()
    metrics = None
    if metric_rows:
        clicks = sum(float(row['clicks']) for row in metric_rows)
        impressions = sum(float(row['impressions']) for row in metric_rows)
        weighted_position = sum(float(row['position']) * float(row['impressions']) for row in metric_rows)
        metrics = {
            'clicks': round(clicks, 2), 'impressions': round(impressions, 2),
            'ctr': round(clicks / impressions * 100, 2) if impressions else 0,
            'position': round(weighted_position / impressions, 2) if impressions else None,
            'rows_in_view': len(rows), 'rows_saved': len(metric_rows),
        }
    return {
        'configured': google_search_console_configured(), 'status': connection['status'],
        'property': connection.get('selected_property'), 'properties': properties,
        'last_error': connection.get('last_error'),
        'last_sync': row_to_dict(sync) if sync else None, 'metrics': metrics, 'queries': rows,
    }


@app.route('/api/analytics/integrations/google/start', methods=['GET'])
def start_google_search_console_oauth():
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    try:
        project_id = int(request.args.get('project_id', ''))
    except ValueError:
        return jsonify({'error': 'A valid project_id is required.'}), 400
    if not project_for_user(project_id, user_id):
        return jsonify({'error': 'Project not found.'}), 404
    if not google_search_console_configured():
        return jsonify({'error': 'Google Search Console is not configured on this server.'}), 503
    try:
        oauth_token_cipher()
    except RuntimeError as error:
        return jsonify({'error': str(error)}), 503
    state = secrets.token_urlsafe(32)
    session['gsc_oauth_state'] = state
    session['gsc_oauth_project_id'] = project_id
    params = {
        'client_id': os.environ['GOOGLE_CLIENT_ID'], 'redirect_uri': google_redirect_uri(),
        'response_type': 'code', 'scope': GOOGLE_WEBMASTERS_SCOPE,
        'access_type': 'offline', 'include_granted_scopes': 'true', 'prompt': 'consent',
        'state': state,
    }
    return redirect('https://accounts.google.com/o/oauth2/v2/auth?' + urlencode(params))


@app.route('/api/analytics/integrations/google/callback', methods=['GET'])
def google_search_console_oauth_callback():
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    expected_state = session.pop('gsc_oauth_state', None)
    project_id = session.pop('gsc_oauth_project_id', None)
    if not expected_state or not secrets.compare_digest(request.args.get('state', ''), expected_state):
        return jsonify({'error': 'Google OAuth state validation failed.'}), 400
    project = project_for_user(project_id, user_id)
    if not project:
        return jsonify({'error': 'Project not found.'}), 404
    if request.args.get('error'):
        return redirect(f'/analytics?project={project_id}&gsc=denied')
    code = request.args.get('code')
    if not code:
        return jsonify({'error': 'Google did not return an authorization code.'}), 400
    try:
        token_payload = external_json_request(
            'https://oauth2.googleapis.com/token', method='POST', form={
                'code': code, 'client_id': os.environ['GOOGLE_CLIENT_ID'],
                'client_secret': os.environ['GOOGLE_CLIENT_SECRET'],
                'redirect_uri': google_redirect_uri(), 'grant_type': 'authorization_code',
            },
        )
        access_token = token_payload.get('access_token')
        if not access_token:
            raise ProviderAPIError('Google did not return an access token.')
        site_payload = external_json_request(
            'https://www.googleapis.com/webmasters/v3/sites',
            headers={'Authorization': f'Bearer {access_token}'},
        )
        sites = site_payload.get('siteEntry') or []
        now = datetime.utcnow()
        existing = gsc_connection_for_project(project_id, user_id)
        refresh_token = token_payload.get('refresh_token')
        encrypted_refresh = encrypt_oauth_token(refresh_token) if refresh_token else (existing or {}).get('encrypted_refresh_token')
        if not encrypted_refresh:
            raise ProviderAPIError('Google did not return offline access. Reconnect and approve consent.')
        selected_property = None
        for site in sites:
            site_url = site.get('siteUrl') or ''
            site_domain = normalise_domain(site_url.replace('sc-domain:', ''))
            if site_domain == project['domain']:
                selected_property = site_url
                break
        if not selected_property and len(sites) == 1:
            selected_property = sites[0].get('siteUrl')
        expires = now + timedelta(seconds=max(int(token_payload.get('expires_in', 3600)) - 30, 60))
        with engine.begin() as conn:
            values = dict(
                user_id=user_id, encrypted_refresh_token=encrypted_refresh,
                encrypted_access_token=encrypt_oauth_token(access_token), token_expires_at=expires,
                granted_scopes=token_payload.get('scope') or GOOGLE_WEBMASTERS_SCOPE,
                selected_property=selected_property, status='connected', last_error=None, updated_at=now,
            )
            if existing:
                conn.execute(update(gsc_connections).where(gsc_connections.c.id == existing['id']).values(**values))
                connection_id = existing['id']
                conn.execute(gsc_properties.delete().where(gsc_properties.c.connection_id == connection_id))
            else:
                result = conn.execute(insert(gsc_connections).values(
                    project_id=project_id, created_at=now, **values,
                ))
                connection_id = result.inserted_primary_key[0]
            for site in sites:
                site_url = (site.get('siteUrl') or '')[:2048]
                if site_url:
                    conn.execute(insert(gsc_properties).values(
                        connection_id=connection_id, site_url=site_url,
                        permission_level=(site.get('permissionLevel') or 'unknown')[:80],
                        selected=site_url == selected_property,
                    ))
        return redirect(f'/analytics?project={project_id}&gsc=connected')
    except (ProviderAPIError, RuntimeError) as error:
        return redirect(f'/analytics?project={project_id}&gsc=error&message={quote(str(error)[:180])}')


@app.route('/api/analytics/projects/<int:project_id>/search-console', methods=['GET', 'DELETE'])
def search_console_connection_endpoint(project_id):
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    if not project_for_user(project_id, user_id):
        return jsonify({'error': 'Project not found.'}), 404
    if request.method == 'DELETE':
        connection = gsc_connection_for_project(project_id, user_id)
        if connection:
            with engine.begin() as conn:
                sync_ids = [row[0] for row in conn.execute(select(gsc_sync_runs.c.id).where(
                    gsc_sync_runs.c.connection_id == connection['id']
                )).all()]
                if sync_ids:
                    conn.execute(gsc_query_rows.delete().where(gsc_query_rows.c.sync_run_id.in_(sync_ids)))
                conn.execute(gsc_sync_runs.delete().where(gsc_sync_runs.c.connection_id == connection['id']))
                conn.execute(gsc_properties.delete().where(gsc_properties.c.connection_id == connection['id']))
                conn.execute(gsc_connections.delete().where(gsc_connections.c.id == connection['id']))
        return jsonify({'status': 'disconnected'})
    return jsonify({'search_console': gsc_report(project_id, user_id)})


@app.route('/api/analytics/projects/<int:project_id>/search-console/property', methods=['PUT'])
def select_search_console_property(project_id):
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    connection = gsc_connection_for_project(project_id, user_id)
    if not connection:
        return jsonify({'error': 'Connect Google Search Console first.'}), 409
    site_url = ((request.get_json(silent=True) or {}).get('site_url') or '').strip()
    with engine.connect() as conn:
        allowed = conn.execute(select(gsc_properties.c.id).where(
            (gsc_properties.c.connection_id == connection['id']) & (gsc_properties.c.site_url == site_url)
        )).scalar_one_or_none()
    if not allowed:
        return jsonify({'error': 'Select a property returned by Google Search Console.'}), 400
    with engine.begin() as conn:
        conn.execute(update(gsc_properties).where(gsc_properties.c.connection_id == connection['id']).values(selected=False))
        conn.execute(update(gsc_properties).where(gsc_properties.c.id == allowed).values(selected=True))
        conn.execute(update(gsc_connections).where(gsc_connections.c.id == connection['id']).values(
            selected_property=site_url, updated_at=datetime.utcnow(),
        ))
    return jsonify({'search_console': gsc_report(project_id, user_id)})


@app.route('/api/analytics/projects/<int:project_id>/search-console/sync', methods=['POST'])
def sync_search_console(project_id):
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    connection = gsc_connection_for_project(project_id, user_id)
    if not connection:
        return jsonify({'error': 'Connect Google Search Console first.'}), 409
    property_url = connection.get('selected_property')
    if not property_url:
        return jsonify({'error': 'Choose a Search Console property first.'}), 409
    body = request.get_json(silent=True) or {}
    end_day = date.today() - timedelta(days=3)
    start_day = end_day - timedelta(days=27)
    try:
        requested_start = date.fromisoformat(body.get('start_date')) if body.get('start_date') else start_day
        requested_end = date.fromisoformat(body.get('end_date')) if body.get('end_date') else end_day
    except ValueError:
        return jsonify({'error': 'Search Console dates must use YYYY-MM-DD.'}), 400
    if requested_end < requested_start or (requested_end - requested_start).days > 365:
        return jsonify({'error': 'Choose a valid date range of at most 366 days.'}), 400
    now = datetime.utcnow()
    with engine.begin() as conn:
        result = conn.execute(insert(gsc_sync_runs).values(
            project_id=project_id, connection_id=connection['id'], property_url=property_url,
            status='running', start_date=requested_start.isoformat(), end_date=requested_end.isoformat(),
            rows_saved=0, data_state='final', error=None, created_at=now, completed_at=None,
        ))
        sync_id = result.inserted_primary_key[0]
    try:
        access_token = refresh_google_access_token(connection)
        row_limit = max(1, min(int(os.environ.get('GSC_ROW_LIMIT', '2500')), 25_000))
        payload = external_json_request(
            f"https://www.googleapis.com/webmasters/v3/sites/{quote(property_url, safe='')}/searchAnalytics/query",
            method='POST', headers={'Authorization': f'Bearer {access_token}'}, payload={
                'startDate': requested_start.isoformat(), 'endDate': requested_end.isoformat(),
                'dimensions': ['query', 'page'], 'type': 'web', 'aggregationType': 'auto',
                'rowLimit': row_limit, 'startRow': 0, 'dataState': 'final',
            }, timeout=45,
        )
        query_rows = []
        for row in payload.get('rows') or []:
            keys = row.get('keys') or []
            query_rows.append({
                'sync_run_id': sync_id, 'query': (keys[0] if keys else '(unknown)')[:2000],
                'page': (keys[1] if len(keys) > 1 else None),
                'clicks': float(row.get('clicks', 0)), 'impressions': float(row.get('impressions', 0)),
                'ctr': float(row.get('ctr', 0)), 'position': float(row.get('position', 0)),
            })
        with engine.begin() as conn:
            if query_rows:
                conn.execute(insert(gsc_query_rows), query_rows)
            conn.execute(update(gsc_sync_runs).where(gsc_sync_runs.c.id == sync_id).values(
                status='succeeded', rows_saved=len(query_rows), completed_at=datetime.utcnow(),
            ))
            conn.execute(update(gsc_connections).where(gsc_connections.c.id == connection['id']).values(
                status='connected', last_error=None, updated_at=datetime.utcnow(),
            ))
        return jsonify({'status': 'success', 'search_console': gsc_report(project_id, user_id)})
    except (ProviderAPIError, RuntimeError) as error:
        with engine.begin() as conn:
            conn.execute(update(gsc_sync_runs).where(gsc_sync_runs.c.id == sync_id).values(
                status='failed', error=str(error)[:2000], completed_at=datetime.utcnow(),
            ))
            conn.execute(update(gsc_connections).where(gsc_connections.c.id == connection['id']).values(
                status='error', last_error=str(error)[:2000], updated_at=datetime.utcnow(),
            ))
        return jsonify({'error': str(error), 'search_console': gsc_report(project_id, user_id)}), 502


def analytics_tracking_payload(project_id):
    with engine.connect() as conn:
        topics = [row_to_dict(row) for row in conn.execute(select(analytics_topics).where(
            analytics_topics.c.project_id == project_id
        ).order_by(analytics_topics.c.name)).mappings().all()]
        competitors = [row_to_dict(row) for row in conn.execute(select(analytics_competitors).where(
            analytics_competitors.c.project_id == project_id
        ).order_by(analytics_competitors.c.name)).mappings().all()]
        prompt_rows = conn.execute(select(
            analytics_tracked_prompts,
            analytics_topics.c.name.label('topic_name'),
        ).outerjoin(
            analytics_topics, analytics_tracked_prompts.c.topic_id == analytics_topics.c.id
        ).where(analytics_tracked_prompts.c.project_id == project_id)
            .order_by(desc(analytics_tracked_prompts.c.active), analytics_tracked_prompts.c.created_at)).mappings().all()
        prompts = [row_to_dict(row) for row in prompt_rows]
        schedule = conn.execute(select(analytics_scan_schedules).where(
            analytics_scan_schedules.c.project_id == project_id
        )).mappings().first()
    open_model = open_model_settings()
    return {
        'topics': topics, 'competitors': competitors, 'prompts': prompts,
        'schedule': row_to_dict(schedule) if schedule else None,
        'providers': {
            'perplexity': {
                'configured': bool(os.environ.get('PERPLEXITY_API_KEY')),
                'model': f"Agent preset: {os.environ.get('PERPLEXITY_AGENT_PRESET', 'low')}",
            },
            'open_model': {
                'configured': open_model['configured'],
                'provider': open_model['provider'],
                'model': open_model['model'],
                'purpose': 'Evidence-grounded opportunity summaries only',
            },
        },
    }


def ensure_project_owner(project_id):
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return None, None, auth_error
    project = project_for_user(project_id, user_id)
    if not project:
        return user_id, None, (jsonify({'error': 'Project not found.'}), 404)
    return user_id, project, None


@app.route('/api/analytics/projects/<int:project_id>/tracking', methods=['GET'])
def analytics_tracking_endpoint(project_id):
    _user_id, project, error = ensure_project_owner(project_id)
    if error:
        return error
    return jsonify({'project': row_to_dict(project), 'tracking': analytics_tracking_payload(project_id)})


@app.route('/api/analytics/projects/<int:project_id>/topics', methods=['POST'])
def create_analytics_topic(project_id):
    _user_id, _project, error = ensure_project_owner(project_id)
    if error:
        return error
    name = ((request.get_json(silent=True) or {}).get('name') or '').strip()
    if not name or len(name) > 180:
        return jsonify({'error': 'Enter a topic between 1 and 180 characters.'}), 400
    try:
        with engine.begin() as conn:
            result = conn.execute(insert(analytics_topics).values(
                project_id=project_id, name=name, created_at=datetime.utcnow(),
            ))
    except IntegrityError:
        return jsonify({'error': 'That topic is already tracked.'}), 409
    with engine.connect() as conn:
        row = conn.execute(select(analytics_topics).where(analytics_topics.c.id == result.inserted_primary_key[0])).mappings().first()
    return jsonify({'topic': row_to_dict(row)}), 201


@app.route('/api/analytics/projects/<int:project_id>/topics/<int:topic_id>', methods=['DELETE'])
def delete_analytics_topic(project_id, topic_id):
    _user_id, _project, error = ensure_project_owner(project_id)
    if error:
        return error
    with engine.begin() as conn:
        exists = conn.execute(select(analytics_topics.c.id).where(
            (analytics_topics.c.id == topic_id) & (analytics_topics.c.project_id == project_id)
        )).scalar_one_or_none()
        if not exists:
            return jsonify({'error': 'Topic not found.'}), 404
        conn.execute(update(analytics_tracked_prompts).where(
            analytics_tracked_prompts.c.topic_id == topic_id
        ).values(topic_id=None, updated_at=datetime.utcnow()))
        conn.execute(analytics_topics.delete().where(analytics_topics.c.id == topic_id))
    return jsonify({'status': 'success'})


@app.route('/api/analytics/projects/<int:project_id>/competitors', methods=['POST'])
def create_analytics_competitor(project_id):
    _user_id, _project, error = ensure_project_owner(project_id)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    domain_value = (data.get('domain') or '').strip()
    domain = normalise_domain(domain_value) if domain_value else None
    if not name or len(name) > 180:
        return jsonify({'error': 'Enter a competitor name between 1 and 180 characters.'}), 400
    if domain_value and not domain:
        return jsonify({'error': 'Enter a valid competitor domain or leave it blank.'}), 400
    try:
        with engine.begin() as conn:
            result = conn.execute(insert(analytics_competitors).values(
                project_id=project_id, name=name, domain=domain, created_at=datetime.utcnow(),
            ))
    except IntegrityError:
        return jsonify({'error': 'That competitor is already tracked.'}), 409
    with engine.connect() as conn:
        row = conn.execute(select(analytics_competitors).where(
            analytics_competitors.c.id == result.inserted_primary_key[0]
        )).mappings().first()
    return jsonify({'competitor': row_to_dict(row)}), 201


@app.route('/api/analytics/projects/<int:project_id>/competitors/<int:competitor_id>', methods=['DELETE'])
def delete_analytics_competitor(project_id, competitor_id):
    _user_id, _project, error = ensure_project_owner(project_id)
    if error:
        return error
    with engine.begin() as conn:
        result = conn.execute(analytics_competitors.delete().where(
            (analytics_competitors.c.id == competitor_id) &
            (analytics_competitors.c.project_id == project_id)
        ))
    if not result.rowcount:
        return jsonify({'error': 'Competitor not found.'}), 404
    return jsonify({'status': 'success'})


@app.route('/api/analytics/projects/<int:project_id>/tracked-prompts', methods=['POST'])
def create_analytics_tracked_prompt(project_id):
    _user_id, _project, error = ensure_project_owner(project_id)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    prompt = (data.get('prompt') or '').strip()
    intent = (data.get('intent') or 'Discovery').strip()[:80] or 'Discovery'
    try:
        topic_id = int(data['topic_id']) if data.get('topic_id') else None
    except (TypeError, ValueError):
        return jsonify({'error': 'Choose a valid topic.'}), 400
    if len(prompt) < 8 or len(prompt) > 1000:
        return jsonify({'error': 'Enter a prompt between 8 and 1,000 characters.'}), 400
    if topic_id:
        with engine.connect() as conn:
            topic = conn.execute(select(analytics_topics.c.id).where(
                (analytics_topics.c.id == topic_id) & (analytics_topics.c.project_id == project_id)
            )).scalar_one_or_none()
        if not topic:
            return jsonify({'error': 'The selected topic does not belong to this project.'}), 400
    now = datetime.utcnow()
    with engine.begin() as conn:
        prompt_count = conn.execute(select(func.count()).select_from(analytics_tracked_prompts).where(
            analytics_tracked_prompts.c.project_id == project_id
        )).scalar_one()
        if prompt_count >= ANALYTICS_MAX_TRACKED_PROMPTS:
            return jsonify({'error': f'This project has reached its {ANALYTICS_MAX_TRACKED_PROMPTS}-prompt storage limit.'}), 409
        result = conn.execute(insert(analytics_tracked_prompts).values(
            project_id=project_id, topic_id=topic_id, prompt=prompt, intent=intent,
            active=True, created_at=now, updated_at=now,
        ))
    return jsonify({'prompt_id': result.inserted_primary_key[0]}), 201


@app.route('/api/analytics/projects/<int:project_id>/tracked-prompts/<int:prompt_id>', methods=['PATCH', 'DELETE'])
def update_analytics_tracked_prompt(project_id, prompt_id):
    _user_id, _project, error = ensure_project_owner(project_id)
    if error:
        return error
    with engine.connect() as conn:
        prompt = conn.execute(select(analytics_tracked_prompts).where(
            (analytics_tracked_prompts.c.id == prompt_id) &
            (analytics_tracked_prompts.c.project_id == project_id)
        )).mappings().first()
    if not prompt:
        return jsonify({'error': 'Prompt not found.'}), 404
    if request.method == 'DELETE':
        with engine.begin() as conn:
            conn.execute(analytics_tracked_prompts.delete().where(analytics_tracked_prompts.c.id == prompt_id))
        return jsonify({'status': 'success'})
    data = request.get_json(silent=True) or {}
    values = {'updated_at': datetime.utcnow()}
    if 'active' in data:
        values['active'] = bool(data['active'])
    if 'prompt' in data:
        prompt_text = (data.get('prompt') or '').strip()
        if len(prompt_text) < 8 or len(prompt_text) > 1000:
            return jsonify({'error': 'Enter a prompt between 8 and 1,000 characters.'}), 400
        values['prompt'] = prompt_text
    with engine.begin() as conn:
        conn.execute(update(analytics_tracked_prompts).where(analytics_tracked_prompts.c.id == prompt_id).values(**values))
    return jsonify({'status': 'success'})


def next_schedule_time(frequency, from_time=None):
    from_time = from_time or datetime.utcnow()
    return from_time + {'daily': timedelta(days=1), 'weekly': timedelta(days=7), 'monthly': timedelta(days=30)}[frequency]


@app.route('/api/analytics/projects/<int:project_id>/scan-schedule', methods=['PUT'])
def update_analytics_scan_schedule(project_id):
    _user_id, _project, error = ensure_project_owner(project_id)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get('enabled'))
    frequency = (data.get('frequency') or 'weekly').lower()
    region = (data.get('region') or '').strip().upper()[:2] or None
    if frequency not in {'daily', 'weekly', 'monthly'}:
        return jsonify({'error': 'Frequency must be daily, weekly, or monthly.'}), 400
    if region and not re.fullmatch(r'[A-Z]{2}', region):
        return jsonify({'error': 'Region must be a two-letter country code.'}), 400
    now = datetime.utcnow()
    with engine.begin() as conn:
        existing = conn.execute(select(analytics_scan_schedules.c.id).where(
            analytics_scan_schedules.c.project_id == project_id
        )).scalar_one_or_none()
        values = dict(
            enabled=enabled, frequency=frequency, region=region,
            next_run_at=next_schedule_time(frequency, now) if enabled else None,
            updated_at=now,
        )
        if existing:
            conn.execute(update(analytics_scan_schedules).where(analytics_scan_schedules.c.id == existing).values(**values))
        else:
            conn.execute(insert(analytics_scan_schedules).values(
                project_id=project_id, last_run_at=None, created_at=now, **values,
            ))
    return jsonify({'tracking': analytics_tracking_payload(project_id)})


def evidence_url(value):
    value = (value or '').strip()
    parsed = urlparse(value)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        return None
    return parsed._replace(fragment='').geturl()[:2048]


def domain_matches(candidate_url, tracked_domain):
    candidate_domain = normalise_domain(candidate_url)
    tracked = normalise_site_host(tracked_domain)
    return bool(candidate_domain and (
        normalise_site_host(candidate_domain) == tracked or
        normalise_site_host(candidate_domain).endswith('.' + tracked)
    ))


def text_mentions_alias(text_value, aliases):
    text_value = text_value or ''
    for alias in aliases:
        alias = (alias or '').strip()
        if len(alias) < 2:
            continue
        if re.search(rf'(?<![\w]){re.escape(alias)}(?![\w])', text_value, flags=re.I):
            return True
    return False


def project_brand_aliases(project):
    domain_label = project['domain'].split('.')[0].replace('-', ' ')
    return list(dict.fromkeys([project['brand_name'], project['domain'], domain_label]))


def call_perplexity_search(prompt, region=None):
    api_key = os.environ.get('PERPLEXITY_API_KEY')
    if not api_key:
        raise ProviderAPIError('PERPLEXITY_API_KEY is not configured.')
    max_results = max(1, min(int(os.environ.get('PERPLEXITY_MAX_RESULTS', '10')), 20))
    payload = {
        'query': prompt, 'max_results': max_results,
        'search_context_size': os.environ.get('PERPLEXITY_SEARCH_CONTEXT', 'medium'),
    }
    if region and re.fullmatch(r'[A-Z]{2}', region):
        payload['country'] = region
    return external_json_request(
        'https://api.perplexity.ai/search', method='POST', payload=payload,
        headers={'Authorization': f'Bearer {api_key}'}, timeout=45,
    )


def call_perplexity_answer(prompt):
    api_key = os.environ.get('PERPLEXITY_API_KEY')
    if not api_key:
        raise ProviderAPIError('PERPLEXITY_API_KEY is not configured.')
    payload = external_json_request(
        'https://api.perplexity.ai/v1/agent', method='POST',
        headers={'Authorization': f'Bearer {api_key}'}, timeout=60,
        payload={
            'preset': os.environ.get('PERPLEXITY_AGENT_PRESET', 'low'),
            'input': prompt,
            'tools': [{'type': 'web_search'}],
            'max_output_tokens': max(256, min(int(os.environ.get('PERPLEXITY_AGENT_MAX_OUTPUT_TOKENS', '1200')), 4000)),
            'instructions': (
                'Answer the user directly using current web evidence. Use the web search tool, '
                'preserve source annotations, and do not invent citations.'
            ),
        },
    )
    status = payload.get('status')
    if status and status != 'completed':
        provider_error = payload.get('error') or {}
        message = provider_error.get('message') if isinstance(provider_error, dict) else provider_error
        raise ProviderAPIError(message or f'Perplexity Agent response ended with status {status}.', payload=payload)
    return payload


def perplexity_answer_text(payload):
    output_text = payload.get('output_text')
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
    agent_parts = []
    for output_item in payload.get('output') or []:
        if not isinstance(output_item, dict) or output_item.get('type') != 'message':
            continue
        for content in output_item.get('content') or []:
            if isinstance(content, dict) and content.get('type') == 'output_text' and isinstance(content.get('text'), str):
                agent_parts.append(content['text'])
    if agent_parts:
        return '\n'.join(agent_parts).strip()
    choices = payload.get('choices') or []
    if not choices:
        return ''
    content = (choices[0].get('message') or {}).get('content')
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(item.get('text', '') for item in content if isinstance(item, dict))
    return ''


def perplexity_answer_citations(payload):
    """Return URL-bearing citations from current Agent and legacy Sonar payloads."""
    citations = []
    for item in payload.get('citations') or []:
        citations.append(item)
    for output_item in payload.get('output') or []:
        if not isinstance(output_item, dict) or output_item.get('type') != 'message':
            continue
        for content in output_item.get('content') or []:
            if not isinstance(content, dict):
                continue
            for annotation in content.get('annotations') or []:
                if isinstance(annotation, dict) and annotation.get('url'):
                    citations.append(annotation)
    return citations


def normalise_perplexity_sources(search_payload, answer_payload):
    sources = []
    seen = set()

    def add_source(item, source_kind, rank):
        if isinstance(item, str):
            item = {'url': item}
        if not isinstance(item, dict):
            return
        url = evidence_url(item.get('url'))
        evidence_key = (source_kind, url)
        if not url or evidence_key in seen:
            return
        seen.add(evidence_key)
        sources.append({
            'rank': rank, 'source_kind': source_kind,
            'title': (item.get('title') or '')[:2000] or None,
            'url': url, 'domain': normalise_domain(url),
            'snippet': (item.get('snippet') or '')[:8000] or None,
            'published_at': str(item.get('date') or item.get('last_updated') or '')[:80] or None,
        })

    for rank, item in enumerate((search_payload or {}).get('results') or [], 1):
        add_source(item, 'search_result', rank)
    answer_results = (answer_payload or {}).get('search_results') or []
    for rank, item in enumerate(answer_results, 1):
        add_source(item, 'answer_source', rank)
    for output_item in (answer_payload or {}).get('output') or []:
        if not isinstance(output_item, dict):
            continue
        if output_item.get('type') == 'search_results':
            for rank, item in enumerate(output_item.get('results') or [], 1):
                add_source(item, 'agent_search_result', rank)
        elif output_item.get('type') == 'fetch_url_results':
            for rank, item in enumerate(output_item.get('contents') or [], 1):
                add_source(item, 'agent_fetched_source', rank)
    for rank, item in enumerate(perplexity_answer_citations(answer_payload or {}), 1):
        add_source(item, 'answer_citation', rank)
    return sources


def parse_json_from_model(text_value):
    text_value = (text_value or '').strip()
    text_value = re.sub(r'^```(?:json)?\s*', '', text_value, flags=re.I)
    text_value = re.sub(r'\s*```$', '', text_value)
    try:
        return json.loads(text_value)
    except json.JSONDecodeError:
        match = re.search(r'(\{.*\}|\[.*\])', text_value, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(1))


def rule_based_opportunities(project, evidence_rows):
    opportunities = []
    missing_mentions = [row for row in evidence_rows if row.get('answer_text') and not row.get('brand_mentioned')]
    missing_citations = [row for row in evidence_rows if row.get('answer_text') and not row.get('brand_cited')]
    missing_sources = [row for row in evidence_rows if row.get('source_present') is False]
    if missing_mentions:
        sample = missing_mentions[0]
        opportunities.append({
            'title': 'Build a direct answer for an unmentioned prompt',
            'rationale': f"{project['brand_name']} was absent from the stored answer to: {sample['prompt']}",
            'evidence_refs': f"answer:{sample['id']}", 'priority': 'high',
        })
    if missing_citations:
        sample = missing_citations[0]
        opportunities.append({
            'title': 'Publish sourceable proof for an uncited topic',
            'rationale': f"The provider answer did not cite {project['domain']} for: {sample['prompt']}",
            'evidence_refs': f"answer:{sample['id']}", 'priority': 'high',
        })
    if missing_sources:
        sample = missing_sources[0]
        opportunities.append({
            'title': 'Close a ranked-source coverage gap',
            'rationale': f"The tracked domain did not appear in the saved Perplexity Search results for: {sample['prompt']}",
            'evidence_refs': f"answer:{sample['id']}", 'priority': 'medium',
        })
    if not opportunities and evidence_rows:
        sample = evidence_rows[0]
        opportunities.append({
            'title': 'Protect and deepen measured coverage',
            'rationale': 'Current evidence contains brand or source coverage. Add fresh first-party proof and monitor the same approved prompt set over time.',
            'evidence_refs': f"answer:{sample['id']}", 'priority': 'medium',
        })
    return opportunities[:5]


def open_model_settings():
    if os.environ.get('HF_TOKEN'):
        return {
            'configured': True,
            'provider': 'Hugging Face Inference Providers',
            'base_url': os.environ.get('HF_BASE_URL', 'https://router.huggingface.co/v1'),
            'model': os.environ.get('HF_MODEL', 'openai/gpt-oss-120b:preferred'),
            'headers': {'Authorization': f"Bearer {os.environ['HF_TOKEN']}"},
        }
    if os.environ.get('OLLAMA_BASE_URL'):
        return {
            'configured': True,
            'provider': 'Ollama',
            'base_url': os.environ['OLLAMA_BASE_URL'],
            'model': os.environ.get('OLLAMA_MODEL', 'gpt-oss:20b'),
            'headers': {},
        }
    return {
        'configured': False,
        'provider': 'Hugging Face Inference Providers or Ollama',
        'base_url': os.environ.get('HF_BASE_URL', 'https://router.huggingface.co/v1'),
        'model': os.environ.get('HF_MODEL', 'openai/gpt-oss-120b:preferred'),
        'headers': {},
    }


def open_model_evidence_opportunities(project, evidence_rows):
    """Use an open-weight model only to summarize stored evidence, never to invent metrics."""
    settings = open_model_settings()
    if not settings['configured'] or not evidence_rows:
        return None
    model = settings['model']
    evidence = [
        {
            'evidence_id': f"answer:{row['id']}", 'prompt': row['prompt'],
            'brand_mentioned': row.get('brand_mentioned'), 'brand_cited': row.get('brand_cited'),
            'source_present': row.get('source_present'), 'best_source_rank': row.get('best_source_rank'),
            'answer_excerpt': (row.get('answer_text') or '')[:700],
        }
        for row in evidence_rows[:20]
    ]
    payload = external_json_request(
        settings['base_url'].rstrip('/') + '/chat/completions',
        method='POST', headers=settings['headers'], timeout=90,
        payload={
            'model': model, 'temperature': 0.1, 'max_tokens': 900,
            'messages': [
                {
                    'role': 'system',
                    'content': (
                        'You are an AEO analyst. Use only the supplied evidence. Do not create or recalculate statistics. '
                        'Return JSON with an opportunities array. Each item must contain title, rationale, evidence_refs, and priority. '
                        'evidence_refs must cite one or more supplied evidence_id values. Return at most five items.'
                    ),
                },
                {
                    'role': 'user',
                    'content': json.dumps({'brand': project['brand_name'], 'domain': project['domain'], 'evidence': evidence}),
                },
            ],
        },
    )
    choices = payload.get('choices') or []
    if not choices:
        raise ProviderAPIError('The open model returned no recommendation content.')
    content = (choices[0].get('message') or {}).get('content') or ''
    parsed = parse_json_from_model(content)
    items = parsed.get('opportunities') if isinstance(parsed, dict) else parsed
    if not isinstance(items, list):
        raise ProviderAPIError('The open model returned an invalid opportunity format.')
    allowed_refs = {f"answer:{row['id']}" for row in evidence_rows}
    normalized = []
    for item in items[:5]:
        if not isinstance(item, dict):
            continue
        refs = item.get('evidence_refs') or ''
        if isinstance(refs, list):
            refs = ','.join(str(value) for value in refs)
        cited_refs = [ref for ref in re.findall(r'answer:\d+', str(refs)) if ref in allowed_refs]
        if not cited_refs:
            continue
        title = str(item.get('title') or '').strip()
        rationale = str(item.get('rationale') or '').strip()
        if title and rationale:
            normalized.append({
                'title': title[:255], 'rationale': rationale[:6000],
                'evidence_refs': ','.join(dict.fromkeys(cited_refs)),
                'priority': str(item.get('priority') or 'medium').lower() if str(item.get('priority') or '').lower() in {'high', 'medium', 'low'} else 'medium',
            })
    return normalized or None


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


def latest_prompt_evidence(project_id, run_id=None):
    with engine.connect() as conn:
        statement = select(analytics_prompt_scan_runs).where(
            analytics_prompt_scan_runs.c.project_id == project_id
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
            analytics_prompt_scan_runs.c.mention_rate, analytics_prompt_scan_runs.c.citation_rate,
            analytics_prompt_scan_runs.c.source_presence_rate, analytics_prompt_scan_runs.c.share_of_voice,
            analytics_prompt_scan_runs.c.created_at,
        ).where(analytics_prompt_scan_runs.c.project_id == project_id)
            .order_by(desc(analytics_prompt_scan_runs.c.created_at)).limit(12)).mappings().all()]
    for answer in answer_rows:
        answer['sources'] = sources_by_answer.get(answer['id'], [])
        answer.pop('raw_response', None)
    history.reverse()
    return {'run': row_to_dict(scan), 'answers': answer_rows, 'opportunities': opportunities, 'history': history}


@app.route('/api/analytics/projects/<int:project_id>/prompt-scans', methods=['POST'])
def start_analytics_prompt_scan(project_id):
    user_id, project, error = ensure_project_owner(project_id)
    if error:
        return error
    if not os.environ.get('PERPLEXITY_API_KEY'):
        return jsonify({'error': 'Perplexity is not configured. Add PERPLEXITY_API_KEY on the server.'}), 503
    with engine.connect() as conn:
        prompt_count = conn.execute(select(func.count()).select_from(analytics_tracked_prompts).where(
            (analytics_tracked_prompts.c.project_id == project_id) &
            (analytics_tracked_prompts.c.active.is_(True))
        )).scalar_one()
        active = conn.execute(select(analytics_audit_jobs).where(
            (analytics_audit_jobs.c.project_id == project_id) &
            (analytics_audit_jobs.c.job_type == 'prompt_scan') &
            (analytics_audit_jobs.c.status.in_(['queued', 'running']))
        ).order_by(desc(analytics_audit_jobs.c.created_at)).limit(1)).mappings().first()
    if not prompt_count:
        return jsonify({'error': 'Add at least one active tracked prompt first.'}), 409
    if prompt_count > PERPLEXITY_MAX_PROMPTS_PER_SCAN:
        return jsonify({'error': f'Pause prompts until no more than {PERPLEXITY_MAX_PROMPTS_PER_SCAN} are active for one provider scan.'}), 409
    if active:
        return jsonify({'status': 'accepted', 'job': row_to_dict(active)}), 202
    job_id = create_analytics_job(project, user_id, 'prompt_scan', provider='Perplexity')
    start_background_analytics_job(job_id, run_prompt_scan_job)
    return jsonify({'status': 'accepted', 'job_id': job_id}), 202


@app.route('/api/analytics/projects/<int:project_id>/evidence', methods=['GET'])
def analytics_evidence_endpoint(project_id):
    _user_id, project, error = ensure_project_owner(project_id)
    if error:
        return error
    try:
        run_id = int(request.args['run_id']) if request.args.get('run_id') else None
    except ValueError:
        return jsonify({'error': 'run_id must be an integer.'}), 400
    evidence = latest_prompt_evidence(project_id, run_id)
    with engine.connect() as conn:
        active_job = conn.execute(select(analytics_audit_jobs).where(
            (analytics_audit_jobs.c.project_id == project_id) &
            (analytics_audit_jobs.c.job_type == 'prompt_scan') &
            (analytics_audit_jobs.c.status.in_(['queued', 'running']))
        ).order_by(desc(analytics_audit_jobs.c.created_at)).limit(1)).mappings().first()
    return jsonify({
        'project': row_to_dict(project), 'evidence': evidence,
        'active_job': row_to_dict(active_job) if active_job else None,
    })


@app.route('/api/analytics/projects/<int:project_id>/evidence/<int:answer_id>', methods=['GET'])
def analytics_evidence_detail_endpoint(project_id, answer_id):
    _user_id, _project, error = ensure_project_owner(project_id)
    if error:
        return error
    with engine.connect() as conn:
        answer = conn.execute(select(
            analytics_provider_answers,
            func.coalesce(analytics_provider_answers.c.prompt_text, analytics_tracked_prompts.c.prompt).label('resolved_prompt'),
            func.coalesce(analytics_provider_answers.c.prompt_intent, analytics_tracked_prompts.c.intent).label('resolved_intent'),
            func.coalesce(analytics_provider_answers.c.topic_name, analytics_topics.c.name).label('resolved_topic_name'),
        ).join(
            analytics_prompt_scan_runs, analytics_provider_answers.c.scan_run_id == analytics_prompt_scan_runs.c.id
        ).outerjoin(
            analytics_tracked_prompts, analytics_provider_answers.c.prompt_id == analytics_tracked_prompts.c.id
        ).outerjoin(
            analytics_topics, analytics_tracked_prompts.c.topic_id == analytics_topics.c.id
        ).where(
            (analytics_provider_answers.c.id == answer_id) &
            (analytics_prompt_scan_runs.c.project_id == project_id)
        )).mappings().first()
        if not answer:
            return jsonify({'error': 'Evidence record not found.'}), 404
        sources = [row_to_dict(row) for row in conn.execute(select(analytics_answer_sources).where(
            analytics_answer_sources.c.answer_id == answer_id
        ).order_by(analytics_answer_sources.c.source_kind, analytics_answer_sources.c.rank)).mappings().all()]
    result = row_to_dict(answer)
    result['prompt'] = result.pop('resolved_prompt')
    result['intent'] = result.pop('resolved_intent')
    result['topic_name'] = result.pop('resolved_topic_name')
    try:
        result['raw_response'] = json.loads(result.get('raw_response') or '{}')
    except json.JSONDecodeError:
        result['raw_response'] = {'unparsed': result.get('raw_response')}
    result['sources'] = sources
    return jsonify({'evidence': result})


def prompt_collection_for_user(collection_id, user_id):
    with engine.connect() as conn:
        row = conn.execute(select(prompt_collections).where(
            (prompt_collections.c.id == collection_id) & (prompt_collections.c.user_id == user_id)
        )).mappings().first()
    return dict(row) if row else None


def prompt_query_for_user(query_id, user_id):
    with engine.connect() as conn:
        row = conn.execute(select(prompt_queries, prompt_collections.c.brand_name).join(
            prompt_collections, prompt_queries.c.collection_id == prompt_collections.c.id
        ).where(
            (prompt_queries.c.id == query_id) & (prompt_collections.c.user_id == user_id)
        )).mappings().first()
    return dict(row) if row else None


def make_prompt_result(query, brand_name, iteration):
    """Produce a deterministic prompt benchmark until live engine connectors exist."""
    seed = int(hashlib.sha256(
        f"{query['prompt']}:{query['engine']}:{brand_name}:{iteration}".encode()
    ).hexdigest()[:8], 16)
    score = 39 + seed % 53
    position = 1 + (seed >> 7) % 7
    cited = 'Yes' if score >= 63 else 'No'
    competitors = ['Capterra', 'G2', 'HubSpot', 'Semrush', 'Ahrefs', 'Industry publication']
    leader = brand_name if position == 1 else competitors[(seed >> 13) % len(competitors)]
    summaries = [
        f'{query["engine"]} frames the answer around practical selection criteria and established alternatives.',
        f'The answer prioritizes proof, use cases, and clear comparisons before mentioning solutions.',
        f'The response favours authoritative how-to content and brands with specific evidence.',
    ]
    actions = [
        'Add a direct answer section, first-party proof, and an FAQ that mirrors this question.',
        'Publish a comparison page that explains relevant use cases, tradeoffs, and measurable outcomes.',
        'Strengthen supporting content with expert attribution, examples, and concise sourceable facts.',
    ]
    return {
        'visibility_score': score,
        'brand_position': position,
        'cited': cited,
        'leading_brand': leader,
        'answer_summary': summaries[(seed >> 18) % len(summaries)],
        'recommendation': actions[(seed >> 22) % len(actions)],
    }


def prompt_collection_report(collection_id, user_id):
    collection = prompt_collection_for_user(collection_id, user_id)
    if not collection:
        return None
    with engine.connect() as conn:
        queries = [dict(row) for row in conn.execute(select(prompt_queries).where(
            prompt_queries.c.collection_id == collection_id
        ).order_by(desc(prompt_queries.c.updated_at))).mappings().all()]
        rows = []
        for query in queries:
            result = conn.execute(select(prompt_query_results).where(
                prompt_query_results.c.query_id == query['id']
            ).order_by(desc(prompt_query_results.c.created_at)).limit(1)).mappings().first()
            rows.append({**row_to_dict(query), 'result': row_to_dict(result) if result else None})
    analysed = [row['result'] for row in rows if row['result']]
    summary = {
        'tracked_count': len(rows),
        'analysed_count': len(analysed),
        'average_visibility': round(sum(item['visibility_score'] for item in analysed) / len(analysed)) if analysed else None,
        'citation_rate': round(sum(item['cited'] == 'Yes' for item in analysed) * 100 / len(analysed)) if analysed else None,
    }
    return {'collection': row_to_dict(collection), 'queries': rows, 'summary': summary}


@app.route('/prompt-intelligence')
def prompt_intelligence_page():
    if not session.get('user_id'):
        return redirect('/login')
    return send_from_directory(BASE_DIR, 'prompt_intelligence.html')


@app.route('/api/prompt-intelligence/collections', methods=['GET', 'POST'])
def prompt_collections_endpoint():
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        name = (data.get('name') or '').strip()
        brand_name = (data.get('brand_name') or '').strip()
        website = normalise_domain(data.get('website')) if data.get('website') else None
        if not name or not brand_name:
            return jsonify({'error': 'Give this collection a name and enter the brand name.'}), 400
        now = datetime.utcnow()
        with engine.begin() as conn:
            result = conn.execute(insert(prompt_collections).values(
                user_id=user_id, name=name[:150], brand_name=brand_name[:150], website=website,
                created_at=now, updated_at=now,
            ))
            collection_id = result.inserted_primary_key[0]
        return jsonify({'status': 'success', 'collection': row_to_dict(prompt_collection_for_user(collection_id, user_id))}), 201
    with engine.connect() as conn:
        collections = [dict(row) for row in conn.execute(select(prompt_collections).where(
            prompt_collections.c.user_id == user_id
        ).order_by(desc(prompt_collections.c.updated_at))).mappings().all()]
        for collection in collections:
            collection['prompt_count'] = len(conn.execute(select(prompt_queries.c.id).where(
                prompt_queries.c.collection_id == collection['id']
            )).all())
    return jsonify({'collections': [row_to_dict(item) for item in collections]})


@app.route('/api/prompt-intelligence/collections/<int:collection_id>', methods=['DELETE'])
def delete_prompt_collection(collection_id):
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    if not prompt_collection_for_user(collection_id, user_id):
        return jsonify({'error': 'Prompt collection not found.'}), 404
    with engine.begin() as conn:
        query_ids = [row[0] for row in conn.execute(select(prompt_queries.c.id).where(
            prompt_queries.c.collection_id == collection_id
        )).all()]
        if query_ids:
            conn.execute(prompt_query_results.delete().where(prompt_query_results.c.query_id.in_(query_ids)))
        conn.execute(prompt_queries.delete().where(prompt_queries.c.collection_id == collection_id))
        conn.execute(prompt_collections.delete().where(prompt_collections.c.id == collection_id))
    return jsonify({'status': 'success'})


@app.route('/api/prompt-intelligence/collections/<int:collection_id>/report', methods=['GET'])
def prompt_collection_report_endpoint(collection_id):
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    report = prompt_collection_report(collection_id, user_id)
    if not report:
        return jsonify({'error': 'Prompt collection not found.'}), 404
    return jsonify(report)


@app.route('/api/prompt-intelligence/collections/<int:collection_id>/queries', methods=['POST'])
def create_prompt_query(collection_id):
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    collection = prompt_collection_for_user(collection_id, user_id)
    if not collection:
        return jsonify({'error': 'Prompt collection not found.'}), 404
    data = request.get_json(silent=True) or {}
    prompt = (data.get('prompt') or '').strip()
    search_engine = (data.get('engine') or 'ChatGPT').strip()
    intent = (data.get('intent') or 'Informational').strip()
    allowed_engines = {'ChatGPT', 'Perplexity', 'Claude', 'Google AI Overviews', 'Microsoft Copilot'}
    allowed_intents = {'Informational', 'Commercial', 'Comparison', 'Navigational', 'Alternative'}
    if not prompt or len(prompt) > 500:
        return jsonify({'error': 'Enter a prompt of up to 500 characters.'}), 400
    if search_engine not in allowed_engines or intent not in allowed_intents:
        return jsonify({'error': 'Choose a valid engine and search intent.'}), 400
    now = datetime.utcnow()
    with engine.begin() as conn:
        result = conn.execute(insert(prompt_queries).values(
            collection_id=collection_id, prompt=prompt, engine=search_engine, intent=intent,
            created_at=now, updated_at=now,
        ))
        query_id = result.inserted_primary_key[0]
        conn.execute(prompt_collections.update().where(prompt_collections.c.id == collection_id).values(updated_at=now))
    return jsonify({'status': 'success', 'query_id': query_id}), 201


@app.route('/api/prompt-intelligence/queries/<int:query_id>/analyse', methods=['POST'])
def analyse_prompt_query(query_id):
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    query = prompt_query_for_user(query_id, user_id)
    if not query:
        return jsonify({'error': 'Prompt not found.'}), 404
    with engine.connect() as conn:
        iteration = len(conn.execute(select(prompt_query_results.c.id).where(
            prompt_query_results.c.query_id == query_id
        )).all()) + 1
    result_data = make_prompt_result(query, query['brand_name'], iteration)
    now = datetime.utcnow()
    with engine.begin() as conn:
        conn.execute(insert(prompt_query_results).values(query_id=query_id, created_at=now, **result_data))
        conn.execute(prompt_queries.update().where(prompt_queries.c.id == query_id).values(updated_at=now))
        conn.execute(prompt_collections.update().where(prompt_collections.c.id == query['collection_id']).values(updated_at=now))
    return jsonify({'status': 'success', 'report': prompt_collection_report(query['collection_id'], user_id)})


@app.route('/api/prompt-intelligence/queries/<int:query_id>', methods=['DELETE'])
def delete_prompt_query(query_id):
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    query = prompt_query_for_user(query_id, user_id)
    if not query:
        return jsonify({'error': 'Prompt not found.'}), 404
    with engine.begin() as conn:
        conn.execute(prompt_query_results.delete().where(prompt_query_results.c.query_id == query_id))
        conn.execute(prompt_queries.delete().where(prompt_queries.c.id == query_id))
        conn.execute(prompt_collections.update().where(prompt_collections.c.id == query['collection_id']).values(updated_at=datetime.utcnow()))
    return jsonify({'status': 'success'})


def watchlist_for_user(watchlist_id, user_id):
    with engine.connect() as conn:
        row = conn.execute(select(visibility_watchlists).where(
            (visibility_watchlists.c.id == watchlist_id) & (visibility_watchlists.c.user_id == user_id)
        )).mappings().first()
    return dict(row) if row else None


def make_visibility_report(watchlist, iteration):
    """Build a repeatable visibility baseline for a watchlist.

    Production live checks need consented provider connectors. This baseline is
    stored as a report and follows the same workflow users will use with those
    connectors: watchlist, scan, engine coverage, appearances and history.
    """
    seed = int(hashlib.sha256(
        f"{watchlist['brand_name']}:{watchlist['topic']}:{iteration}".encode()
    ).hexdigest()[:8], 16)
    score = min(95, 45 + seed % 39 + min(iteration - 1, 7))
    mentions_found = 11 + (seed >> 4) % 19
    citations_found = max(2, round(mentions_found * (score / 115)))
    competitor_mentions = 8 + (seed >> 9) % 16
    engine_offsets = [('ChatGPT', 9), ('Perplexity', 4), ('Google AI Overviews', -2), ('Claude', -5), ('Microsoft Copilot', -8)]
    engines = []
    for index, (engine_name, offset) in enumerate(engine_offsets):
        engine_score = max(15, min(96, score + offset + ((seed >> (index + 3)) % 5)))
        engines.append({
            'engine': engine_name,
            'visibility_score': engine_score,
            'mentions': max(1, round(engine_score / 11)),
            'citations': max(0, round(engine_score / 25)),
            'change': (seed >> (index + 11)) % 8 - 2,
        })
    competitors = ['G2', 'Capterra', 'HubSpot', 'Semrush', 'Ahrefs']
    competitor = competitors[(seed >> 18) % len(competitors)]
    topic = watchlist['topic'].lower()
    appearances = [
        ('ChatGPT', f'What are the best {topic} solutions?', 'Named recommendation', 'Positive', 'Yes', competitor, 'Add a proof-led comparison page that highlights your strongest differentiator.'),
        ('Perplexity', f'How do teams improve their {topic} workflow?', 'Supporting mention', 'Neutral', 'No', competitor, 'Publish a practical guide with original data and a sourceable product workflow.'),
        ('Google AI Overviews', f'{watchlist["brand_name"]} alternatives for {topic}', 'Comparison mention', 'Neutral', 'No', competitor, 'Strengthen your alternatives page with buyer criteria, outcomes, and clear feature evidence.'),
        ('Claude', f'Who should use {watchlist["brand_name"]}?', 'Brand answer', 'Positive', 'Yes', watchlist['brand_name'], 'Expand use-case pages with concise FAQs, testimonials, and expert attribution.'),
        ('Microsoft Copilot', f'How to choose a {topic} platform', 'Not mentioned', 'Absent', 'No', competitor, 'Create a selection guide that directly answers feature, cost, and implementation questions.'),
    ]
    mention_rows = [
        {'engine': engine_name, 'query': query, 'appearance': appearance, 'sentiment': sentiment,
         'cited': cited, 'competitor': leading_brand, 'action': action}
        for engine_name, query, appearance, sentiment, cited, leading_brand, action in appearances
    ]
    summary = (
        f"{watchlist['brand_name']} appears in {mentions_found} modelled AI answer opportunities for {watchlist['topic']}. "
        f"Prioritise unlinked comparison and selection answers where {competitor} currently has stronger coverage."
    )
    return {'visibility_score': score, 'mentions_found': mentions_found, 'citations_found': citations_found,
            'competitor_mentions': competitor_mentions, 'summary': summary, 'engines': engines, 'mentions': mention_rows}


def visibility_report(watchlist_id, user_id):
    watchlist = watchlist_for_user(watchlist_id, user_id)
    if not watchlist:
        return None
    with engine.connect() as conn:
        scan = conn.execute(select(visibility_scans).where(
            visibility_scans.c.watchlist_id == watchlist_id
        ).order_by(desc(visibility_scans.c.created_at)).limit(1)).mappings().first()
        if not scan:
            return {'watchlist': row_to_dict(watchlist), 'scan': None, 'engines': [], 'mentions': [], 'history': []}
        scan = dict(scan)
        engines = [dict(row) for row in conn.execute(select(visibility_engine_results).where(
            visibility_engine_results.c.scan_id == scan['id']
        ).order_by(desc(visibility_engine_results.c.visibility_score))).mappings().all()]
        mentions = [dict(row) for row in conn.execute(select(visibility_mentions).where(
            visibility_mentions.c.scan_id == scan['id']
        ).order_by(visibility_mentions.c.engine)).mappings().all()]
        history = [row_to_dict(row) for row in conn.execute(select(
            visibility_scans.c.visibility_score, visibility_scans.c.created_at
        ).where(visibility_scans.c.watchlist_id == watchlist_id).order_by(visibility_scans.c.created_at).limit(12)).mappings().all()]
    return {'watchlist': row_to_dict(watchlist), 'scan': row_to_dict(scan), 'engines': engines, 'mentions': mentions, 'history': history}


@app.route('/visibility-tracking')
def visibility_tracking_page():
    if not session.get('user_id'):
        return redirect('/login')
    return send_from_directory(BASE_DIR, 'visibility_tracking.html')


@app.route('/api/visibility-tracking/watchlists', methods=['GET', 'POST'])
def visibility_watchlists_endpoint():
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        name = (data.get('name') or '').strip()
        brand_name = (data.get('brand_name') or '').strip()
        topic = (data.get('topic') or '').strip()
        website = normalise_domain(data.get('website')) if data.get('website') else None
        if not name or not brand_name or not topic:
            return jsonify({'error': 'Enter a watchlist name, brand name, and topic.'}), 400
        now = datetime.utcnow()
        with engine.begin() as conn:
            result = conn.execute(insert(visibility_watchlists).values(
                user_id=user_id, name=name[:150], brand_name=brand_name[:150], website=website,
                topic=topic[:150], created_at=now, updated_at=now,
            ))
            watchlist_id = result.inserted_primary_key[0]
        return jsonify({'status': 'success', 'watchlist': row_to_dict(watchlist_for_user(watchlist_id, user_id))}), 201
    with engine.connect() as conn:
        watchlists = [dict(row) for row in conn.execute(select(visibility_watchlists).where(
            visibility_watchlists.c.user_id == user_id
        ).order_by(desc(visibility_watchlists.c.updated_at))).mappings().all()]
        for watchlist in watchlists:
            latest = conn.execute(select(visibility_scans.c.visibility_score, visibility_scans.c.created_at).where(
                visibility_scans.c.watchlist_id == watchlist['id']
            ).order_by(desc(visibility_scans.c.created_at)).limit(1)).mappings().first()
            watchlist['latest_scan'] = row_to_dict(latest) if latest else None
    return jsonify({'watchlists': [row_to_dict(item) for item in watchlists]})


@app.route('/api/visibility-tracking/watchlists/<int:watchlist_id>', methods=['DELETE'])
def delete_visibility_watchlist(watchlist_id):
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    if not watchlist_for_user(watchlist_id, user_id):
        return jsonify({'error': 'Visibility watchlist not found.'}), 404
    with engine.begin() as conn:
        scan_ids = [row[0] for row in conn.execute(select(visibility_scans.c.id).where(
            visibility_scans.c.watchlist_id == watchlist_id
        )).all()]
        if scan_ids:
            conn.execute(visibility_engine_results.delete().where(visibility_engine_results.c.scan_id.in_(scan_ids)))
            conn.execute(visibility_mentions.delete().where(visibility_mentions.c.scan_id.in_(scan_ids)))
        conn.execute(visibility_scans.delete().where(visibility_scans.c.watchlist_id == watchlist_id))
        conn.execute(visibility_watchlists.delete().where(visibility_watchlists.c.id == watchlist_id))
    return jsonify({'status': 'success'})


@app.route('/api/visibility-tracking/watchlists/<int:watchlist_id>/scan', methods=['POST'])
def scan_visibility_watchlist(watchlist_id):
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    watchlist = watchlist_for_user(watchlist_id, user_id)
    if not watchlist:
        return jsonify({'error': 'Visibility watchlist not found.'}), 404
    with engine.connect() as conn:
        iteration = len(conn.execute(select(visibility_scans.c.id).where(
            visibility_scans.c.watchlist_id == watchlist_id
        )).all()) + 1
    report = make_visibility_report(watchlist, iteration)
    now = datetime.utcnow()
    with engine.begin() as conn:
        result = conn.execute(insert(visibility_scans).values(
            watchlist_id=watchlist_id, created_at=now, visibility_score=report['visibility_score'],
            mentions_found=report['mentions_found'], citations_found=report['citations_found'],
            competitor_mentions=report['competitor_mentions'], summary=report['summary'],
        ))
        scan_id = result.inserted_primary_key[0]
        conn.execute(insert(visibility_engine_results), [dict(item, scan_id=scan_id) for item in report['engines']])
        conn.execute(insert(visibility_mentions), [dict(item, scan_id=scan_id) for item in report['mentions']])
        conn.execute(visibility_watchlists.update().where(visibility_watchlists.c.id == watchlist_id).values(updated_at=now))
    return jsonify({'status': 'success', 'report': visibility_report(watchlist_id, user_id)})


@app.route('/api/visibility-tracking/watchlists/<int:watchlist_id>/report', methods=['GET'])
def visibility_report_endpoint(watchlist_id):
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    report = visibility_report(watchlist_id, user_id)
    if not report:
        return jsonify({'error': 'Visibility watchlist not found.'}), 404
    return jsonify(report)


def content_document_for_user(document_id, user_id):
    with engine.connect() as conn:
        row = conn.execute(select(content_documents).where(
            (content_documents.c.id == document_id) & (content_documents.c.user_id == user_id)
        )).mappings().first()
    return dict(row) if row else None


def make_content_draft(document):
    """Create a structured, editable starter draft from the saved brief.

    This provides an end-to-end studio experience without presenting a local
    template as a live third-party model response. A provider-backed writer can
    replace this function when the customer supplies credentials.
    """
    title = document['title']
    brand = document['brand_name']
    keyword = document['keyword']
    content_type = document['content_type'].lower()
    tone = document['tone'].lower()
    seo_title = f"{title} | {brand}"[:200]
    meta_description = f"Learn how {brand} approaches {keyword}, with practical guidance, decision criteria, and clear next steps."[:320]
    outline = '\n'.join([
        f'Introduction: why {keyword} matters now',
        f'What to look for when evaluating {keyword}',
        f'How {brand} helps teams succeed',
        'Practical implementation steps',
        'Frequently asked questions',
        'Conclusion and next step',
    ])
    content = f"""# {title}

## Start with the outcome

Teams exploring **{keyword}** are usually looking for a clearer path from a business challenge to a measurable result. This {content_type} explains the choices that matter, the common trade-offs, and a practical way to move forward.

## What good {keyword} looks like

The strongest approach starts with a specific audience, a real workflow, and evidence that the solution can deliver. Avoid broad claims. Instead, define the problem, show the process, and connect each capability to an outcome the reader can recognise.

### A useful evaluation checklist

1. Identify the workflow that is creating the most friction.
2. Agree on the result that would make the investment worthwhile.
3. Compare options using proof, implementation effort, and long-term fit.
4. Give stakeholders a simple next step to validate the decision.

## How {brand} can help

{brand} helps teams turn their priorities into a focused plan. Lead with the use case that matters to the reader, support it with first-party evidence, and make the next action easy to understand.

## Put this into practice

Choose one priority workflow, document its current state, and use the checklist above to shape the first improvement. A clear, {tone} explanation supported by examples will be more useful—and more citeable—than a generic overview.

## Frequently asked questions

### Who is this for?

It is for teams evaluating {keyword} and looking for an outcome-focused starting point.

### What should we do next?

Start with the workflow where the gap is most visible, then validate your approach with stakeholders and real examples.
"""
    recommendations = '\n'.join([
        'Add a first-party statistic, customer quote, or worked example before publishing.',
        'Use the target keyword naturally in the introduction and one section heading.',
        'Link to a relevant product, comparison, or conversion page with descriptive anchor text.',
        'Review factual claims with a subject-matter expert before publishing.',
    ])
    return {'content': content, 'seo_title': seo_title, 'meta_description': meta_description,
            'outline': outline, 'recommendations': recommendations}


@app.route('/content-studio')
def content_studio_page():
    if not session.get('user_id'):
        return redirect('/login')
    return send_from_directory(BASE_DIR, 'content_studio.html')


@app.route('/api/content-studio/documents', methods=['GET', 'POST'])
def content_documents_endpoint():
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        title = (data.get('title') or '').strip()
        brand_name = (data.get('brand_name') or '').strip()
        keyword = (data.get('keyword') or '').strip()
        content_type = (data.get('content_type') or 'Blog post').strip()
        tone = (data.get('tone') or 'Expert').strip()
        allowed_types = {'Blog post', 'Landing page', 'Comparison page', 'Product page', 'Email'}
        allowed_tones = {'Expert', 'Conversational', 'Confident', 'Educational'}
        if not title or not brand_name or not keyword:
            return jsonify({'error': 'Enter a title, brand name, and target topic or keyword.'}), 400
        if content_type not in allowed_types or tone not in allowed_tones:
            return jsonify({'error': 'Choose a valid content type and tone.'}), 400
        now = datetime.utcnow()
        with engine.begin() as conn:
            result = conn.execute(insert(content_documents).values(
                user_id=user_id, title=title[:200], brand_name=brand_name[:150], keyword=keyword[:200],
                content_type=content_type, tone=tone, content='', seo_title='', meta_description='',
                outline='', recommendations='', status='Brief', version=0, created_at=now, updated_at=now,
            ))
            document_id = result.inserted_primary_key[0]
        return jsonify({'status': 'success', 'document': row_to_dict(content_document_for_user(document_id, user_id))}), 201
    with engine.connect() as conn:
        documents = [row_to_dict(row) for row in conn.execute(select(content_documents).where(
            content_documents.c.user_id == user_id
        ).order_by(desc(content_documents.c.updated_at))).mappings().all()]
    return jsonify({'documents': documents})


@app.route('/api/content-studio/documents/<int:document_id>', methods=['GET', 'PATCH', 'DELETE'])
def content_document_endpoint(document_id):
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    document = content_document_for_user(document_id, user_id)
    if not document:
        return jsonify({'error': 'Content document not found.'}), 404
    if request.method == 'GET':
        return jsonify({'document': row_to_dict(document)})
    if request.method == 'DELETE':
        with engine.begin() as conn:
            conn.execute(content_documents.delete().where(content_documents.c.id == document_id))
        return jsonify({'status': 'success'})

    data = request.get_json(silent=True) or {}
    updates = {}
    text_limits = {'title': 200, 'content': 30000, 'seo_title': 200, 'meta_description': 320}
    for key, limit in text_limits.items():
        if key in data and isinstance(data[key], str):
            value = data[key].strip() if key != 'content' else data[key]
            if not value and key == 'title':
                return jsonify({'error': 'A document title is required.'}), 400
            if len(value) > limit:
                return jsonify({'error': f'{key.replace("_", " ").title()} is too long.'}), 400
            updates[key] = value
    if data.get('status') in {'Brief', 'Draft', 'Ready for review', 'Published'}:
        updates['status'] = data['status']
    if not updates:
        return jsonify({'error': 'No valid document changes were provided.'}), 400
    updates['updated_at'] = datetime.utcnow()
    updates['version'] = document['version'] + 1
    with engine.begin() as conn:
        conn.execute(content_documents.update().where(content_documents.c.id == document_id).values(**updates))
    return jsonify({'status': 'success', 'document': row_to_dict(content_document_for_user(document_id, user_id))})


@app.route('/api/content-studio/documents/<int:document_id>/generate', methods=['POST'])
def generate_content_document(document_id):
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    document = content_document_for_user(document_id, user_id)
    if not document:
        return jsonify({'error': 'Content document not found.'}), 404
    draft = make_content_draft(document)
    with engine.begin() as conn:
        conn.execute(content_documents.update().where(content_documents.c.id == document_id).values(
            **draft, status='Draft', version=document['version'] + 1, updated_at=datetime.utcnow()
        ))
    return jsonify({'status': 'success', 'document': row_to_dict(content_document_for_user(document_id, user_id))})


def master_workspace_for_user(user_id):
    with engine.connect() as conn:
        row = conn.execute(select(master_workspaces).where(
            master_workspaces.c.user_id == user_id
        )).mappings().first()
    return dict(row) if row else None


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


@app.route('/api/master-workspace/summary')
def master_workspace_summary_endpoint():
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    summary = master_workspace_summary(user_id)
    return jsonify({'workspace': None} if not summary else summary)


@app.route('/api/master-workspace', methods=['GET', 'POST'])
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


@app.route('/workspace')
def workspace_page():
    if not session.get('user_id'):
        return redirect('/login')
    return send_from_directory(BASE_DIR, 'workspace.html')


@app.route('/profile')
def profile_page():
    if not session.get('user_id'):
        return redirect('/login')
    return send_from_directory(BASE_DIR, 'profile.html')


@app.route('/login')
def login_page():
    # simple HTML page that posts to /api/login via fetch
    html = """
    <!doctype html>
    <html>
      <head>
        <meta charset='utf-8'>
        <meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>Login</title>
        <style>*{box-sizing:border-box}body{font-family:system-ui,sans-serif;min-height:100vh;margin:0;padding:clamp(1rem,5vw,2rem);display:grid;align-content:center;background:#0b1220;color:#eef3ff}form{width:min(100%,26rem)}label{display:grid;gap:.4rem;margin:.8rem 0}input{padding:.7rem;width:100%;border-radius:8px;border:1px solid #333;background:#071018;color:#eef3ff;font-size:16px}button{margin-top:1rem;padding:.75rem 1rem;border-radius:8px;background:#ffba08;border:none;color:#061018;font-weight:700;cursor:pointer}a{color:#6eaff0}@media(max-width:400px){button{width:100%}}</style>
      </head>
      <body>
        <h1>Login</h1>
        <form id='login-form'>
          <label>Username or email<input name='username' required></label>
          <label>Password<input name='password' type='password' required></label>
          <label><input type='checkbox' name='remember'> Remember me</label>
          <button type='submit'>Log in</button>
        </form>
        <p>New? <a href='/register'>Create an account</a></p>
        <p id='note'></p>
        <script>
          const form=document.getElementById('login-form');
          form.addEventListener('submit', async e=>{
            e.preventDefault();
            const data={
              username: form.username.value,
              password: form.password.value,
              remember: form.remember.checked
            };
            const res=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
            const j=await res.json();
            const note=document.getElementById('note');
            if(res.ok){ note.textContent='Logged in. Redirecting...'; setTimeout(()=>location.href='/profile',400); } else { note.textContent = j.error || 'Login failed'; }
          });
        </script>
      </body>
    </html>
    """
    return html


@app.route('/register')
def register_page():
    html = """
    <!doctype html>
    <html>
      <head>
        <meta charset='utf-8'>
        <meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>Register</title>
        <style>*{box-sizing:border-box}body{font-family:system-ui,sans-serif;min-height:100vh;margin:0;padding:clamp(1rem,5vw,2rem);display:grid;align-content:center;background:#0b1220;color:#eef3ff}form{width:min(100%,26rem)}label{display:grid;gap:.4rem;margin:.8rem 0}input{padding:.7rem;width:100%;border-radius:8px;border:1px solid #333;background:#071018;color:#eef3ff;font-size:16px}button{margin-top:1rem;padding:.75rem 1rem;border-radius:8px;background:#ffba08;border:none;color:#061018;font-weight:700;cursor:pointer}a{color:#6eaff0}@media(max-width:400px){button{width:100%}}</style>
      </head>
      <body>
        <h1>Create an account</h1>
        <form id='reg-form'>
          <label>Username<input name='username' required></label>
          <label>Email<input name='email' type='email' required></label>
          <label>Password<input name='password' type='password' required></label>
          <button type='submit'>Register</button>
        </form>
        <p>Have an account? <a href='/login'>Log in</a></p>
        <p id='note'></p>
        <script>
          const form=document.getElementById('reg-form');
          form.addEventListener('submit', async e=>{
            e.preventDefault();
            const data={username:form.username.value,email:form.email.value,password:form.password.value};
            const res=await fetch('/api/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
            const j=await res.json();
            const note=document.getElementById('note');
            if(res.ok){ note.textContent='Registered. Redirecting to login...'; setTimeout(()=>location.href='/login',800); } else { note.textContent = j.error || 'Registration failed'; }
          });
        </script>
      </body>
    </html>
    """
    return html


@app.route('/admin/contacts')
def admin_contacts():
    # require login
    if not session.get('user_id'):
        return redirect('/login')

    with engine.connect() as conn:
        stmt = select(contacts.c.id, contacts.c.name, contacts.c.email, contacts.c.message, contacts.c.created_at).order_by(desc(contacts.c.created_at))
        result = conn.execute(stmt)
        rows = [row_to_dict(r) for r in result.mappings().all()]

    rows_html = ''.join(
        f"<tr><td>{c['id']}</td><td>{c['name']}</td><td>{c['email']}</td><td>{c['message']}</td><td>{c['created_at']}</td></tr>"
        for c in rows
    )
    html = f"""
    <!DOCTYPE html>
    <html lang='en'>
      <head>
        <meta charset='utf-8'>
        <meta name='viewport' content='width=device-width, initial-scale=1'>
        <title>Contact submissions</title>
        <style>
          * {{ box-sizing: border-box; }}
          body {{ font-family: system-ui, sans-serif; background: #0b1220; color: #eef3ff; margin: 0; padding: clamp(1rem, 4vw, 2rem); }}
          .table-wrap {{ overflow-x: auto; -webkit-overflow-scrolling: touch; }}
          table {{ width: 100%; min-width: 720px; border-collapse: collapse; margin-top: 1rem; }}
          th, td {{ border: 1px solid rgba(255,255,255,0.12); padding: 0.75rem 1rem; text-align: left; }}
          th {{ background: rgba(255,255,255,0.07); }}
          tr:nth-child(even) {{ background: rgba(255,255,255,0.03); }}
          h1 {{ margin: 0; font-size: 1.75rem; }}
          .note {{ color: #9cb2d3; margin-top: 0.5rem; }}
          a {{ color: #3f88c5; text-decoration: none; }}
        </style>
      </head>
      <body>
        <h1>Saved contact submissions</h1>
        <p class='note'>This page reads directly from the database used by the app (Postgres or SQLite depending on configuration).</p>
        <p><a href='/'>Back to homepage</a></p>
        <div class='table-wrap'>
          <table>
            <thead>
              <tr><th>ID</th><th>Name</th><th>Email</th><th>Message</th><th>Created at</th></tr>
            </thead>
            <tbody>
              {rows_html or '<tr><td colspan="5">No submissions yet.</td></tr>'}
            </tbody>
          </table>
        </div>
      </body>
    </html>
    """
    return html


@app.cli.command('run-scheduled-analytics')
def run_scheduled_analytics_command():
    """Recover queued jobs and enqueue due prompt schedules for a cron worker."""
    now = datetime.utcnow()
    stale_before = now - timedelta(minutes=45)
    batch_size = max(1, min(int(os.environ.get('ANALYTICS_JOB_BATCH', '10')), 50))
    with engine.begin() as conn:
        conn.execute(update(analytics_audit_jobs).where(
            (analytics_audit_jobs.c.status == 'running') &
            (analytics_audit_jobs.c.started_at < stale_before)
        ).values(status='failed_retryable', error='Recovered after a stale worker lease.'))
        due_schedules = conn.execute(select(
            analytics_scan_schedules,
            analytics_projects.c.user_id,
        ).join(
            analytics_projects, analytics_scan_schedules.c.project_id == analytics_projects.c.id
        ).where(
            (analytics_scan_schedules.c.enabled.is_(True)) &
            (analytics_scan_schedules.c.next_run_at <= now)
        ).order_by(analytics_scan_schedules.c.next_run_at).limit(batch_size)).mappings().all()
    scheduled_count = 0
    for schedule in due_schedules:
        project = project_for_user(schedule['project_id'], schedule['user_id'])
        if not project:
            continue
        with engine.connect() as conn:
            active = conn.execute(select(analytics_audit_jobs.c.id).where(
                (analytics_audit_jobs.c.project_id == schedule['project_id']) &
                (analytics_audit_jobs.c.job_type == 'prompt_scan') &
                (analytics_audit_jobs.c.status.in_(['queued', 'running', 'failed_retryable']))
            ).limit(1)).scalar_one_or_none()
        if not active:
            create_analytics_job(project, schedule['user_id'], 'prompt_scan', provider='Perplexity')
            scheduled_count += 1
        with engine.begin() as conn:
            conn.execute(update(analytics_scan_schedules).where(
                analytics_scan_schedules.c.id == schedule['id']
            ).values(
                last_run_at=now, next_run_at=next_schedule_time(schedule['frequency'], now),
                updated_at=now,
            ))

    with engine.connect() as conn:
        jobs = conn.execute(select(analytics_audit_jobs.c.id, analytics_audit_jobs.c.job_type).where(
            analytics_audit_jobs.c.status.in_(['queued', 'failed_retryable'])
        ).order_by(analytics_audit_jobs.c.created_at).limit(batch_size)).all()
    processed = 0
    for job_id, job_type in jobs:
        try:
            if job_type == 'site_audit':
                run_site_audit_job(job_id)
                processed += 1
            elif job_type == 'prompt_scan':
                run_prompt_scan_job(job_id)
                processed += 1
        except Exception as error:
            update_analytics_job(
                job_id, status='failed_retryable', error=str(error)[:2000],
                completed_at=datetime.utcnow(),
            )
    print(f'Analytics worker queued {scheduled_count} scheduled scan(s) and processed {processed} job(s).')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port)
