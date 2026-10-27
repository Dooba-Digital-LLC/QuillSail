import json
from io import BytesIO

import pytest
from lxml import html
from PIL import Image
from sqlalchemy import func, select

from copydesk import appearance, settings
from copydesk.db import db
from copydesk.models import Job, Setting, Upload

PATH = "/dashboard/settings/appearance"


def image(color="purple"):
    stream = BytesIO()
    Image.new("RGBA", (600, 150), color).save(stream, "PNG")
    stream.seek(0)
    return stream, "hero.png"


def save(client, **values):
    return client.post(PATH, data={"csrf_token": "test-csrf", **values})


def page(client, url="/"):
    return html.fromstring(client.get(url).data)


def heading(document):
    return document.get_element_by_id("hero-title")


def snapshot(app):
    with app.app_context():
        return ({row.key: row.value for row in db.session.scalars(select(Setting))},
                db.session.scalar(select(func.count()).select_from(Upload)),
                db.session.scalar(select(func.count()).select_from(Job)),
                sorted(app.extensions["copydesk"].config.uploads_dir.rglob("*")))


@pytest.mark.parametrize("mode", ["newsletter", "blog", "hybrid"])
def test_custom_home_hero_keeps_publication_identity_and_signup(app, editor_client, mode):
    with app.app_context():
        settings.set_values({"site.mode": mode, "site.organization": "Example Organization", "site.tagline": "Original tagline"})
        db.session.commit()
    assert save(editor_client, **{"appearance.hero_title": "Stories from our community",
                                 "appearance.hero_subtitle": "Projects, people and ideas",
                                 "appearance.hero_overline": "The community journal"}).status_code == 302
    document = page(editor_client)
    assert heading(document).text_content().strip() == "Stories from our community"
    assert "Projects, people and ideas" in document.text_content()
    assert "The community journal" in document.text_content()
    assert document.xpath("//header[contains(@class,'site-header')]//a[contains(@class,'brand')]")[0].text_content().strip() == "Example Newsletter"
    assert "Example Newsletter" in document.find("head/title").text
    assert document.xpath("//form[contains(@class,'subscribe-box')]") if mode != "blog" else not document.xpath("//form[contains(@class,'subscribe-box')]")
    preview = page(editor_client, PATH + "/preview?preview_format=home")
    assert heading(preview).text_content().strip() == "Stories from our community"
    if mode == "blog":
        assert "Latest posts" in preview.text_content()
    with app.app_context():
        assert settings.get("site.name") == "Example Newsletter"
        assert settings.get("site.tagline") == "Original tagline"
        assert settings.get("site.organization") == "Example Organization"


def test_hero_logo_is_independent_from_header_and_email_logos(app, editor_client):
    assert save(editor_client, logo=image("blue"), logo_dark=image("white"), hero_logo=image(), hero_logo_dark=image("yellow"),
                **{"appearance.hero_display": "logo", "appearance.hero_title": "Accessible hero name", "appearance.hero_logo_width": "720"}).status_code == 302
    document = page(editor_client)
    h1 = heading(document)
    assert not h1.text_content().strip()
    logos = h1.findall("img")
    assert len(logos) == 2
    assert all(logo.get("alt") == "Accessible hero name" for logo in logos)
    with app.test_request_context("/"):
        assert settings.get("appearance.hero_logo") in logos[0].get("src")
        assert settings.get("appearance.hero_logo_dark") in logos[1].get("src")
        main = document.xpath("//header[contains(@class,'site-header')]//img")[0]
        assert settings.get("appearance.logo") in main.get("src")
        email = editor_client.get(PATH + "/preview?preview_format=email").get_data(as_text=True)
        assert settings.get("appearance.hero_logo") not in email
        assert settings.get("appearance.logo") in email
    assert "--hero-logo-width:720px" in editor_client.get("/").get_data(as_text=True)


def test_hero_reuses_main_logo_or_falls_back_to_readable_title(app, editor_client):
    assert save(editor_client, **{"appearance.hero_display": "logo", "appearance.hero_title": "A custom heading"}).status_code == 302
    assert heading(page(editor_client)).text_content().strip() == "A custom heading"
    assert save(editor_client, logo=image()).status_code == 302
    h1 = heading(page(editor_client))
    assert len(h1.findall("img")) == 1 and not h1.text_content().strip()
    assert save(editor_client, **{"appearance.hero_display": "both"}).status_code == 302
    h1 = heading(page(editor_client))
    assert h1.text_content().strip() == "A custom heading"
    assert h1.find("img").get("alt") == ""
    assert save(editor_client, remove_logo="1").status_code == 302
    assert heading(page(editor_client)).text_content().strip() == "A custom heading"


def test_custom_hero_light_logo_does_not_use_an_unrelated_dark_masthead(app, editor_client):
    assert save(editor_client, logo=image("blue"), logo_dark=image("white"), hero_logo=image(),
                **{"appearance.hero_display": "logo"}).status_code == 302
    assert len(heading(page(editor_client)).findall("img")) == 1


def test_home_preview_with_new_logo_is_private_and_does_not_write(app, editor_client):
    before = snapshot(app)
    response = editor_client.post(PATH + "/preview", data={"csrf_token": "test-csrf", "preview_format": "home",
        "appearance.hero_title": "Unsaved homepage title", "appearance.hero_display": "both", "hero_logo": image()})
    assert response.status_code == 200
    document = html.fromstring(response.data)
    assert heading(document).text_content().strip() == "Unsaved homepage title"
    assert heading(document).find("img").get("src").startswith("data:image/png;base64,")
    assert document.xpath("//form[contains(@class,'subscribe-box')]//button[@disabled]")
    assert response.headers["Cache-Control"] == "private, no-store"
    assert "noindex" in response.headers["X-Robots-Tag"]
    assert snapshot(app) == before
    assert heading(page(editor_client)).text_content().strip() == "Example Newsletter"


def test_blank_overrides_keep_defaults_and_explicit_visibility_controls_hide_them(app, editor_client):
    with app.app_context():
        settings.set_values({"site.tagline": "Original tagline", "site.organization": "Original organization"})
        db.session.commit()
    assert save(editor_client, full_design="1").status_code == 302  # A browser form from before these controls existed.
    document = page(editor_client)
    assert "Original tagline" in document.xpath("//section[contains(@class,'hero')]")[0].text_content()
    assert "Original organization" in document.xpath("//section[contains(@class,'hero')]")[0].text_content()
    assert save(editor_client, full_design="1", hero_controls="1").status_code == 302
    hero = page(editor_client).xpath("//section[contains(@class,'hero')]")[0].text_content()
    assert "Original tagline" not in hero and "Original organization" not in hero


def test_hero_controls_escape_text_validate_images_and_reject_invalid_sizes(app, editor_client):
    assert save(editor_client, **{"appearance.hero_title": '<script id="bad">alert(1)</script>'}).status_code == 302
    document = page(editor_client)
    assert not document.xpath("//script[@id='bad']")
    assert heading(document).text_content().strip() == '<script id="bad">alert(1)</script>'
    before = snapshot(app)
    assert save(editor_client, hero_logo=(BytesIO(b'<svg onload="alert(1)"></svg>'), "bad.svg"),
                **{"appearance.hero_display": "unknown", "appearance.hero_logo_width": "999999"}).status_code == 422
    assert snapshot(app) == before


def test_hero_design_import_export_and_style_preservation(app, editor_client):
    assert save(editor_client, **{"appearance.hero_title": "Keep this homepage", "appearance.hero_display": "logo"}).status_code == 302
    response = editor_client.get(PATH + "/export")
    assert response.json["settings"]["appearance.hero_title"] == "Keep this homepage"
    assert "appearance.hero_logo" not in response.json["settings"]
    for name in appearance.PRESETS:
        assert not any(key.startswith("appearance.hero_") for key in appearance.preset_values(name))
    loaded = save(editor_client, action="preset", preset="brief")
    assert b'value="Keep this homepage"' in loaded.data
    document = {"format": "quillsail.appearance.v1", "settings": {"appearance.hero_title": "Imported homepage"}}
    before = snapshot(app)
    response = save(editor_client, action="import", design_file=(BytesIO(json.dumps(document).encode()), "design.json"))
    assert response.status_code == 200 and b'value="Imported homepage"' in response.data
    assert snapshot(app) == before


@pytest.mark.parametrize("mode, default", [("newsletter", "center"), ("blog", "left"), ("hybrid", "left")])
def test_hero_alignment_defaults_to_the_existing_layout_and_can_be_changed(app, editor_client, mode, default):
    with app.app_context():
        settings.set_values({"site.mode": mode})
        db.session.commit()

    def alignment(document):
        intro = document.xpath("//*[@id='hero-title']/ancestor::*[contains(@class,'hero-align-')][1]")[0]
        return [name for name in intro.get("class").split() if name.startswith("hero-align-")]

    assert alignment(page(editor_client)) == ["hero-align-" + default]
    for choice in ("left", "center", "right"):
        assert save(editor_client, **{"appearance.hero_align": choice}).status_code == 302
        assert alignment(page(editor_client)) == ["hero-align-" + choice]
    preview = editor_client.post(PATH + "/preview", data={"csrf_token": "test-csrf", "preview_format": "home",
                                                           "appearance.hero_align": "center"})
    assert alignment(html.fromstring(preview.data)) == ["hero-align-center"]
    assert alignment(page(editor_client)) == ["hero-align-right"]
    assert save(editor_client, **{"appearance.hero_align": "diagonal"}).status_code == 422
    assert alignment(page(editor_client)) == ["hero-align-right"]
