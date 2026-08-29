"""CLI worker: schedule enqueue, stale-lease recovery, job dispatch."""

from datetime import date, datetime, timedelta
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
import os

from app.db import engine
from app.jobs import create_analytics_job, run_site_audit_job, update_analytics_job
from app.models import analytics_audit_jobs, analytics_projects, analytics_scan_schedules
from app.ownership import project_for_user
from app.scanning import next_schedule_time, run_prompt_scan_job

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


def register_cli(app):
    """Re-register the `flask run-scheduled-analytics` command on the built app.

    In the monolith this was an @app.cli.command decorator at import time. The
    function stays directly callable so existing invocations keep working.
    """
    app.cli.command('run-scheduled-analytics')(run_scheduled_analytics_command)
