"""Canonical page metadata and factual, connected Schema.org entities."""

from __future__ import annotations

from textwrap import shorten

from quillsail_site.config import Config
from quillsail_site.docs import Page
from quillsail_site.releases import Release

HOME_TITLE = "QuillSail — Open-source Blog & Newsletter CMS"
HOME_DESCRIPTION = (
    "Run your own blog and newsletter with QuillSail: free, open-source software "
    "for reader submissions, editorial review and email publishing."
)
DOWNLOAD_TITLE = "Download QuillSail for macOS, Windows & Linux"
DOWNLOAD_DESCRIPTION = (
    "Download QuillSail free for macOS, Windows or Linux. Get the desktop app, "
    "terminal installer or self-hosted server package, with verified release checksums."
)


def description(text: str) -> str:
    """Keep snippets readable, without cutting a word or inheriting layout spaces."""
    return shorten(str(text), width=160, placeholder="…")


def page_metadata(config: Config, *, title: str, summary: str, url: str,
                  release: Release | None = None, page: Page | None = None,
                  download: bool = False) -> dict:
    summary = description(summary)
    documentation = page is not None
    address = config.docs if documentation else config.site
    home = config.site.join("/")
    identity = home + "#organization"
    software = home + "#software"
    website = address.join("/") + "#website"
    graph = [
        {"@type": "Organization", "@id": identity, "name": "QuillSail contributors",
         "url": home, "logo": config.site.join("/static/img/favicon.svg"),
         "sameAs": [config.github_url]},
        {"@type": "WebSite", "@id": website,
         "name": "QuillSail Documentation" if documentation else "QuillSail",
         "url": address.join("/"), "inLanguage": "en", "publisher": {"@id": identity}},
    ]
    webpage = {
        "@type": "CollectionPage" if documentation and not page.slug else "WebPage",
        "@id": url + "#webpage", "url": url, "name": title, "description": summary,
        "inLanguage": "en", "isPartOf": {"@id": website}, "about": {"@id": software},
        "primaryImageOfPage": {"@type": "ImageObject", "url": config.site.join("/static/img/og.png"),
                               "width": 1200, "height": 630},
    }
    graph.append(webpage)
    breadcrumbs = []
    if documentation:
        breadcrumbs = [{"title": "QuillSail", "url": home},
                       {"title": "Documentation", "url": config.docs.join("/")}]
        if page.slug:
            breadcrumbs.append({"title": page.title, "url": url})
            article = {
                "@type": "TechArticle", "@id": url + "#article", "url": url,
                "headline": page.heading, "description": summary, "inLanguage": "en",
                "articleSection": page.section, "author": {"@id": identity},
                "mainEntityOfPage": {"@id": webpage["@id"]}, "about": {"@id": software},
                "isPartOf": {"@id": website},
            }
            graph.append(article)
            webpage["mainEntity"] = {"@id": article["@id"]}
    else:
        application = {
            "@type": "SoftwareApplication", "@id": software, "name": "QuillSail",
            "description": HOME_DESCRIPTION, "url": home,
            "applicationCategory": "BusinessApplication",
            "applicationSubCategory": "Blog and newsletter content management system",
            "operatingSystem": ["macOS", "Windows", "Linux"],
            "downloadUrl": config.site.join("/download"),
            "license": "https://opensource.org/licenses/MIT", "isAccessibleForFree": True,
            "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD",
                       "url": config.site.join("/download")},
            "publisher": {"@id": identity}, "sameAs": [config.github_url],
            "image": config.site.join("/static/img/og.png"),
            "featureList": ["Reader submissions", "Editorial review", "Email newsletters",
                            "Self-hosted publishing", "Reader groups and permissions"],
        }
        if release:
            application["softwareVersion"] = release.version
            application["releaseNotes"] = release.notes_url
        graph.append(application)
        webpage["mainEntity"] = {"@id": software}
        if download:
            breadcrumbs = [{"title": "Home", "url": home}, {"title": "Download", "url": url}]
    if breadcrumbs:
        breadcrumb = {"@type": "BreadcrumbList", "@id": url + "#breadcrumbs", "itemListElement": [
            {"@type": "ListItem", "position": index, "name": item["title"], "item": item["url"]}
            for index, item in enumerate(breadcrumbs, 1)
        ]}
        graph.append(breadcrumb)
        webpage["breadcrumb"] = {"@id": breadcrumb["@id"]}
    return {
        "seo_title": title, "seo_description": summary, "canonical_url": url,
        "structured_data": {"@context": "https://schema.org", "@graph": graph},
        "breadcrumbs": breadcrumbs,
    }
