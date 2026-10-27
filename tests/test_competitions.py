from datetime import timedelta

import pytest
from sqlalchemy import func, select

from copydesk import competitions as contests
from copydesk.db import db
from copydesk.models import (
    Competition,
    CompetitionEntry,
    CompetitionJudge,
    CompetitionScore,
    CompetitionVote,
    Group,
    GroupMembership,
    User,
)
from copydesk.utils import utcnow
from copydesk.views.account import anonymize


@pytest.fixture
def people(app):
    with app.app_context():
        rows = [
            User(
                email=f"{name}@example.test",
                username=name,
                display_name=name.capitalize(),
                email_verified_at=utcnow(),
            )
            for name in ("alice", "bob", "judge", "reader", "other")
        ]
        db.session.add_all(rows)
        db.session.commit()
        return {row.username: row.id for row in rows}


def signed(app, user_id, *, verified=True):
    client = app.test_client()
    with app.app_context():
        user = db.session.get(User, user_id)
        if not verified:
            user.email_verified_at = None
            db.session.commit()
        version = user.auth_version
    with client.session_transaction() as session:
        session["uid"], session["uv"], session["_csrf"] = user_id, version, "test-csrf"
    return client


def post(client, path, **data):
    return client.post(path, data={"csrf_token": "test-csrf", **data})


def create(client, app, mode="both", judges=()):
    response = post(
        client,
        "/dashboard/competitions/new",
        title="Community challenge",
        description="Build something useful.",
        rules="Original work. Quality and usefulness.",
        mode=mode,
        judge_ids=list(judges),
    )
    assert response.status_code == 302
    with app.app_context():
        return db.session.scalar(select(Competition.id).order_by(Competition.id.desc()).limit(1))


def phase(client, competition_id, action):
    return post(client, f"/dashboard/competitions/{competition_id}/phase", action=action)


def enter(client, app, competition_id, title):
    response = post(
        client,
        f"/competitions/{competition_id}/enter",
        title=title,
        body=f"Private entry body {title}",
        project_url="https://example.test/project",
    )
    assert response.status_code == 302
    with app.app_context():
        return db.session.scalar(select(CompetitionEntry.id).where(CompetitionEntry.title == title))


def moderate(client, competition_id, entry_id, decision="approved"):
    return post(
        client,
        f"/dashboard/competitions/{competition_id}/entries/{entry_id}/moderate",
        decision=decision,
        moderation_note="Private organizer feedback",
    )


@pytest.mark.parametrize("mode", ["judges", "readers", "both"])
def test_complete_hosting_entry_judging_voting_and_result_flow(app, editor_client, people, mode):
    competition_id = create(editor_client, app, mode, [people["judge"]])
    alice, bob, judge, reader = [
        signed(app, people[name]) for name in ("alice", "bob", "judge", "reader")
    ]
    public = app.test_client()
    assert public.get(f"/competitions/{competition_id}/").status_code == 404
    assert editor_client.get(f"/dashboard/competitions/{competition_id}/edit").status_code == 200
    assert editor_client.get(f"/competitions/{competition_id}/").status_code == 200
    assert b"Community challenge" not in public.get("/competitions/").data
    phase(editor_client, competition_id, "open")
    assert alice.get(f"/competitions/{competition_id}/enter").status_code == 200
    a = enter(alice, app, competition_id, "Alice project")
    b = enter(bob, app, competition_id, "Bob project")
    assert public.get(f"/competitions/{competition_id}/entries/{a}/").status_code == 404
    assert judge.get(f"/competitions/{competition_id}/entries/{a}/").status_code == 404
    moderate(editor_client, competition_id, a)
    moderate(editor_client, competition_id, b)
    admin_page = editor_client.get(f"/dashboard/competitions/{competition_id}/").get_data(as_text=True)
    assert f'id="f-moderation_note_{a}"' in admin_page
    assert f'id="f-moderation_note_{b}"' in admin_page
    assert b"Alice project" not in public.get(f"/competitions/{competition_id}/").data
    assert post(reader, f"/competitions/{competition_id}/vote", entry_id=a).status_code == 422
    phase(editor_client, competition_id, "judge")
    assert public.get(f"/competitions/{competition_id}/entries/{a}/").status_code == 200
    assert (
        b"Private organizer feedback"
        not in public.get(f"/competitions/{competition_id}/entries/{a}/").data
    )
    assert (
        b"Private organizer feedback"
        in alice.get(f"/competitions/{competition_id}/entries/{a}/").data
    )
    assert (
        post(
            alice, f"/competitions/{competition_id}/enter", title="Changed", body="Changed"
        ).status_code
        == 422
    )
    if mode != "readers":
        for entry_id, value in ((a, 90), (b, 80)):
            assert (
                post(
                    judge,
                    f"/competitions/{competition_id}/entries/{entry_id}/score",
                    score=value,
                    notes="SECRET judge notes",
                ).status_code
                == 302
            )
        assert (
            b"SECRET judge notes"
            not in public.get(f"/competitions/{competition_id}/entries/{a}/").data
        )
        assert (
            b"SECRET judge notes"
            not in alice.get(f"/competitions/{competition_id}/entries/{a}/").data
        )
    else:
        assert post(
            judge, f"/competitions/{competition_id}/entries/{a}/score", score=100
        ).status_code in (403, 422)
    if mode != "judges":
        assert post(alice, f"/competitions/{competition_id}/vote", entry_id=a).status_code == 422
        assert post(reader, f"/competitions/{competition_id}/vote", entry_id=a).status_code == 302
        assert post(reader, f"/competitions/{competition_id}/vote", entry_id=b).status_code == 302
        with app.app_context():
            ballots = db.session.scalars(
                select(CompetitionVote).where(CompetitionVote.competition_id == competition_id)
            ).all()
            assert len(ballots) == 1 and ballots[0].entry_id == b
        assert (
            post(reader, f"/competitions/{competition_id}/vote", action="remove").status_code == 302
        )
        with app.app_context():
            assert db.session.scalar(select(func.count()).select_from(CompetitionVote)) == 0
        post(reader, f"/competitions/{competition_id}/vote", entry_id=b)
    else:
        assert post(reader, f"/competitions/{competition_id}/vote", entry_id=a).status_code == 422
    before = public.get(f"/competitions/{competition_id}/").get_data(as_text=True)
    assert "Judges’ Choice</h3>" not in before and "Provisional standings" not in before
    phase(editor_client, competition_id, "close")
    assert post(reader, f"/competitions/{competition_id}/vote", entry_id=a).status_code == 422
    assert post(
        judge, f"/competitions/{competition_id}/entries/{a}/score", score=50
    ).status_code in (403, 422)
    phase(editor_client, competition_id, "publish")
    with app.app_context():
        competition = db.session.get(Competition, competition_id)
        assert competition.status == "results"
        snapshot = competition.results
        if mode != "readers":
            assert snapshot["judges"][0]["entry_id"] == a
        if mode != "judges":
            assert snapshot["readers"][0]["entry_id"] == b
        db.session.get(User, people["reader"]).is_active = False
        db.session.commit()
        assert contests.standings(competition)[1]["votes"] == (1 if mode != "judges" else 0)
    result = public.get(f"/competitions/{competition_id}/").get_data(as_text=True)
    assert "Results published" in result and "SECRET judge notes" not in result
    moderate(editor_client, competition_id, a, "rejected")
    with app.app_context():
        assert db.session.get(CompetitionEntry, a).status == "approved"
        assert db.session.get(Competition, competition_id).results == snapshot


def test_permissions_verification_csrf_and_assigned_judge_access(
    app, client, editor_client, people
):
    competition_id = create(editor_client, app, judges=[people["judge"]])
    alice = signed(app, people["alice"])
    unverified = signed(app, people["other"], verified=False)
    assert alice.get("/dashboard/competitions/").status_code == 403
    assert post(alice, "/dashboard/competitions/new", title="Forbidden").status_code == 403
    assert (
        editor_client.post(
            f"/dashboard/competitions/{competition_id}/phase", data={"action": "open"}
        ).status_code
        == 400
    )
    phase(editor_client, competition_id, "open")
    assert app.test_client().get(f"/competitions/{competition_id}/enter").status_code == 302
    assert post(unverified, f"/competitions/{competition_id}/enter", title="No").location.endswith(
        "/verify-email"
    )
    a = enter(alice, app, competition_id, "Owned entry")
    moderate(editor_client, competition_id, a)
    phase(editor_client, competition_id, "judge")
    assert (
        post(alice, f"/competitions/{competition_id}/entries/{a}/score", score=100).status_code
        == 403
    )
    judge = signed(app, people["judge"])
    assert b"Judge this entry" in judge.get(f"/competitions/{competition_id}/entries/{a}/").data
    assert (
        post(judge, f"/competitions/{competition_id}/entries/{a}/score", score="NaN").status_code
        == 422
    )
    assert (
        post(judge, f"/competitions/{competition_id}/entries/{a}/score", score=101).status_code
        == 422
    )
    assert (
        post(judge, f"/competitions/{competition_id}/entries/{a}/score", score=80).status_code
        == 302
    )
    assert (
        post(judge, f"/competitions/{competition_id}/entries/{a}/score", score=85).status_code
        == 302
    )
    with app.app_context():
        scores = db.session.scalars(select(CompetitionScore)).all()
        assert len(scores) == 1 and scores[0].score == 85


def test_deadlines_are_enforced_without_background_jobs(app, editor_client, people):
    competition_id = create(editor_client, app, "both")
    phase(editor_client, competition_id, "open")
    alice, reader = signed(app, people["alice"]), signed(app, people["reader"])
    a = enter(alice, app, competition_id, "Timed entry")
    moderate(editor_client, competition_id, a)
    with app.app_context():
        item = db.session.get(Competition, competition_id)
        item.entries_close_at = utcnow() - timedelta(seconds=1)
        item.ends_at = utcnow() + timedelta(hours=1)
        db.session.commit()
    assert (
        post(alice, f"/competitions/{competition_id}/enter", title="Late", body="Late").status_code
        == 422
    )
    assert post(reader, f"/competitions/{competition_id}/vote", entry_id=a).status_code == 302
    with app.app_context():
        db.session.get(Competition, competition_id).ends_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
    assert post(reader, f"/competitions/{competition_id}/vote", action="remove").status_code == 422
    assert (
        post(
            editor_client, f"/competitions/{competition_id}/entries/{a}/score", score=90
        ).status_code
        == 422
    )


def test_edit_resets_approval_withdrawal_and_cross_competition_isolation(
    app, editor_client, people
):
    a_comp = create(editor_client, app)
    b_comp = create(editor_client, app)
    for comp in (a_comp, b_comp):
        phase(editor_client, comp, "open")
    alice, bob = signed(app, people["alice"]), signed(app, people["bob"])
    a = enter(alice, app, a_comp, "First entry")
    b = enter(bob, app, b_comp, "Other entry")
    moderate(editor_client, a_comp, a)
    assert (
        post(
            alice,
            f"/competitions/{a_comp}/enter",
            title="Edited",
            body="New body",
            project_url="https://example.test",
        ).status_code
        == 302
    )
    with app.app_context():
        assert db.session.get(CompetitionEntry, a).status == "pending"
        assert (
            db.session.scalar(
                select(func.count())
                .select_from(CompetitionEntry)
                .where(CompetitionEntry.competition_id == a_comp)
            )
            == 1
        )
    assert bob.get(f"/competitions/{a_comp}/entries/{a}/").status_code == 404
    assert post(bob, f"/competitions/{a_comp}/entries/{a}/withdraw").status_code == 404
    assert moderate(editor_client, a_comp, b).status_code == 404
    assert post(alice, f"/competitions/{a_comp}/entries/{a}/withdraw").status_code == 302
    moderate(editor_client, a_comp, a)
    with app.app_context():
        assert db.session.get(CompetitionEntry, a).status == "withdrawn"
    phase(editor_client, a_comp, "judge")
    assert post(bob, f"/competitions/{a_comp}/vote", entry_id=b).status_code == 404


def test_draft_validation_rule_locking_ties_and_required_scores(app, editor_client, people):
    invalid = post(
        editor_client,
        "/dashboard/competitions/new",
        title="",
        description="Brief",
        mode="unknown",
        judge_ids=["bad"],
    )
    assert invalid.status_code == 422
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(Competition)) == 0
    competition_id = create(editor_client, app, judges=[people["judge"], people["judge"]])
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(CompetitionJudge)) == 1
    phase(editor_client, competition_id, "open")
    response = post(
        editor_client,
        f"/dashboard/competitions/{competition_id}/edit",
        title="Changed",
        description="Changed",
        mode="readers",
    )
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(Competition, competition_id).mode == "both"
    a = enter(signed(app, people["alice"]), app, competition_id, "Tie A")
    b = enter(signed(app, people["bob"]), app, competition_id, "Tie B")
    for entry_id in (a, b):
        moderate(editor_client, competition_id, entry_id)
    phase(editor_client, competition_id, "judge")
    phase(editor_client, competition_id, "close")
    phase(editor_client, competition_id, "publish")
    with app.app_context():
        item = db.session.get(Competition, competition_id)
        assert item.status == "closed"
        # Fixtures cover a tied final snapshot without reopening the live phase.
        for entry_id in (a, b):
            db.session.add(CompetitionScore(entry_id=entry_id, judge_id=people["judge"], score=90))
        db.session.commit()
    phase(editor_client, competition_id, "publish")
    with app.app_context():
        result = db.session.get(Competition, competition_id).results
        assert [row["rank"] for row in result["judges"]] == [1, 1]
        assert result["readers"] == []


def test_admin_cannot_judge_own_entry_and_invalid_links_are_rejected(app, editor_client):
    competition_id = create(editor_client, app)
    phase(editor_client, competition_id, "open")
    assert (
        post(
            editor_client,
            f"/competitions/{competition_id}/enter",
            title="Bad link",
            body="Description",
            project_url="javascript:alert(1)",
        ).status_code
        == 422
    )
    entry_id = enter(editor_client, app, competition_id, "Admin entry")
    moderate(editor_client, competition_id, entry_id)
    phase(editor_client, competition_id, "judge")
    assert (
        post(
            editor_client, f"/competitions/{competition_id}/entries/{entry_id}/score", score=100
        ).status_code
        == 422
    )


def test_delegated_host_and_account_export_deletion(app, editor_client, people):
    with app.app_context():
        role = Group(slug="hosts", name="Hosts", permissions=["manage_competitions"])
        db.session.add(role)
        db.session.flush()
        db.session.add(GroupMembership(user_id=people["other"], group_id=role.id))
        db.session.commit()
    host = signed(app, people["other"])
    assert host.get("/dashboard/competitions/").status_code == 200
    assert b"Dashboard" in host.get("/").data
    competition_id = create(host, app)
    phase(host, competition_id, "open")
    alice = signed(app, people["alice"])
    a = enter(alice, app, competition_id, "Private account data")
    exported = alice.get("/account/export").get_json()
    assert exported["competition_entries"][0]["title"] == "Private account data"
    with app.app_context():
        user = db.session.get(User, people["alice"])
        anonymize(user, actor=user)
        db.session.commit()
        entry = db.session.get(CompetitionEntry, a)
        assert entry.status == "withdrawn" and not entry.body and not entry.project_url
    assert alice.get(f"/competitions/{competition_id}/entries/{a}/").status_code == 404


def test_delete_draft_only(app, editor_client):
    competition_id = create(editor_client, app)
    phase(editor_client, competition_id, "delete")
    with app.app_context():
        assert db.session.get(Competition, competition_id) is None
    competition_id = create(editor_client, app)
    phase(editor_client, competition_id, "open")
    phase(editor_client, competition_id, "delete")
    with app.app_context():
        assert db.session.get(Competition, competition_id) is not None


def test_concurrent_votes_still_create_one_ballot(app, editor_client, people):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    competition_id = create(editor_client, app, "readers")
    phase(editor_client, competition_id, "open")
    entries = [
        enter(signed(app, people[name]), app, competition_id, name) for name in ("alice", "bob")
    ]
    for entry_id in entries:
        moderate(editor_client, competition_id, entry_id)
    phase(editor_client, competition_id, "judge")
    clients = [signed(app, people["reader"]), signed(app, people["reader"])]
    barrier = Barrier(2)

    def vote(index):
        barrier.wait(timeout=5)
        return post(
            clients[index], f"/competitions/{competition_id}/vote", entry_id=entries[index]
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(vote, range(2))) == [302, 302]
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(CompetitionVote)) == 1


def test_upgrade_preserves_existing_email_configuration(tmp_path):
    from sqlalchemy import inspect

    from copydesk import migrate
    from copydesk.db import make_engine
    from copydesk.models import EmailConnection, Setting

    engine = make_engine(f"sqlite:///{tmp_path}/upgrade.db")
    try:
        migrate.upgrade(engine, "0002")
        with engine.begin() as connection:
            connection.execute(
                Setting.__table__.insert().values(
                    key="private.email.client.gmail", value="encrypted-client"
                )
            )
            connection.execute(
                EmailConnection.__table__.insert().values(
                    id=1, version=1, connection_id="unchanged", payload="encrypted-sender"
                )
            )
        migrate.upgrade(engine)
        with engine.connect() as connection:
            assert connection.scalar(select(Setting.value)) == "encrypted-client"
            assert connection.scalar(select(EmailConnection.payload)) == "encrypted-sender"
        assert "competition_votes" in inspect(engine).get_table_names()
        assert migrate.current_revision(engine) == migrate.head_revision()
    finally:
        engine.dispose()
