from datetime import timedelta
from urllib.parse import urlsplit

from sqlalchemy import select

from copydesk import issue_previews, newsletter, workflow
from copydesk.db import db
from copydesk.models import Group, Issue, IssuePreview, User
from copydesk.utils import utcnow


def create_preview(app, admin):
    with app.app_context():
        actor = db.session.get(User, admin)
        issue = newsletter.create(actor, "UNLISTED-WORKFORCE-EDITION")
        newsletter.add_block(issue, "text", "Employer spotlight", "Visible illustrative opportunity.")
        group_item = newsletter.add_block(issue, "text", "PRIVATE-GROUP-NOTE", "GROUP-SECRET-BODY")
        group_item.audience_groups = [db.session.scalar(select(Group).where(Group.is_audience.is_(True)))]
        article = workflow.create_draft(actor, "Draft public article")
        workflow.save(article, actor, title=article.title, subtitle="", body="SELECTED-DRAFT-BODY")
        newsletter.add_article(issue, article)
        restricted = workflow.create_draft(actor, "RESTRICTED-ARTICLE-TITLE")
        restricted.visibility = "members"
        restricted.body = "RESTRICTED-ARTICLE-BODY"
        newsletter.add_article(issue, restricted)
        url = issue_previews.create(issue, actor, "example")
        db.session.commit()
        return issue.id, urlsplit(url).path + "?" + urlsplit(url).query


def test_capability_is_required_and_only_selected_public_content_is_shared(app, client, admin):
    item_id, url = create_preview(app, admin)
    assert client.get("/issues/example").status_code == 404
    assert client.get("/issues/example?key=wrong").status_code == 404
    response = client.get(url)
    assert response.status_code == 200
    assert b"Visible illustrative opportunity" in response.data
    assert b"SELECTED-DRAFT-BODY" in response.data
    for secret in (b"PRIVATE-GROUP-NOTE", b"GROUP-SECRET-BODY", b"RESTRICTED-ARTICLE-TITLE", b"RESTRICTED-ARTICLE-BODY"):
        assert secret not in response.data
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "noindex" in response.headers["X-Robots-Tag"]
    for path in ("/", "/issues/", "/feed.xml", "/issues/feed.xml", "/sitemap.xml", "/search?q=workforce"):
        assert b"UNLISTED-WORKFORCE-EDITION" not in client.get(path).data, path
    with app.app_context():
        number = db.session.get(Issue, item_id).number
        link = db.session.scalar(select(IssuePreview))
        assert link.token_hash not in url
        assert db.session.get(Issue, item_id).status == "draft"
    assert client.get(f"/issues/{number}/").status_code == 404
    email = client.get(url + "&format=email")
    assert email.status_code == 200
    assert b"SELECTED-DRAFT-BODY" in email.data
    assert b"RESTRICTED-ARTICLE-BODY" not in email.data
    assert b"you have not been subscribed" in email.data
    assert b"You're getting this because" not in email.data


def test_expiry_rotation_and_revocation(app, client, admin):
    item_id, old_url = create_preview(app, admin)
    with app.app_context():
        url = issue_previews.create(db.session.get(Issue, item_id), db.session.get(User, admin), "example")
        db.session.commit()
    new_url = urlsplit(url).path + "?" + urlsplit(url).query
    assert client.get(old_url).status_code == 404
    assert client.get(new_url).status_code == 200
    with app.app_context():
        link = db.session.scalar(select(IssuePreview))
        link.expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
    assert client.get(new_url).status_code == 404
    with app.app_context():
        issue_previews.revoke(db.session.get(Issue, item_id), db.session.get(User, admin))
        db.session.commit()
        assert db.session.scalar(select(IssuePreview)) is None


def test_preview_controls_require_permission_and_csrf(app, client, editor_client, admin):
    item_id, _url = create_preview(app, admin)
    path = f"/dashboard/issues/{item_id}/share"
    assert editor_client.post(path, data={"slug": "example"}).status_code == 400
    response = editor_client.post(path, data={"csrf_token": "test-csrf", "slug": "example", "days": "7"})
    assert response.status_code == 200
    assert b"including the secret key" in response.data
    assert b"key=" in response.data
    assert client.get(f"/dashboard/issues/{item_id}/preview").status_code == 200
    assert editor_client.post(path, data={"csrf_token": "test-csrf", "action": "revoke"}).status_code == 302


def test_publishing_or_scheduling_disables_the_private_link(app, client, admin):
    item_id, url = create_preview(app, admin)
    with app.app_context():
        db.session.get(Issue, item_id).status = "scheduled"
        db.session.commit()
    assert client.get(url).status_code == 404


def test_reader_cannot_generate_or_revoke_a_preview(app, client, admin):
    item_id, url = create_preview(app, admin)
    with app.app_context():
        reader = User(email="preview-reader@example.test", username="preview-reader", display_name="Reader", email_verified_at=utcnow())
        db.session.add(reader)
        db.session.commit()
        uid, uv = reader.id, reader.auth_version
    with client.session_transaction() as session:
        session.update(uid=uid, uv=uv, _csrf="reader-csrf")
    path = f"/dashboard/issues/{item_id}/share"
    for data in ({"slug": "example"}, {"action": "revoke"}):
        assert client.post(path, data={"csrf_token": "reader-csrf", **data}).status_code == 403
    assert client.get(url).status_code == 200


def test_upgrade_from_previous_schema_preserves_issues(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    from copydesk import migrate

    engine = create_engine(f"sqlite:///{tmp_path}/upgrade.db")
    try:
        migrate.upgrade(engine, "0004")
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO settings (key, value, updated_at) VALUES ('upgrade-marker', '\"kept\"', CURRENT_TIMESTAMP)"))
        migrate.upgrade(engine)
        assert {"issue_previews", "business_inquiries"} <= set(inspect(engine).get_table_names())
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT value FROM settings WHERE key = 'upgrade-marker'")) == '"kept"'
        assert migrate.current_revision(engine) == "0005"
    finally:
        engine.dispose()
