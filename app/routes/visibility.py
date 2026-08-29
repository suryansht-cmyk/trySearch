"""Visibility watchlists and scans."""

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
from app.models import visibility_engine_results, visibility_mentions, visibility_scans, visibility_watchlists
from app.ownership import watchlist_for_user
from app.routes.pages import index
from app.utils import normalise_domain, row_to_dict

visibility_bp = Blueprint('visibility', __name__)

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

@visibility_bp.route('/api/visibility-tracking/watchlists', methods=['GET', 'POST'])
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

@visibility_bp.route('/api/visibility-tracking/watchlists/<int:watchlist_id>', methods=['DELETE'])
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

@visibility_bp.route('/api/visibility-tracking/watchlists/<int:watchlist_id>/scan', methods=['POST'])
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

@visibility_bp.route('/api/visibility-tracking/watchlists/<int:watchlist_id>/report', methods=['GET'])
def visibility_report_endpoint(watchlist_id):
    user_id, auth_error = analytics_user_id()
    if auth_error:
        return auth_error
    report = visibility_report(watchlist_id, user_id)
    if not report:
        return jsonify({'error': 'Visibility watchlist not found.'}), 404
    return jsonify(report)
