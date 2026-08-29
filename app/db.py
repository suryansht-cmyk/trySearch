"""Engine, metadata and the startup schema bootstrap.

T2 replaces bootstrap_database() below with Alembic migrations. The logic is moved
here unchanged so T1 stays a pure move.
"""

import os
import uuid

from sqlalchemy import MetaData, create_engine, insert, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.config import IS_PRODUCTION, SQLITE_PATH


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



def get_database_identity():
    """Return the database's permanent application identity, creating it once."""
    from app.models import app_metadata
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



# Set by bootstrap_database(). Read it as db.DATABASE_IDENTITY rather than importing
# the name, so callers see the value bound at startup rather than this placeholder.
DATABASE_IDENTITY = None


def bootstrap_database():
    """Create the schema and pin the database identity, exactly as import used to.

    app.models imports `metadata` from this module, so the table definitions are
    imported here inside the function rather than at module scope, where they would
    form a cycle.
    """
    global DATABASE_IDENTITY
    from app import models  # noqa: F401 - registers the tables on `metadata`

    # Create tables if they don't exist
    metadata.create_all(engine)

    ensure_database_column('analytics_projects', 'website_url', 'VARCHAR(2048)')
    ensure_database_column('analytics_provider_answers', 'prompt_text', 'TEXT')
    ensure_database_column('analytics_provider_answers', 'prompt_intent', 'VARCHAR(80)')
    ensure_database_column('analytics_provider_answers', 'topic_name', 'VARCHAR(180)')
    ensure_database_column('analytics_prompt_scan_runs', 'competitor_snapshot', 'TEXT')

    DATABASE_IDENTITY = get_database_identity()
    expected_database_identity = os.environ.get('DATABASE_INSTANCE_ID')
    if expected_database_identity and expected_database_identity != DATABASE_IDENTITY:
        raise RuntimeError(
            'DATABASE_INSTANCE_ID does not match the connected database. Refusing to start '
            'against an unexpected database.'
        )
    return DATABASE_IDENTITY
