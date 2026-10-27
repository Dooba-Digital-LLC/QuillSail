"""Webhooks: announce new issues, articles and more in Discord, Slack or any app.

Turn it on in copydesk.toml::

    [extensions]
    enabled = ["webhooks"]

    [extensions.webhooks]
    endpoints = [
      # Post to a Discord channel when an issue goes out or an article is published.
      { url = "https://discord.com/api/webhooks/...", format = "discord", events = ["issue.sent", "article.published"] },
      # Tell the editors' Slack channel about new submissions.
      { url = "https://hooks.slack.com/services/...", format = "slack", events = ["article.submitted"] },
      # Send JSON to your own service, signed with a shared secret.
      { url = "https://example.edu/hooks/copydesk", format = "json", secret = "change-me" },
    ]

Formats: "discord", "slack" or "json". Leave out ``events`` to get issue.sent
and article.published. JSON requests carry an ``X-Copydesk-Signature`` header,
"sha256=" plus the HMAC-SHA256 of the body, when a secret is set.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request
from typing import Any

from copydesk import __version__, jobs, settings
from copydesk.helpers import external_url
from copydesk.hooks import Extension
from copydesk.utils import join_names, utcnow

log = logging.getLogger("copydesk.extensions.webhooks")

extension = Extension(
    "webhooks",
    title="Webhooks",
    description="Announce new issues and articles in Discord, Slack or any app that accepts webhooks.",
    version="1.0.0",
    author="QuillSail contributors",
)

DEFAULT_EVENTS = ("issue.sent", "article.published")
SUPPORTED = ("issue.sent", "article.published", "article.submitted", "subscriber.confirmed",
             "membership.requested", "user.registered")


def _queue(event: str, text: str, data: dict[str, Any]) -> None:
    for endpoint in extension.option("endpoints", []) or []:
        if not isinstance(endpoint, dict) or not endpoint.get("url"):
            continue
        if event not in (endpoint.get("events") or DEFAULT_EVENTS):
            continue
        jobs.enqueue(
            "webhooks.deliver",
            {
                "url": endpoint["url"],
                "format": endpoint.get("format", "json"),
                "secret": endpoint.get("secret", ""),
                "event": event,
                "text": text,
                "data": data,
            },
            priority=jobs.PRIORITY_NORMAL,
            max_attempts=5,
        )


@extension.on("issue.sent")
def _issue_sent(issue, **_):
    url = external_url("public.issue", number=issue.number)
    site = settings.get("site.name")
    _queue("issue.sent", f"**{site} #{issue.number}** is out: {issue.display_title}\n{url}",
           {"number": issue.number, "title": issue.display_title, "url": url})


@extension.on("article.published")
def _article_published(article, **_):
    if article.visibility != "public":
        return
    url = external_url("public.article", slug=article.slug)
    authors = join_names([a.display_name for a in article.authors])
    _queue("article.published", f"New on {settings.get('site.name')}: **{article.display_title}** by {authors}\n{url}",
           {"title": article.display_title, "url": url, "authors": [a.display_name for a in article.authors]})


@extension.on("article.submitted")
def _article_submitted(article, **_):
    url = external_url("dash.article", article_id=article.id)
    authors = join_names([a.display_name for a in article.authors])
    _queue("article.submitted", f"New submission: **{article.display_title}** by {authors}\n{url}",
           {"title": article.display_title, "url": url, "authors": [a.display_name for a in article.authors]})


@extension.on("subscriber.confirmed")
def _subscriber_confirmed(subscriber, **_):
    groups = [g.name for g in subscriber.groups]
    note = f" ({', '.join(groups)})" if groups else ""
    _queue("subscriber.confirmed", f"New subscriber to {settings.get('site.name')}{note}", {"groups": groups})


@extension.on("membership.requested")
def _membership_requested(membership, **_):
    url = external_url("dash.requests")
    _queue("membership.requested",
           f"{membership.user.display_name} asked to join **{membership.group.name}**\n{url}",
           {"user": membership.user.display_name, "group": membership.group.name, "url": url})


@extension.on("user.registered")
def _user_registered(user, **_):
    _queue("user.registered", f"{user.display_name} created an account", {"user": user.display_name})


def build_body(fmt: str, event: str, text: str, data: dict[str, Any]) -> dict[str, Any]:
    site = settings.get("site.name")
    if fmt == "discord":
        return {"content": text[:2000], "username": site[:80], "allowed_mentions": {"parse": []}}
    if fmt == "slack":
        return {"text": text.replace("**", "*")}
    return {"event": event, "site": site, "sent_at": utcnow().isoformat() + "Z", "text": text, "data": data}


@extension.job("webhooks.deliver")
def deliver(payload: dict[str, Any]) -> None:
    body = json.dumps(build_body(payload.get("format", "json"), payload["event"], payload["text"],
                                 payload.get("data", {}))).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": f"QuillSail/{__version__} (+webhooks)"}
    if payload.get("secret"):
        digest = hmac.new(payload["secret"].encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers["X-Copydesk-Signature"] = f"sha256={digest}"
    request = urllib.request.Request(payload["url"], data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - admin-configured URL
            response.read(1024)
    except urllib.error.HTTPError as exc:
        if exc.code == 429 or exc.code >= 500:
            raise jobs.RetryLater(f"Webhook returned {exc.code}; will retry.", delay=120) from exc
        log.warning("Webhook to %s was rejected with HTTP %s; not retrying.", payload["url"], exc.code)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise jobs.RetryLater(f"Couldn't reach the webhook: {exc}", delay=120) from exc
