"""Content studio documents."""

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
from app.models import content_documents
from app.ownership import content_document_for_user
from app.utils import row_to_dict

content_bp = Blueprint('content', __name__)

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

@content_bp.route('/api/content-studio/documents', methods=['GET', 'POST'])
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

@content_bp.route('/api/content-studio/documents/<int:document_id>', methods=['GET', 'PATCH', 'DELETE'])
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

@content_bp.route('/api/content-studio/documents/<int:document_id>/generate', methods=['POST'])
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
