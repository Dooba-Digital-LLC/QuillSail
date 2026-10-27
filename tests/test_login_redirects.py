from urllib.parse import parse_qs, urlsplit

import pytest

from copydesk.app import PrefixMiddleware


@pytest.fixture(params=["", "/newsletter", "/community/news"])
def mounted_client(app, request):
    prefix = request.param
    app.config["APPLICATION_ROOT"] = prefix or "/"
    if prefix:
        app.wsgi_app = PrefixMiddleware(app.wsgi_app, prefix)
    return app.test_client(), prefix


def sign_in(client, prefix, target):
    client.get(prefix + "/login", query_string={"next": target})
    with client.session_transaction() as session:
        csrf = session["_csrf"]
    return client.post(prefix + "/login", data={
        "email": "editor@example.edu", "password": "correct-horse-battery-123",
        "csrf_token": csrf, "next": target,
    })


@pytest.mark.parametrize("target", ["/dashboard/", "/dashboard/business/?status=new#inbox", "/account/security", "/"])
def test_sign_in_keeps_app_relative_next_inside_mount(mounted_client, target):
    client, prefix = mounted_client
    response = sign_in(client, prefix, target)
    assert response.status_code == 302
    assert response.headers["Location"] == prefix + target


def test_already_prefixed_next_is_not_prefixed_twice(mounted_client):
    client, prefix = mounted_client
    target = prefix + "/dashboard/business/?status=follow_up#inbox"
    response = sign_in(client, prefix, target)
    assert response.headers["Location"] == target
    assert client.get(prefix + "/login", query_string={"next": target}).headers["Location"] == target


def test_dashboard_guard_returns_to_requested_page_after_login(mounted_client):
    client, prefix = mounted_client
    destination = prefix + "/dashboard/business/?status=new"
    guarded = client.get(destination)
    assert guarded.status_code == 302
    login = urlsplit(guarded.headers["Location"])
    assert login.path == prefix + "/login"
    target = parse_qs(login.query)["next"][0]
    response = sign_in(client, prefix, target)
    assert response.headers["Location"] == destination
    page = client.get(response.headers["Location"])
    assert page.status_code == 200
    assert b"Business outreach" in page.data


def test_authenticated_login_redirect_is_mount_aware(mounted_client):
    client, prefix = mounted_client
    sign_in(client, prefix, "/")
    response = client.get(prefix + "/login", query_string={"next": "/dashboard/settings/email"})
    assert response.headers["Location"] == prefix + "/dashboard/settings/email"


@pytest.mark.parametrize("target", [
    "https://evil.example/", "//evil.example/", "/\\evil.example/", "/\n/evil.example/",
    "/../dashboard/", "/newsletter/../dashboard/", "/%2e%2e/dashboard/", "/%5cevil.example/",
])
def test_unsafe_next_falls_back_to_the_app_home(mounted_client, target):
    client, prefix = mounted_client
    response = sign_in(client, prefix, target)
    assert response.headers["Location"] == prefix + "/"
