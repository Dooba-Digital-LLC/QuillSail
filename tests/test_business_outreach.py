from sqlalchemy import func, select

from copydesk import business, settings
from copydesk.auth import make_token
from copydesk.db import db
from copydesk.models import Article, BusinessInquiry, Delivery, Job, Subscriber, User


def enable(app, client):
    with app.app_context():
        settings.set_value("business.enabled", True)
        db.session.commit()
    with client.session_transaction() as session:
        session["_csrf"] = "outreach-csrf"


def submit(app, client, **overrides):
    enable(app, client)
    data = {"csrf_token": "outreach-csrf", "organization": "Sample Business",
            "contact_name": "Private Contact", "email": "private@example.test",
            "kind": "project", "title": "Student inventory project", "description": "Build a small inventory dashboard.",
            "skills": "Python", "compensation": "Paid project; terms to be agreed", "consent": "1"}
    data.update(overrides)
    return client.post("/business/", data=data)


def inquiry_id(app):
    with app.app_context():
        return db.session.scalar(select(BusinessInquiry.id))


def token_for(app, item_id):
    with app.app_context():
        item = db.session.get(BusinessInquiry, item_id)
        return make_token("confirm-business", {"id": item.id, "email": item.email})


def test_intake_is_opt_in_and_csrf_protected(app, client):
    assert client.get("/business/").status_code == 404
    enable(app, client)
    assert client.get("/business/").status_code == 200
    assert client.post("/business/", data={}).status_code == 400
    assert b"For businesses" in client.get("/").data


def test_contact_confirmation_is_queued_and_requires_a_post(app, client):
    response = submit(app, client)
    assert response.status_code == 302
    item_id = inquiry_id(app)
    with app.app_context():
        item = db.session.get(BusinessInquiry, item_id)
        assert item.status == "unverified" and item.verified_at is None
        job = db.session.scalar(select(Job).where(Job.kind == "mail.send"))
        assert "Confirm your business inquiry" in job.payload["html"]
        assert "http://localhost/business/confirm/" in job.payload["text"]
        assert db.session.scalar(select(func.count()).select_from(Subscriber)) == 0
        assert db.session.scalar(select(func.count()).select_from(Delivery)) == 0
    path = "/business/confirm/" + token_for(app, item_id)
    assert client.get(path).status_code == 200
    with app.app_context():
        assert db.session.get(BusinessInquiry, item_id).verified_at is None
    assert client.post(path).status_code == 400
    assert client.post(path, data={"csrf_token": "outreach-csrf"}).status_code == 302
    assert client.post(path, data={"csrf_token": "outreach-csrf"}).status_code == 302
    assert client.get(path + "tampered").status_code == 404
    with app.app_context():
        item = db.session.get(BusinessInquiry, item_id)
        assert item.status == "new" and item.verified_at is not None


def test_private_inbox_and_editorial_handoff(app, client, editor_client, admin):
    assert submit(app, editor_client).status_code == 302
    item_id = inquiry_id(app)
    path = f"/dashboard/business/{item_id}/"
    assert editor_client.post(path, data={"csrf_token": "outreach-csrf", "status": "approved"}).status_code == 422
    token = token_for(app, item_id)
    client.post("/business/confirm/" + token, data={"csrf_token": "outreach-csrf"})
    response = editor_client.post(path, data={"csrf_token": "outreach-csrf", "status": "approved", "staff_notes": "CONFIDENTIAL-STAFF-NOTE"})
    assert response.status_code == 302
    assert b"Reply by email" in editor_client.get(path).data
    response = editor_client.post(path, data={"csrf_token": "outreach-csrf", "action": "draft"})
    assert response.status_code == 302
    assert editor_client.post(path, data={"csrf_token": "outreach-csrf", "action": "draft"}).status_code == 302
    with app.app_context():
        item = db.session.get(BusinessInquiry, item_id)
        article = item.article
        assert article.status == "draft" and article.published_at is None
        assert "inventory dashboard" in article.body
        assert "private@example.test" not in article.body
        assert "Private Contact" not in article.body
        assert "CONFIDENTIAL-STAFF-NOTE" not in article.body
        assert article.word_count > 0
        assert db.session.scalar(select(func.count()).select_from(Article)) == 1
    with client.session_transaction() as session:
        session.clear()
    assert client.get(path).status_code == 302
    assert b"Sample Business" not in client.get("/").data
    assert client.get("/business/1/").status_code == 404


def test_contact_email_only_copied_with_explicit_consent(app, admin):
    with app.app_context():
        from copydesk.utils import utcnow

        item = BusinessInquiry(organization="Example", contact_name="Contact", email="allowed@example.test",
                               kind="internship", title="Paid internship", description="A sample role.",
                               verified_at=utcnow(), status="approved", share_contact=True)
        db.session.add(item)
        db.session.flush()
        article = business.create_article(item, db.session.get(User, admin))
        assert "allowed@example.test" in article.body


def test_validation_and_honeypot_do_not_queue_mail(app, client):
    assert submit(app, client, application_url="javascript:alert(1)").status_code == 422
    assert submit(app, client, email="bad\nheader@example.test").status_code == 422
    assert submit(app, client, consent="").status_code == 422
    assert submit(app, client, deadline="not-a-date").status_code == 422
    assert submit(app, client, website="spam").status_code == 302
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(BusinessInquiry)) == 0
        assert db.session.scalar(select(func.count()).select_from(Job)) == 0


def test_unprivileged_member_cannot_read_or_change_business_inquiries(app, client):
    from copydesk.utils import utcnow

    submit(app, client)
    item_id = inquiry_id(app)
    with app.app_context():
        user = User(email="reader@example.test", username="reader", display_name="Reader", email_verified_at=utcnow())
        db.session.add(user)
        db.session.commit()
        uid, uv = user.id, user.auth_version
    with client.session_transaction() as session:
        session.update(uid=uid, uv=uv)
    assert client.get("/dashboard/business/").status_code == 403
    assert client.get(f"/dashboard/business/{item_id}/").status_code == 403
    assert client.post(f"/dashboard/business/{item_id}/", data={"csrf_token": "outreach-csrf", "action": "delete"}).status_code == 403


def test_outreach_permission_does_not_grant_settings_or_article_creation(app, client):
    from copydesk.models import Group, GroupMembership
    from copydesk.utils import utcnow

    submit(app, client)
    item_id = inquiry_id(app)
    with app.app_context():
        user = User(email="coordinator@example.test", username="coordinator", display_name="Coordinator", email_verified_at=utcnow())
        group = Group(slug="business-coordinators", name="Business coordinators", permissions=["manage_business"])
        members = db.session.scalar(select(Group).where(Group.slug == "members"))
        members.permissions = [key for key in members.permissions if key != "submit"]
        db.session.add_all([user, group])
        db.session.flush()
        db.session.add(GroupMembership(user_id=user.id, group_id=group.id, status="active"))
        item = db.session.get(BusinessInquiry, item_id)
        item.verified_at, item.status = utcnow(), "approved"
        db.session.commit()
        uid, uv = user.id, user.auth_version
    with client.session_transaction() as session:
        session.update(uid=uid, uv=uv)
    assert client.get("/dashboard/business/").status_code == 200
    assert client.get(f"/dashboard/business/{item_id}/").status_code == 200
    assert client.post("/dashboard/business/", data={"csrf_token": "outreach-csrf", "enabled": "1"}).status_code == 403
    assert client.post(f"/dashboard/business/{item_id}/", data={"csrf_token": "outreach-csrf", "action": "draft"}).status_code == 403
