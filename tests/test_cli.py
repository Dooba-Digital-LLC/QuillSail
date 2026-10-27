import json
from datetime import date

from click.testing import CliRunner

from copydesk.cli import main


def test_install_demo_export_restore(tmp_path):
    runner = CliRunner()
    site = tmp_path / "site"
    result = runner.invoke(
        main,
        [
            "init",
            "--dir",
            str(site),
            "--site-name",
            "The Tech Stack",
            "--base-url",
            "http://localhost:5055",
            "--admin-name",
            "Editor",
            "--admin-email",
            "editor@example.edu",
            "--admin-password",
            "correct-horse-battery-123",
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    config = site / "copydesk.toml"
    assert config.stat().st_mode & 0o077 == 0
    args = ["--config", str(config)]
    result = runner.invoke(main, [*args, "demo", "--yes"])
    assert result.exit_code == 0, result.output
    from sqlalchemy import select

    from copydesk.app import create_app
    from copydesk.config import load_config
    from copydesk.db import db
    from copydesk.models import (
        Article,
        BusinessInquiry,
        Competition,
        CompetitionEntry,
        CompetitionJudge,
        CompetitionScore,
        CompetitionVote,
        Issue,
        IssuePreview,
        User,
    )

    with create_app(load_config(config)).app_context():
        users = db.session.scalars(select(User).order_by(User.id).limit(3)).all()
        blog_article = db.session.scalar(select(Article).order_by(Article.id).limit(1))
        blog_article.source_url = 'https://blog.example.test/source-post'
        blog_article.source_author = 'Original blogger'
        db.session.add(BusinessInquiry(organization="Sample business", contact_name="Private contact",
                                       email="private@example.test", kind="project", title="Backup opportunity",
                                       description="Sample project", deadline=date(2027, 1, 15),
                                       staff_notes="Private business follow-up", article_id=blog_article.id))
        issue = db.session.scalar(select(Issue).order_by(Issue.id).limit(1))
        from copydesk.utils import utcnow

        db.session.add(IssuePreview(issue_id=issue.id, slug="example", token_hash="a" * 64, expires_at=utcnow()))
        competition = Competition(title="Backup competition", mode="both", status="results")
        db.session.add(competition)
        db.session.flush()
        entry = CompetitionEntry(
            competition_id=competition.id,
            user_id=users[1].id,
            title="Backup project",
            body="Competition body",
            project_url="https://example.test/project",
            status="approved",
        )
        db.session.add(entry)
        db.session.flush()
        db.session.add(CompetitionJudge(competition_id=competition.id, user_id=users[0].id))
        db.session.add(
            CompetitionScore(
                entry_id=entry.id, judge_id=users[0].id, score=90, notes="Private notes"
            )
        )
        db.session.add(
            CompetitionVote(competition_id=competition.id, entry_id=entry.id, user_id=users[2].id)
        )
        competition.results = {
            "judges": [{"entry_id": entry.id, "rank": 1, "value": 90, "score_count": 1}],
            "readers": [{"entry_id": entry.id, "rank": 1, "value": 1}],
        }
        db.session.commit()
    backup = tmp_path / "backup.json"
    result = runner.invoke(main, [*args, "export", str(backup)])
    assert result.exit_code == 0, result.output
    original = json.loads(backup.read_text())
    restored = tmp_path / "restored"
    (restored / "data").mkdir(parents=True)
    restored_config = restored / "copydesk.toml"
    restored_config.write_text(config.read_text())
    result = runner.invoke(main, ["--config", str(restored_config), "import", str(backup), "--yes"])
    assert result.exit_code == 0, (result.output, result.exception)
    second = tmp_path / "restored.json"
    result = runner.invoke(main, ["--config", str(restored_config), "export", str(second)])
    assert result.exit_code == 0, result.output
    assert json.loads(second.read_text())["tables"] == original["tables"]
    result = runner.invoke(main, ["--config", str(restored_config), "import", str(backup), "--yes"])
    assert result.exit_code != 0
    assert "already has accounts" in result.output


def test_restore_rejects_existing_data_without_accounts(tmp_path):
    from copydesk import jobs, migrate
    from copydesk.app import create_app
    from copydesk.config import load_config
    from copydesk.db import db
    from copydesk.models import Job

    config = tmp_path / "copydesk.toml"
    config.write_text(
        '[site]\nsecret_key = "test-secret-key-at-least-32-characters"\n'
        '[database]\nurl = "sqlite:///test.db"\n'
    )
    app = create_app(load_config(config))
    with app.app_context():
        migrate.upgrade(db.engine)
        job = jobs.enqueue("existing-data", {"keep": True})
        db.session.commit()
        job_id = job.id
    backup = tmp_path / "backup.json"
    backup.write_text(json.dumps({"copydesk": "1.0.0", "tables": {}}))
    result = CliRunner().invoke(main, ["--config", str(config), "import", str(backup), "--yes"])
    assert result.exit_code != 0
    assert "already contains data" in result.output
    with app.app_context():
        assert db.session.get(Job, job_id).payload == {"keep": True}
    db.session.remove()
    db.engine.dispose()
