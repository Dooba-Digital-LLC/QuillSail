from datetime import timedelta

import pytest
from sqlalchemy import select

from copydesk import auth, groups, jobs, mail, newsletter, subscribers, workflow
from copydesk.db import db
from copydesk.models import AnonymousUser, Group, Issue, User
from copydesk.utils import utcnow


@pytest.mark.parametrize('path', [
    '/', '/articles/', '/issues/', '/search?q=hello', '/about', '/guidelines',
    '/privacy', '/join', '/login', '/forgot-password', '/subscribe',
    '/feed.xml', '/issues/feed.xml', '/sitemap.xml', '/robots.txt', '/healthz', '/favicon.svg',
])
def test_public_pages(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert 'Content-Security-Policy' in response.headers


@pytest.mark.parametrize('path', [
    '/dashboard/', '/dashboard/articles/', '/dashboard/issues/', '/dashboard/members/',
    '/dashboard/groups/', '/dashboard/subscribers/', '/dashboard/pages/',
    '/dashboard/settings/', '/dashboard/system', '/dashboard/log/',
    '/account/profile', '/account/groups', '/account/newsletter', '/account/notifications',
    '/account/security', '/account/privacy', '/account/delete', '/write/',
])
def test_admin_pages(editor_client, path):
    assert editor_client.get(path, follow_redirects=True).status_code == 200


def test_csrf_and_permissions(client):
    assert client.post('/dashboard/issues/new').status_code == 400
    assert client.get('/dashboard/', follow_redirects=False).status_code in (302, 403)


def test_composer_and_preview(editor_client, app):
    response = editor_client.post('/dashboard/issues/new', data={'csrf_token': 'test-csrf'})
    assert response.status_code == 302
    assert editor_client.get(response.location).status_code == 200
    with app.app_context():
        issue = db.session.scalar(select(Issue))
        issue.intro = 'Welcome to Example Newsletter.'
        newsletter.add_block(issue, 'text', title='Community', body='News for everyone.')
        db.session.commit()
        issue_id = issue.id
    for suffix in ('preview', 'email', 'send'):
        assert editor_client.get(f'/dashboard/issues/{issue_id}/{suffix}').status_code == 200


def test_editorial_workflow(app, admin):
    with app.app_context():
        editor = db.session.get(User, admin)
        writer = User(email='writer@example.edu', username='writer', display_name='Writer',
                      password_hash=auth.hash_password('writer-password-123'), email_verified_at=utcnow())
        other = User(email='other@example.edu', username='other', display_name='Other',
                     password_hash='unused', email_verified_at=utcnow())
        db.session.add_all([writer, other])
        db.session.flush()
        article = workflow.create_draft(writer, 'Our first story')
        workflow.save(article, writer, title=article.title, subtitle='An introduction', body='A useful word ' * 30)
        assert not workflow.can_see_submission(editor, article)
        assert not workflow.can_edit(other, article)
        with pytest.raises(workflow.WorkflowError):
            workflow.transition(article, 'accept', other)
        workflow.transition(article, 'submit', writer)
        workflow.transition(article, 'start_review', editor)
        with pytest.raises(workflow.WorkflowError):
            workflow.transition(article, 'request_changes', editor)
        workflow.transition(article, 'request_changes', editor, 'Please add sources.')
        assert workflow.can_edit(writer, article)
        workflow.transition(article, 'resubmit', writer)
        workflow.transition(article, 'accept', editor)
        workflow.transition(article, 'publish', editor)
        assert workflow.can_view(AnonymousUser(), article)
        assert len(workflow.revisions(article)) == 1
        assert jobs.queue_counts()['queued'] > 0


def test_group_approval_and_draft_privacy(app, admin):
    with app.app_context():
        writer = User(email='alum@example.edu', username='alum', display_name='Alum',
                      password_hash='unused', email_verified_at=utcnow())
        db.session.add(writer)
        db.session.flush()
        group = db.session.scalar(select(Group).where(Group.slug == 'alumni'))
        membership = groups.request(writer, group, data={'graduation_year': 2020})
        assert membership.status == 'pending'
        groups.approve(membership, db.session.get(User, admin))
        assert membership.status == 'active'
        article = workflow.create_draft(writer, 'Private')
        assert not workflow.can_view(AnonymousUser(), article)
        assert not workflow.can_view(db.session.get(User, admin), article)


def test_subscription_double_opt_in_and_unsubscribe(client, app):
    response = client.post('/subscribe', data={'email': 'reader@example.edu', 'name': 'Reader'})
    assert response.status_code == 302
    with app.app_context():
        sub = subscribers.find('reader@example.edu')
        assert sub.status == 'pending'
        token = auth.make_token('confirm-subscription', {'sid': sub.id, 'email': sub.email})
        secret = sub.token
        issue = Issue(number=1, title='Hello', intro='Welcome')
        db.session.add(issue)
        db.session.flush()
        assert newsletter.recipients(issue) == []
    assert client.get(f'/subscribe/confirm/{token}').status_code == 200
    assert client.get(f'/unsubscribe/{secret}').status_code == 200
    with app.app_context():
        assert subscribers.find('reader@example.edu').status == 'active'
    assert client.post(f'/unsubscribe/{secret}', data={'List-Unsubscribe': 'One-Click'}).status_code == 200
    with app.app_context():
        assert subscribers.find('reader@example.edu').status == 'unsubscribed'


def test_delivery_and_audience_filtering(app, admin):
    with app.app_context():
        editor = db.session.get(User, admin)
        alumni = db.session.scalar(select(Group).where(Group.slug == 'alumni'))
        general, _ = subscribers.subscribe('general@example.edu', confirmed=True, welcome=False)
        alum, _ = subscribers.subscribe('alumni@example.edu', groups=[alumni], confirmed=True, welcome=False)
        issue = newsletter.create(editor, 'Community news')
        issue.intro = 'Welcome to the newsletter.'
        newsletter.add_block(issue, 'text', body='Public community news.')
        private = newsletter.add_block(issue, 'text', body='Alumni office hours.')
        private.audience_groups = [alumni]
        db.session.flush()
        assert 'Alumni office hours.' not in newsletter.render(issue, subscriber=general)[2]
        assert 'Alumni office hours.' in newsletter.render(issue, subscriber=alum)[2]
        newsletter.start_sending(issue, editor)
        db.session.commit()
        jobs.run_pending(max_jobs=20)
        issue = db.session.get(Issue, issue.id)
        assert issue.status == 'sent'
        assert newsletter.delivery_stats(issue)['sent'] == 2
        assert len(mail.outbox) == 2
        assert all(msg['List-Unsubscribe-Post'] == 'List-Unsubscribe=One-Click' for msg in mail.outbox)
        jobs.run_pending(max_jobs=20)
        assert len(mail.outbox) == 2


def test_scheduling_and_reset_tokens(app, admin):
    with app.app_context():
        user = db.session.get(User, admin)
        token = auth.reset_token(user)
        assert auth.user_from_reset_token(token) == user
        user.password_hash = auth.hash_password('another-password-456')
        assert auth.user_from_reset_token(token) is None
        assert auth.read_token('verify-email', token) is None
        issue = newsletter.create(user)
        with pytest.raises(newsletter.IssueError):
            newsletter.schedule(issue, utcnow() - timedelta(hours=1), user)
        newsletter.schedule(issue, utcnow() + timedelta(hours=2), user)
        assert issue.status == 'scheduled'
        newsletter.unschedule(issue, user)
        assert issue.status == 'draft'


def test_public_signup_cannot_change_existing_preferences(client, app):
    with app.app_context():
        sub, _ = subscribers.subscribe('existing@example.edu', confirmed=True, welcome=False)
        db.session.commit()
        secret = sub.token
    assert client.post('/subscribe', data={
        'email': 'existing@example.edu', 'name': 'Uninvited change', 'groups': 'alumni',
    }).status_code == 302
    with app.app_context():
        sub = subscribers.find('existing@example.edu')
        assert sub.name == ''
        assert sub.groups == []
    assert client.post(f'/subscription/{secret}', data={'name': 'My name', 'groups': 'alumni'}).status_code == 302
    with app.app_context():
        sub = subscribers.find('existing@example.edu')
        assert sub.name == 'My name'
        assert [g.slug for g in sub.groups] == ['alumni']


def test_login_and_session_revocation(client, app, admin):
    client.get('/login')
    with client.session_transaction() as session:
        token = session['_csrf']
    response = client.post('/login', data={
        'email': 'editor@example.edu', 'password': 'correct-horse-battery-123', 'csrf_token': token,
    })
    assert response.status_code == 302
    assert client.get('/dashboard/').status_code == 200
    with app.app_context():
        db.session.get(User, admin).auth_version += 1
        db.session.commit()
    assert client.get('/dashboard/').status_code == 302


def test_markdown_sanitizes_active_content(app):
    from copydesk.text import render_markdown

    with app.app_context():
        html = str(render_markdown('<script>alert(1)</script>\n\n[unsafe](javascript:alert(1))'))
        assert '<script>' not in html
        assert 'href="javascript:' not in html
