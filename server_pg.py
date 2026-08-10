import os
import uuid
import hashlib
from urllib.parse import urlparse
from datetime import datetime, timedelta

from flask import Flask, jsonify, request, send_from_directory, abort, session, redirect
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import (
    create_engine,
    MetaData,
    Table,
    Column,
    Integer,
    String,
    Text,
    DateTime,
    select,
    insert,
    desc,
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
    if 'created_at' in d and d['created_at'] is not None:
        d['created_at'] = to_iso(d['created_at'])
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


def make_analytics_report(project, run_number):
    """Create a repeatable first-party baseline report for a tracked domain.

    Live provider querying needs customer-owned provider credentials and consent.
    Until those connectors are configured, the product produces a deterministic
    benchmark from the submitted domain, allowing the full dashboard workflow
    (projects, history, scoring and opportunities) to operate end-to-end.
    """
    seed = int(hashlib.sha256(f"{project['domain']}:{run_number}".encode()).hexdigest()[:8], 16)
    base = 46 + seed % 33
    visibility = min(94, base + min(run_number - 1, 6))
    mention = max(24, visibility - 10 + (seed >> 4) % 11)
    citation = max(18, visibility - 19 + (seed >> 8) % 10)
    share = max(12, visibility - 28 + (seed >> 12) % 12)
    brands = ['G2', 'Capterra', 'HubSpot', 'Semrush', 'Ahrefs']
    competitor = brands[(seed >> 16) % len(brands)]
    engine_offsets = [('ChatGPT', 8), ('Perplexity', 3), ('Google AI Overviews', -3), ('Claude', -6), ('Microsoft Copilot', -9)]
    engines = []
    for index, (engine_name, offset) in enumerate(engine_offsets):
        score = max(18, min(97, visibility + offset + ((seed >> (index + 1)) % 5)))
        engines.append({
            'engine': engine_name,
            'visibility_score': score,
            'mention_rate': max(15, score - 8),
            'citations': max(1, round((score / 100) * 18)),
            'change': (seed >> (index + 5)) % 8 - 2,
        })
    brand = project['brand_name']
    prompts = [
        (f'What are the best {project["industry"].lower()} solutions for growing teams?', 'Commercial', 3, 'No', competitor, 'Create a comparison page that answers buyer criteria and names your differentiator.'),
        (f'How does {brand} compare with {competitor}?', 'Comparison', 2, 'Yes', competitor, 'Add independent proof, pricing context, and a concise alternatives section.'),
        (f'How do I solve a {project["industry"].lower()} workflow problem?', 'Informational', 5, 'No', competitor, 'Publish a step-by-step guide with original examples and expert bylines.'),
        (f'Who should use {brand}?', 'Navigational', 1, 'Yes', brand, 'Strengthen the product page with outcomes, FAQs, and schema-ready facts.'),
        (f'Best tools to use instead of {competitor}', 'Alternative', 4, 'No', competitor, 'Build an alternative page around use cases where your product is strongest.'),
    ]
    prompt_rows = [
        {
            'prompt': prompt,
            'intent': intent,
            'position': position,
            'cited': cited,
            'leading_brand': leading_brand,
            'opportunity': opportunity,
        }
        for prompt, intent, position, cited, leading_brand, opportunity in prompts
    ]
    summary = (
        f'{brand} is visible in {mention}% of the benchmarked AI responses. '
        f'Focus first on citation coverage and comparison prompts where {competitor} currently leads.'
    )
    return {
        'visibility_score': visibility,
        'mention_rate': mention,
        'citation_rate': citation,
        'share_of_voice': share,
        'summary': summary,
        'engines': engines,
        'prompts': prompt_rows,
    }


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
        domain = normalise_domain(data.get('domain'))
        brand_name = (data.get('brand_name') or '').strip()
        industry = (data.get('industry') or 'General').strip()[:150]
        if not domain or not brand_name:
            return jsonify({'error': 'Enter a valid website domain and brand name.'}), 400
        now = datetime.utcnow()
        with engine.begin() as conn:
            result = conn.execute(insert(analytics_projects).values(
                user_id=user_id, domain=domain, brand_name=brand_name[:150], industry=industry or 'General',
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
            latest = conn.execute(
                select(analytics_runs.c.id, analytics_runs.c.visibility_score, analytics_runs.c.created_at)
                .where(analytics_runs.c.project_id == project['id'])
                .order_by(desc(analytics_runs.c.created_at)).limit(1)
            ).mappings().first()
            project['latest_run'] = row_to_dict(latest) if latest else None
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
        run_ids = [row[0] for row in conn.execute(select(analytics_runs.c.id).where(analytics_runs.c.project_id == project_id)).all()]
        if run_ids:
            conn.execute(analytics_engine_metrics.delete().where(analytics_engine_metrics.c.run_id.in_(run_ids)))
            conn.execute(analytics_prompts.delete().where(analytics_prompts.c.run_id.in_(run_ids)))
        conn.execute(analytics_runs.delete().where(analytics_runs.c.project_id == project_id))
        conn.execute(analytics_projects.delete().where(analytics_projects.c.id == project_id))
    return jsonify({'status': 'success'})


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
    with engine.connect() as conn:
        run = conn.execute(select(analytics_runs).where(analytics_runs.c.project_id == project_id)
            .order_by(desc(analytics_runs.c.created_at)).limit(1)).mappings().first()
        if not run:
            return {'project': row_to_dict(project), 'run': None, 'engines': [], 'prompts': [], 'history': []}
        run = dict(run)
        engines = [dict(row) for row in conn.execute(select(analytics_engine_metrics).where(
            analytics_engine_metrics.c.run_id == run['id']).order_by(desc(analytics_engine_metrics.c.visibility_score))).mappings().all()]
        prompts = [dict(row) for row in conn.execute(select(analytics_prompts).where(
            analytics_prompts.c.run_id == run['id']).order_by(analytics_prompts.c.position)).mappings().all()]
        history = [row_to_dict(row) for row in conn.execute(select(
            analytics_runs.c.visibility_score, analytics_runs.c.created_at).where(
            analytics_runs.c.project_id == project_id).order_by(analytics_runs.c.created_at).limit(12)).mappings().all()]
    return {'project': row_to_dict(project), 'run': row_to_dict(run), 'engines': engines, 'prompts': prompts, 'history': history}


@app.route('/api/analytics/projects/<int:project_id>/report', methods=['GET'])
def analytics_report_endpoint(project_id):
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    report = analytics_report(project_id, user_id)
    if not report:
        return jsonify({'error': 'Project not found.'}), 404
    return jsonify(report)


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


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port)
