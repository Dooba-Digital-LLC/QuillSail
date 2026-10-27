"""Host-selected publication modes; changing mode preserves existing content."""

from urllib.parse import urlencode, urlsplit, urlunsplit

from copydesk import settings
from copydesk.helpers import external_url, state

MODES = {
    "newsletter": "Newsletter",
    "blog": "Blog",
    "hybrid": "Blog + newsletter",
}


def is_blog():
    return settings.get("site.mode") in {"blog", "hybrid"}


def has_newsletter():
    return settings.get("site.mode") != "blog"


def valid_web_url(value, *, base=False):
    try:
        url = urlsplit(value)
        if url.port is not None and not 1 <= url.port <= 65535:
            return False
        return bool(
            value
            and len(value) <= 2048
            and url.scheme in {"http", "https"}
            and url.hostname
            and not url.username
            and not url.password
            and "\\" not in value
            and not any(c.isspace() or ord(c) < 32 for c in value)
            and (not base or (not url.query and not url.fragment))
        )
    except ValueError:
        return False


def external_submission_url(article):
    target = settings.get("blog.newsletter_url") or ""
    if (
        not article.is_published
        or article.visibility != "public"
        or not valid_web_url(target, base=True)
    ):
        return ""
    url = urlsplit(target)
    here = urlsplit(state().config.base_url)
    if (url.netloc.lower(), url.path.rstrip("/")) == (here.netloc.lower(), here.path.rstrip("/")):
        return ""
    query = urlencode(
        {
            "source": external_url("public.article", slug=article.slug),
            "title": article.display_title,
            "excerpt": article.summary[:400],
            "author": ", ".join(user.display_name for user in article.authors)[:200],
        }
    )
    return urlunsplit(
        (url.scheme, url.netloc, url.path.rstrip("/") + "/write/from-blog", query, "")
    )
