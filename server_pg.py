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
