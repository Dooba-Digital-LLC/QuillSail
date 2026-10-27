import pytest
from sqlalchemy import select

from copydesk import auth, groups, newsletter, notify, settings, subscribers, workflow
from copydesk.db import db
from copydesk.models import Article, Group, GroupMembership, User
from copydesk.utils import utcnow


def make_user(*, email='reader@example.edu', username='reader', verified=True, permissions=()):
    user = User(email=email, username=username, display_name=username.title(),
                password_hash=auth.hash_password('boundary-password-123'),
                email_verified_at=utcnow() if verified else None)
    db.session.add(user)
    db.session.flush()
    if permissions:
        role = Group(slug=f'{username}-role', name=f'{username} role', permissions=list(permissions))
        db.session.add(role)
        db.session.flush()
        db.session.add(GroupMembership(user=user, group=role, status='active'))
    db.session.commit()
    return user.id


def sign_in(client, app, user_id):
    with app.app_context():
        version = db.session.get(User, user_id).auth_version
    with client.session_transaction() as session:
        session['uid'] = user_id
        session['uv'] = version
        session['_csrf'] = 'boundary-csrf'


def test_group_manager_cannot_open_administrator_group(client, app):
    with app.app_context():
        manager = make_user(username='manager', permissions=['manage_groups'])
        admins = db.session.scalar(select(Group).where(Group.slug == 'admins'))
        group_id = admins.id
    sign_in(client, app, manager)
    response = client.post(f'/dashboard/groups/{group_id}/', data={
        'csrf_token': 'boundary-csrf', 'name': 'Administrators', 'join_policy': 'open',
        'auto_join_domains': 'example.edu',
    })
    assert response.status_code == 403
    with app.app_context():
        assert db.session.get(Group, group_id).join_policy == 'closed'


def test_admin_group_never_allows_self_join(app):
    with app.app_context():
        user = db.session.get(User, make_user())
        admins = db.session.scalar(select(Group).where(Group.slug == 'admins'))
        admins.join_policy = 'open'
        with pytest.raises(groups.GroupError):
            groups.request(user, admins)


def test_unverified_account_cannot_control_existing_subscription(client, app):
    with app.app_context():
        subscribers.subscribe('reader@example.edu', confirmed=True, welcome=False)
        user_id = make_user(verified=False)
    sign_in(client, app, user_id)
    response = client.post('/account/newsletter', data={
        'csrf_token': 'boundary-csrf', 'action': 'unsubscribe',
    })
    assert response.status_code in (302, 403)
    with app.app_context():
        assert subscribers.find('reader@example.edu').status == 'active'


def test_unverified_group_member_cannot_read_group_content(client, app):
    with app.app_context():
        user_id = make_user(verified=False)
        group = db.session.scalar(select(Group).where(Group.slug == 'alumni'))
        db.session.add(GroupMembership(user_id=user_id, group=group, status='active'))
        article = Article(slug='group-secret', title='Group article', body='PRIVATE_GROUP_BODY',
                          status='published', published_at=utcnow(), visibility='groups', visibility_groups=[group])
        db.session.add(article)
        db.session.commit()
    sign_in(client, app, user_id)
    response = client.get('/articles/group-secret/')
    assert 'PRIVATE_GROUP_BODY' not in response.get_data(as_text=True)


def test_private_article_body_is_not_used_as_public_excerpt(client, app):
    with app.app_context():
        article = Article(slug='private-body', title='Members article', body='PRIVATE_BODY_DO_NOT_EXPOSE',
                          visibility='members', status='published', published_at=utcnow())
        db.session.add(article)
        db.session.commit()
    for path in ('/', '/articles/', '/articles/private-body/'):
        assert 'PRIVATE_BODY_DO_NOT_EXPOSE' not in client.get(path).get_data(as_text=True).replace(
            'value="PRIVATE_BODY_DO_NOT_EXPOSE"', '').replace('“PRIVATE_BODY_DO_NOT_EXPOSE”', '')


def test_public_archive_does_not_republish_unpublished_article(client, app, admin):
    with app.app_context():
        article = workflow.create_draft(db.session.get(User, admin), 'Withdrawn from site')
        article.body = 'UNPUBLISHED_ARTICLE_BODY'
        article.status = 'accepted'
        issue = newsletter.create(db.session.get(User, admin), 'Archive')
        issue.published_at = utcnow()
        issue.publish_articles = False
        item = newsletter.add_article(issue, article)
        item.display = 'full'
        db.session.commit()
        number = issue.number
    response = client.get(f'/issues/{number}/')
    assert response.status_code == 200
    assert 'UNPUBLISHED_ARTICLE_BODY' not in response.get_data(as_text=True)


def test_revoked_writer_cannot_submit_existing_draft(client, app):
    with app.app_context():
        user_id = make_user()
        writer = db.session.get(User, user_id)
        article = workflow.create_draft(writer, 'Draft created before revocation')
        article.body = 'A useful sentence with several words. ' * 10
        members = db.session.scalar(select(Group).where(Group.slug == 'members'))
        members.permissions = ['read_members']
        db.session.commit()
        article_id = article.id
    sign_in(client, app, user_id)
    client.post(f'/write/{article_id}/action', data={
        'csrf_token': 'boundary-csrf', 'action': 'submit',
    })
    with app.app_context():
        assert db.session.get(Article, article_id).status == 'draft'


def test_email_change_token_cannot_be_replayed(client, app, admin):
    with app.app_context():
        user = db.session.get(User, admin)
        notify.change_email(user, 'changed@example.edu')
        from copydesk.models import Job

        job = db.session.scalar(select(Job).where(Job.kind == 'mail.send').order_by(Job.id.desc()))
        import re

        token = re.search(r'/account/confirm-email/([^\s<>\"]+)', job.payload['text']).group(1)
        db.session.commit()
    assert client.get(f'/account/confirm-email/{token}').status_code == 302
    assert client.get(f'/account/confirm-email/{token}').status_code == 400


def test_closed_submissions_cannot_be_bypassed_via_status_action(client, app):
    with app.app_context():
        user_id = make_user()
        article = workflow.create_draft(db.session.get(User, user_id), 'Before submissions closed')
        article.body = 'Enough words for this submission. ' * 10
        settings.set_value('submissions.enabled', False)
        db.session.commit()
        article_id = article.id
    sign_in(client, app, user_id)
    client.post(f'/write/{article_id}/action', data={'csrf_token': 'boundary-csrf', 'action': 'submit'})
    with app.app_context():
        assert db.session.get(Article, article_id).status == 'draft'


def test_old_confirmation_does_not_resubscribe_after_unsubscribe(client, app):
    with app.app_context():
        sub, _ = subscribers.subscribe('reader@example.edu')
        token = auth.make_token('confirm-subscription', {'sid': sub.id, 'email': sub.email})
        secret = sub.token
        db.session.commit()
    assert client.get(f'/subscribe/confirm/{token}').status_code == 200
    client.post(f'/unsubscribe/{secret}', data={'List-Unsubscribe': 'One-Click'})
    assert client.get(f'/subscribe/confirm/{token}').status_code == 400
    with app.app_context():
        assert subscribers.find('reader@example.edu').status == 'unsubscribed'


def test_targeted_article_is_absent_from_public_issue_metadata(client, app, admin):
    with app.app_context():
        article = workflow.create_draft(db.session.get(User, admin), 'Targeted article title')
        article.status = 'published'
        article.published_at = utcnow()
        issue = newsletter.create(db.session.get(User, admin), 'Mixed audience')
        issue.published_at = utcnow()
        item = newsletter.add_article(issue, article)
        item.audience_groups = [db.session.scalar(select(Group).where(Group.slug == 'alumni'))]
        db.session.commit()
        number = issue.number
    assert 'Targeted article title' not in client.get('/').get_data(as_text=True).split('Recent articles')[0]
    response = client.get(f'/api/v1/issues/{number}')
    assert 'Targeted article title' not in response.get_data(as_text=True)


def test_authenticated_api_response_is_not_publicly_cacheable(editor_client):
    response = editor_client.get('/api/v1/articles')
    assert response.status_code == 200
    assert 'public' not in response.headers['Cache-Control']
    assert 'no-store' in response.headers['Cache-Control']


def test_internal_comment_is_not_emailed_to_former_reviewer(app, admin):
    from copydesk.models import Job

    with app.app_context():
        former_id = make_user(username='former')
        former = db.session.get(User, former_id)
        article = workflow.create_draft(db.session.get(User, admin), 'Editorial privacy')
        article.status = 'submitted'
        article.assigned_editor = former
        workflow.comment(article, db.session.get(User, admin), 'INTERNAL_EDITOR_NOTE', internal=True)
        db.session.flush()
        assert not db.session.scalars(select(Job).where(Job.kind == 'mail.send')).all()


@pytest.mark.parametrize('payload', [[], ['bad'], 7, 'bad', {'email': []}, {'email': {}}, {'email': 'x@example.edu', 'groups': [{}]}])
def test_invalid_api_payload_is_rejected_without_500(client, payload):
    response = client.post('/api/v1/subscribe', json=payload)
    assert response.status_code == 400


def test_writer_and_editor_can_exchange_comments(client, editor_client, app, admin):
    from copydesk.models import ArticleEvent, Job

    with app.app_context():
        writer_id = make_user()
        article = workflow.create_draft(db.session.get(User, writer_id), 'Conversation')
        article.status = 'submitted'
        article.assigned_editor_id = admin
        db.session.commit()
        article_id = article.id
    sign_in(client, app, writer_id)
    assert client.post(f'/write/{article_id}/comment', data={
        'csrf_token': 'boundary-csrf', 'body': 'Thank you for reviewing.',
    }).status_code == 302
    sign_in(client, app, admin)
    assert client.post(f'/dashboard/articles/{article_id}/comment', data={
        'csrf_token': 'boundary-csrf', 'body': 'Please add an example.',
    }).status_code == 302
    with app.app_context():
        assert len(db.session.scalars(select(ArticleEvent).where(ArticleEvent.kind == 'comment')).all()) == 2
        assert len(db.session.scalars(select(Job).where(Job.kind == 'mail.send')).all()) == 2


def test_search_does_not_match_restricted_body_for_anonymous_readers(client, app):
    with app.app_context():
        article = Article(slug='private-search', title='Visible title', body='HIDDEN_SEARCH_PHRASE',
                          visibility='members', status='published', published_at=utcnow())
        db.session.add(article)
        db.session.commit()
    assert 'Visible title' not in client.get('/search?q=HIDDEN_SEARCH_PHRASE').get_data(as_text=True)


def test_email_change_revokes_old_session_and_old_subscription_token(client, app, admin):
    import re

    from copydesk.models import Job

    with app.app_context():
        user = db.session.get(User, admin)
        sub, _ = subscribers.subscribe(user.email, user=user, confirmed=True, welcome=False)
        old_token = sub.token
        notify.change_email(user, 'new-owner-address@example.edu')
        job = db.session.scalar(select(Job).where(Job.kind == 'mail.send'))
        token = re.search(r'/account/confirm-email/([^\s<>\"]+)', job.payload['text']).group(1)
        db.session.commit()
    sign_in(client, app, admin)
    other = app.test_client()
    assert other.get(f'/account/confirm-email/{token}').status_code == 302
    assert client.get('/dashboard/').status_code == 302
    assert other.get(f'/subscription/{old_token}').status_code == 404
    with app.app_context():
        assert subscribers.find('new-owner-address@example.edu').user_id == admin


def test_non_admin_cannot_make_higher_privilege_role_self_joinable(client, app):
    with app.app_context():
        manager_id = make_user(username='manager', permissions=['manage_groups'])
        role = Group(slug='powerful', name='Powerful', permissions=['manage_users'], join_policy='closed')
        db.session.add(role)
        db.session.commit()
        role_id = role.id
    sign_in(client, app, manager_id)
    assert client.post(f'/dashboard/groups/{role_id}/', data={
        'csrf_token': 'boundary-csrf', 'name': 'Powerful', 'join_policy': 'open',
    }).status_code == 403


@pytest.mark.parametrize('payload', [['bad'], True, {'body': ['bad']}])
def test_invalid_preview_payload_returns_400(editor_client, payload):
    response = editor_client.post('/write/preview', json=payload, headers={'X-CSRF-Token': 'test-csrf'})
    assert response.status_code == 400


def test_invitation_verification_applies_membership_rules_and_subscription(client, app):
    with app.app_context():
        user_id = make_user(verified=False)
        user = db.session.get(User, user_id)
        group = db.session.scalar(select(Group).where(Group.slug == 'students'))
        group.auto_join_domains = ['example.edu']
        subscribers.subscribe(user.email, user=user, send_confirmation=False)
        token = auth.make_token('invite', {'uid': user_id, 'ph': user.password_hash[-16:]})
        db.session.commit()
    client.get(f'/reset-password/{token}')
    with client.session_transaction() as session:
        csrf = session['_csrf']
    response = client.post(f'/reset-password/{token}', data={
        'csrf_token': csrf, 'password': 'brand-new-password-123', 'confirm': 'brand-new-password-123',
    })
    assert response.status_code == 302
    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.is_verified
        assert user.in_group('students')
        assert subscribers.find(user.email).status == 'active'


def test_limited_member_manager_cannot_modify_administrator(client, app, admin):
    with app.app_context():
        manager_id = make_user(username='manager', permissions=['manage_users'])
    sign_in(client, app, manager_id)
    assert client.post(f'/dashboard/members/{admin}/', data={
        'csrf_token': 'boundary-csrf', 'action': 'unblock',
    }).status_code == 403


def test_unverified_administrator_does_not_replace_last_confirmed_admin(app, admin):
    with app.app_context():
        invited_id = make_user(username='invited', verified=False)
        admin_group = db.session.scalar(select(Group).where(Group.slug == 'admins'))
        db.session.add(GroupMembership(user_id=invited_id, group=admin_group, status='active'))
        db.session.commit()
        assert groups.last_admin(db.session.get(User, admin))


def test_api_accepts_repeated_form_groups(client, app):
    from werkzeug.datastructures import MultiDict

    response = client.post('/api/v1/subscribe', data=MultiDict([
        ('email', 'multi@example.edu'), ('groups', 'alumni'), ('groups', 'students'),
    ]))
    assert response.status_code == 202
    with app.app_context():
        assert {g.slug for g in subscribers.find('multi@example.edu').groups} == {'alumni', 'students'}
