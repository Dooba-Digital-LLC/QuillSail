from datetime import timedelta

import pytest
from sqlalchemy import select

from copydesk import jobs, newsletter, subscribers
from copydesk.db import db
from copydesk.mail import Mailer
from copydesk.models import Delivery, Issue, Job, User
from copydesk.utils import utcnow


def test_successful_delivery_survives_later_batch_crash(app, admin, monkeypatch):
    attempted = []
    fail = True

    def send(self, email):
        attempted.append(email.to)
        if fail and email.to == 'second@example.edu':
            raise RuntimeError('Simulated process error after the first delivery')
        return 'test-message-id'

    monkeypatch.setattr(Mailer, 'send', send)
    with app.app_context():
        subscribers.subscribe('first@example.edu', confirmed=True, welcome=False)
        subscribers.subscribe('second@example.edu', confirmed=True, welcome=False)
        issue = newsletter.create(db.session.get(User, admin), 'Delivery recovery')
        issue.intro = 'Hello readers.'
        newsletter.start_sending(issue, db.session.get(User, admin))
        db.session.commit()
        assert jobs.run_one()
        assert jobs.run_one()
        first = db.session.scalar(select(Delivery).where(Delivery.email == 'first@example.edu'))
        assert first.status == 'sent'
        batch = db.session.scalar(select(Job).where(Job.kind == 'issue.batch'))
        assert batch.status == 'queued'
        batch.run_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
        fail = False
        jobs.run_pending(max_jobs=10)
        assert attempted.count('first@example.edu') == 1
        assert db.session.get(Issue, issue.id).status == 'sent'


def test_stale_issue_object_cannot_start_second_send(app, admin):
    from sqlalchemy.orm import Session

    with app.app_context():
        actor = db.session.get(User, admin)
        issue = newsletter.create(actor, 'One dispatch')
        issue.intro = 'Hello readers.'
        db.session.commit()
        with Session(db.engine, expire_on_commit=False) as other:
            stale = other.get(Issue, issue.id)
            newsletter.start_sending(issue, actor)
            db.session.commit()
            # Simulate a second request that loaded the draft before the first committed.
            db.session.expunge(issue)
            other.expunge(stale)
            db.session.add(stale)
            with pytest.raises(newsletter.IssueError):
                newsletter.start_sending(stale, actor)
            db.session.rollback()
        assert db.session.scalar(select(Job).where(Job.kind == 'issue.prepare')) is not None
        assert len(db.session.scalars(select(Job).where(Job.kind == 'issue.prepare')).all()) == 1


def test_running_jobs_are_not_retried_from_dashboard(editor_client, app):
    with app.app_context():
        job = jobs.enqueue('mail.send', {})
        job.status = 'running'
        job.locked_by = 'active-worker'
        job.locked_at = utcnow()
        db.session.commit()
        job_id = job.id
    response = editor_client.post(f'/dashboard/system/jobs/{job_id}', data={
        'csrf_token': 'test-csrf', 'action': 'retry',
    })
    assert response.status_code == 409
    with app.app_context():
        assert db.session.get(Job, job_id).status == 'running'
