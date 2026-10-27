from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from sqlalchemy import select

from copydesk import security
from copydesk.db import db
from copydesk.hooks import Hooks
from copydesk.models import RateLimit
from copydesk.utils import utcnow


def test_rate_limit_is_atomic_across_concurrent_requests(app):
    app.config['RATELIMIT_ENABLED'] = True
    barrier = Barrier(8)

    def attempt(_):
        with app.test_request_context('/'):
            barrier.wait(timeout=10)
            return security.too_many('atomic', 3, 60, key='shared')

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert results.count(False) == 3
    assert results.count(True) == 5
    with app.app_context():
        row = db.session.scalar(select(RateLimit))
        assert row.count == 3
        row.window_start = utcnow() - timedelta(minutes=2)
        db.session.commit()
    with app.test_request_context('/'):
        assert not security.too_many('atomic', 3, 60, key='shared')
        assert db.session.scalar(select(RateLimit)).count == 1


def test_extension_events_accept_event_payload_and_isolate_errors(caplog):
    hooks = Hooks()
    seen = []

    def broken(**kwargs):
        raise ValueError('extension failed')

    hooks.on('article.commented', broken)
    hooks.on('article.commented', lambda event: seen.append(event))
    hooks.fire('article.commented', event='comment-payload')
    assert seen == ['comment-payload']
    assert 'extension failed' in caplog.text


def test_redirects_reject_browser_normalization_and_malformed_urls():
    from copydesk.utils import is_safe_redirect

    for target in ('/\\evil.example', 'https://[', '/\n/evil.example', '//evil.example'):
        assert not is_safe_redirect(target)
    assert is_safe_redirect('/write/?page=2')
