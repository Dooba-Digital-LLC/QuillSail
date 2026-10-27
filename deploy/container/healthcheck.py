"""Check the web endpoint or persistent worker heartbeat without sending mail."""

import json
import sys
from urllib.request import urlopen

from copydesk.config import load_config


def main(kind):
    config = load_config(require_file=True)
    if kind == "worker":
        from copydesk.app import create_app
        from copydesk.jobs import worker_last_seen
        from copydesk.utils import utcnow

        with create_app(config).app_context():
            heartbeat = worker_last_seen()
            if heartbeat is None or not 0 <= (utcnow() - heartbeat).total_seconds() < 180:
                raise RuntimeError("Worker heartbeat is missing or stale")
    else:
        with urlopen(
            "http://127.0.0.1:8000" + config.url_prefix + "/healthz", timeout=3
        ) as response:
            if response.status != 200 or json.load(response).get("database") != "ok":
                raise RuntimeError("Web/database health check failed")


if __name__ == "__main__":
    try:
        main(sys.argv[1] if len(sys.argv) > 1 else "web")
    except Exception:
        print("QuillSail health check failed", file=sys.stderr)
        raise SystemExit(1) from None
