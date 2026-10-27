"""Business contacts stay in a private inbox until staff choose what to publish."""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import urlsplit

from copydesk import audit, notify, workflow
from copydesk.auth import make_token
from copydesk.helpers import external_url
from copydesk.models import BusinessInquiry, User
from copydesk.utils import is_valid_email, normalize_email, utcnow

KINDS = [
    ("internship", "Internship"), ("job", "Job opening"),
    ("project", "Student project or workforce challenge"), ("mentorship", "Mentorship"),
    ("event", "Workshop, visit, or event"), ("partnership", "Partnership conversation"),
    ("feedback", "Feedback on the newsletter"), ("other", "Other"),
]
STATUSES = [("new", "New"), ("reviewing", "In review"), ("follow_up", "Following up"),
            ("approved", "Approved for editorial use"), ("closed", "Closed")]


def validate(form) -> tuple[dict, dict[str, str]]:
    values, errors = {}, {}
    limits = {"organization": 160, "contact_name": 100, "title": 200,
              "description": 12000, "skills": 500, "location": 200, "compensation": 200}
    for key, limit in limits.items():
        value = (form.get(key) or "").strip()
        if key != "description":
            value = " ".join(value.split())
        values[key] = value
        if len(value) > limit:
            errors[key] = f"Use {limit:,} characters or fewer."
        elif key in ("organization", "contact_name", "title", "description") and not value:
            errors[key] = "Fill in this field."
    values["email"] = normalize_email(form.get("email"))
    if not is_valid_email(values["email"]) or len(values["email"]) > 254:
        errors["email"] = "Enter a valid contact email address."
    values["kind"] = form.get("kind", "")
    if values["kind"] not in dict(KINDS):
        errors["kind"] = "Choose the kind of opportunity or request."
    for key in ("website_url", "application_url"):
        value = (form.get(key) or "").strip()
        values[key] = value
        try:
            parsed = urlsplit(value)
            valid = (parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username
                     and not parsed.password and not re.search(r"[\x00-\x20\x7f]", value))
        except ValueError:
            valid = False
        if value and (not valid or len(value) > 2048):
            errors[key] = "Enter a complete http:// or https:// website address."
    values["deadline"] = None
    if form.get("deadline"):
        try:
            values["deadline"] = date.fromisoformat(form["deadline"])
        except ValueError:
            errors["deadline"] = "Enter a valid date."
    values["share_contact"] = bool(form.get("share_contact"))
    if not form.get("consent"):
        errors["consent"] = "Please agree that the editors may contact you about this request."
    return values, errors


def request_confirmation(inquiry: BusinessInquiry) -> None:
    token = make_token("confirm-business", {"id": inquiry.id, "email": inquiry.email})
    notify.send("business_confirm", inquiry.email, f"Confirm your business inquiry for {notify.site_name()}",
                inquiry=inquiry, url=external_url("business.confirm", token=token))


def confirm(inquiry: BusinessInquiry) -> None:
    if inquiry.verified_at is not None:
        return
    inquiry.verified_at = utcnow()
    if inquiry.status == "unverified":
        inquiry.status = "new"
    inquiry.updated_at = utcnow()
    audit.record("business.confirmed", inquiry, label=f"Business inquiry #{inquiry.id}")


def create_article(inquiry: BusinessInquiry, actor: User):
    if inquiry.verified_at is None or inquiry.status != "approved":
        raise ValueError("Confirm the contact email and approve the request before creating a draft.")
    if inquiry.article is not None:
        return inquiry.article
    title = f"{inquiry.organization}: {inquiry.title}"[:200]
    article = workflow.create_draft(actor, title)
    lines = [f"**Organization:** {inquiry.organization}", f"**Opportunity:** {dict(KINDS)[inquiry.kind]}",
             "", inquiry.description]
    for label, value in [("Skills", inquiry.skills), ("Location / format", inquiry.location),
                         ("Compensation", inquiry.compensation), ("Deadline", inquiry.deadline),
                         ("Learn more / apply", inquiry.application_url or inquiry.website_url)]:
        if value:
            lines += ["", f"**{label}:** {value}"]
    if inquiry.share_contact:
        lines += ["", f"**Contact:** {inquiry.email}"]
    workflow.save(article, actor, title=title, subtitle="", body="\n".join(lines),
                  summary="Created from a reviewed business inquiry.")
    article.excerpt = inquiry.description[:300]
    inquiry.article = article
    inquiry.updated_at = utcnow()
    audit.record("business.draft_created", inquiry, actor=actor, label=f"Business inquiry #{inquiry.id}")
    return article
