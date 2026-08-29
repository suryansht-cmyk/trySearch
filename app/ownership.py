"""Hand-written per-user ownership lookups.

T6 replaces this entire module with app/tenancy.py and its require_workspace()
guard. Moved unchanged here so T1 stays a pure move."""

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
from app.models import analytics_projects, content_documents, master_workspaces, prompt_collections, prompt_queries, visibility_watchlists

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

def ensure_project_owner(project_id):
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return None, None, auth_error
    project = project_for_user(project_id, user_id)
    if not project:
        return user_id, None, (jsonify({'error': 'Project not found.'}), 404)
    return user_id, project, None

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

def watchlist_for_user(watchlist_id, user_id):
    with engine.connect() as conn:
        row = conn.execute(select(visibility_watchlists).where(
            (visibility_watchlists.c.id == watchlist_id) & (visibility_watchlists.c.user_id == user_id)
        )).mappings().first()
    return dict(row) if row else None

def content_document_for_user(document_id, user_id):
    with engine.connect() as conn:
        row = conn.execute(select(content_documents).where(
            (content_documents.c.id == document_id) & (content_documents.c.user_id == user_id)
        )).mappings().first()
    return dict(row) if row else None

def master_workspace_for_user(user_id):
    with engine.connect() as conn:
        row = conn.execute(select(master_workspaces).where(
            master_workspaces.c.user_id == user_id
        )).mappings().first()
    return dict(row) if row else None
