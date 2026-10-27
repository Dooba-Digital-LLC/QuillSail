"""Competition rules shared by public participation and administration."""

from decimal import Decimal
from urllib.parse import urlsplit

from flask import abort
from sqlalchemy import delete, func, select, update

from copydesk import permissions
from copydesk.db import db
from copydesk.models import (
    Competition,
    CompetitionEntry,
    CompetitionJudge,
    CompetitionScore,
    CompetitionVote,
    User,
)
from copydesk.utils import utcnow

MODES = {
    "judges": "Judges only",
    "readers": "Reader voting only",
    "both": "Judges and reader voting",
}
PHASES = {
    "draft": "Draft",
    "submissions": "Accepting entries",
    "judging": "Judging and voting",
    "closed": "Closed",
    "results": "Results published",
}


class CompetitionProblem(Exception):
    pass


def phase(competition):
    current = competition.status
    now = utcnow()
    if (
        current == "submissions"
        and competition.entries_close_at
        and now >= competition.entries_close_at
    ):
        current = "judging"
    if current in {"submissions", "judging"} and competition.ends_at and now >= competition.ends_at:
        current = "closed"
    return current


def lock(competition_id):
    # Every mutation locks the same parent row before checking phase/deadlines.
    # UPDATE also serializes writers on SQLite, where SELECT FOR UPDATE is ignored.
    changed = db.session.execute(
        update(Competition)
        .where(Competition.id == competition_id)
        .values(version=Competition.version + 1)
    )
    if changed.rowcount != 1:
        abort(404)
    return db.session.get(Competition, competition_id, populate_existing=True)


def is_judge(user, competition):
    if not user.is_authenticated or not user.is_verified or not user.is_active or user.is_deleted:
        return False
    return (
        permissions.is_admin(user)
        or db.session.scalar(
            select(CompetitionJudge.id).where(
                CompetitionJudge.competition_id == competition.id,
                CompetitionJudge.user_id == user.id,
            )
        )
        is not None
    )


def can_manage(user):
    return (
        user.is_authenticated
        and user.is_verified
        and user.is_active
        and not user.is_deleted
        and permissions.has(user, "manage_competitions")
    )


def entry_visible(user, competition, entry):
    return (
        can_manage(user)
        or (user.is_authenticated and entry.user_id == user.id)
        or (
            competition.status != "draft"
            and phase(competition) in {"judging", "closed", "results"}
            and entry.status == "approved"
        )
    )


def validate_entry(form):
    title = str(form.get("title") or "").strip()
    body = str(form.get("body") or "").strip()
    project_url = str(form.get("project_url") or "").strip()
    errors = {}
    if not 1 <= len(title) <= 200:
        errors["title"] = "Use a title between 1 and 200 characters."
    if not 1 <= len(body) <= 100000:
        errors["body"] = "Describe your entry using up to 100,000 characters."
    if project_url:
        try:
            url = urlsplit(project_url)
            if (
                len(project_url) > 2048
                or url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
                or any(c.isspace() for c in project_url)
            ):
                raise ValueError
        except ValueError:
            errors["project_url"] = (
                "Use a complete http:// or https:// link without a username or password."
            )
    return {"title": title, "body": body, "project_url": project_url}, errors


def standings(competition):
    entries = db.session.scalars(
        select(CompetitionEntry)
        .where(
            CompetitionEntry.competition_id == competition.id, CompetitionEntry.status == "approved"
        )
        .order_by(CompetitionEntry.id)
    ).all()
    ids = [entry.id for entry in entries]
    if competition.status == "results":
        judges = {row["entry_id"]: row for row in competition.results.get("judges", [])}
        readers = {row["entry_id"]: row for row in competition.results.get("readers", [])}
        return [
            {
                "entry": entry,
                "average": judges.get(entry.id, {}).get("value"),
                "score_count": judges.get(entry.id, {}).get("score_count", 0),
                "votes": readers.get(entry.id, {}).get("value", 0),
            }
            for entry in entries
        ]
    active = (
        User.is_active.is_(True),
        User.email_verified_at.is_not(None),
        User.deleted_at.is_(None),
    )
    scores = {
        row[0]: (row[1], row[2])
        for row in db.session.execute(
            select(CompetitionScore.entry_id, func.avg(CompetitionScore.score), func.count())
            .join(User, User.id == CompetitionScore.judge_id)
            .where(CompetitionScore.entry_id.in_(ids), *active)
            .group_by(CompetitionScore.entry_id)
        )
    }
    votes = dict(
        db.session.execute(
            select(CompetitionVote.entry_id, func.count())
            .join(User, User.id == CompetitionVote.user_id)
            .where(
                CompetitionVote.competition_id == competition.id,
                CompetitionVote.entry_id.in_(ids),
                *active,
            )
            .group_by(CompetitionVote.entry_id)
        ).all()
    )
    return [
        {
            "entry": entry,
            "average": round(float(scores[entry.id][0]), 2) if entry.id in scores else None,
            "score_count": scores.get(entry.id, (None, 0))[1],
            "votes": votes.get(entry.id, 0),
        }
        for entry in entries
    ]


def publish_results(competition):
    if phase(competition) != "closed":
        raise CompetitionProblem("Close judging and voting before publishing results.")
    rows = standings(competition)
    if not rows:
        raise CompetitionProblem("Approve at least one entry before publishing results.")
    if competition.mode in {"judges", "both"} and any(row["average"] is None for row in rows):
        raise CompetitionProblem(
            "Every approved entry needs at least one judge score before publishing results."
        )
    result = {}
    for key, metric in (("judges", "average"), ("readers", "votes")):
        if competition.mode not in {key, "both"}:
            continue
        ranked = sorted(
            (
                row
                for row in rows
                if row[metric] is not None and (key != "readers" or row[metric] > 0)
            ),
            key=lambda row: (-Decimal(str(row[metric])), row["entry"].id),
        )
        previous, rank = None, 0
        result[key] = []
        for position, row in enumerate(ranked, 1):
            if row[metric] != previous:
                rank = position
            previous = row[metric]
            result[key].append(
                {
                    "entry_id": row["entry"].id,
                    "rank": rank,
                    "value": row[metric],
                    "score_count": row["score_count"],
                }
            )
    competition.results = result
    competition.status = "results"
    competition.results_at = utcnow()


def erase_user_data(user):
    """Remove ballots, private notes and unpublished entries on account deletion."""
    affected = set(
        db.session.scalars(
            select(CompetitionEntry.competition_id)
            .where(CompetitionEntry.user_id == user.id)
            .order_by(CompetitionEntry.competition_id)
        ).all()
    )
    affected.update(
        db.session.scalars(
            select(CompetitionVote.competition_id).where(CompetitionVote.user_id == user.id)
        )
    )
    affected.update(
        db.session.scalars(
            select(CompetitionJudge.competition_id).where(CompetitionJudge.user_id == user.id)
        )
    )
    affected.update(
        db.session.scalars(
            select(CompetitionEntry.competition_id)
            .join(CompetitionScore, CompetitionScore.entry_id == CompetitionEntry.id)
            .where(CompetitionScore.judge_id == user.id)
        )
    )
    for competition_id in sorted(affected):
        competition = lock(competition_id)
        if competition.status != "results":
            for entry in db.session.scalars(
                select(CompetitionEntry).where(
                    CompetitionEntry.competition_id == competition_id,
                    CompetitionEntry.user_id == user.id,
                )
            ):
                entry.title, entry.body, entry.project_url, entry.moderation_note = (
                    "Withdrawn entry",
                    "",
                    "",
                    "",
                )
                entry.status = "withdrawn"
                db.session.execute(
                    delete(CompetitionScore).where(CompetitionScore.entry_id == entry.id)
                )
    db.session.execute(delete(CompetitionVote).where(CompetitionVote.user_id == user.id))
    db.session.execute(delete(CompetitionScore).where(CompetitionScore.judge_id == user.id))
    db.session.execute(delete(CompetitionJudge).where(CompetitionJudge.user_id == user.id))
