"""Workspace ownership lookups, resolved through org membership.

T5 removed `WHERE user_id = ?` from these: a workspace belongs to an organization,
and a user reaches it by being a member of that org. T6 replaces this module with
app/tenancy.py, adding require_workspace()/require_org() and role checks — these
functions do not yet enforce roles, so a client_viewer can still write.
"""

from datetime import datetime

from flask import jsonify
from sqlalchemy import insert, select

from app.auth import analytics_user_id
from app.db import engine
from app.models import content_documents, memberships, organizations, workspaces


def workspace_for_user(workspace_id, user_id):
    """Return the workspace if the user belongs to the org that owns it."""
    with engine.connect() as conn:
        row = conn.execute(
            select(workspaces)
            .join(memberships, memberships.c.org_id == workspaces.c.org_id)
            .where(
                (workspaces.c.id == workspace_id)
                & (memberships.c.user_id == user_id)
                & (workspaces.c.status == 'active')
            )
            .limit(1)
        ).mappings().first()
    return dict(row) if row else None


def workspaces_for_user(user_id):
    """Every active workspace the user can reach, newest first."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(workspaces)
            .join(memberships, memberships.c.org_id == workspaces.c.org_id)
            .where(
                (memberships.c.user_id == user_id)
                & (workspaces.c.status == 'active')
            )
            .order_by(workspaces.c.created_at.desc())
        ).mappings().all()
    return [dict(row) for row in rows]


def ensure_workspace_access(workspace_id):
    """Resolve the session user and the workspace, or the response to return."""
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return None, None, auth_error
    workspace = workspace_for_user(workspace_id, user_id)
    if not workspace:
        return user_id, None, (jsonify({'error': 'Workspace not found.'}), 404)
    return user_id, workspace, None


def content_document_for_user(document_id, user_id):
    """A document is reachable when the user belongs to its workspace's org.

    The route shape is unchanged — no workspace_id in the URL — but the predicate
    is now tenancy, not `content_documents.user_id`, which T5 removed.
    """
    with engine.connect() as conn:
        row = conn.execute(
            select(content_documents)
            .join(workspaces, workspaces.c.id == content_documents.c.workspace_id)
            .join(memberships, memberships.c.org_id == workspaces.c.org_id)
            .where(
                (content_documents.c.id == document_id)
                & (memberships.c.user_id == user_id)
                & (workspaces.c.status == 'active')
            )
            .limit(1)
        ).mappings().first()
    return dict(row) if row else None


def content_documents_for_user(user_id):
    """Every document in every workspace the user can reach, newest first."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(content_documents)
            .join(workspaces, workspaces.c.id == content_documents.c.workspace_id)
            .join(memberships, memberships.c.org_id == workspaces.c.org_id)
            .where(
                (memberships.c.user_id == user_id)
                & (workspaces.c.status == 'active')
            )
            .order_by(content_documents.c.updated_at.desc())
        ).mappings().all()
    return [dict(row) for row in rows]


def default_org_for_user(user_id):
    """The org a new workspace belongs to, created with the user as owner if needed.

    A workspace now requires an org_id, but registration predates T5 and creates
    only a users row. Rather than fail on first workspace creation, the user's
    personal org is created lazily here. T6 replaces this when invitations and
    role assignment become explicit.
    """
    with engine.connect() as conn:
        org_id = conn.execute(
            select(memberships.c.org_id)
            .where(memberships.c.user_id == user_id)
            .order_by(memberships.c.org_id)
            .limit(1)
        ).scalar_one_or_none()
    if org_id:
        return org_id
    with engine.begin() as conn:
        org_id = conn.execute(insert(organizations).values(
            name='Personal workspace', created_at=datetime.utcnow(),
        )).inserted_primary_key[0]
        conn.execute(insert(memberships).values(
            org_id=org_id, user_id=user_id, role='owner',
        ))
    return org_id
