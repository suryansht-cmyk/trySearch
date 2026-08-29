"""Prompt collections and queries."""

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
import hashlib

from app.auth import analytics_user_id
from app.db import engine
from app.models import prompt_collections, prompt_queries, prompt_query_results
from app.ownership import prompt_collection_for_user, prompt_query_for_user
from app.utils import normalise_domain, row_to_dict

prompt_intelligence_bp = Blueprint('prompt_intelligence', __name__)

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

@prompt_intelligence_bp.route('/api/prompt-intelligence/collections', methods=['GET', 'POST'])
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

@prompt_intelligence_bp.route('/api/prompt-intelligence/collections/<int:collection_id>', methods=['DELETE'])
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

@prompt_intelligence_bp.route('/api/prompt-intelligence/collections/<int:collection_id>/report', methods=['GET'])
def prompt_collection_report_endpoint(collection_id):
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    report = prompt_collection_report(collection_id, user_id)
    if not report:
        return jsonify({'error': 'Prompt collection not found.'}), 404
    return jsonify(report)

@prompt_intelligence_bp.route('/api/prompt-intelligence/collections/<int:collection_id>/queries', methods=['POST'])
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

@prompt_intelligence_bp.route('/api/prompt-intelligence/queries/<int:query_id>/analyse', methods=['POST'])
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

@prompt_intelligence_bp.route('/api/prompt-intelligence/queries/<int:query_id>', methods=['DELETE'])
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
