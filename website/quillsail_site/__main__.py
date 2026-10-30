"""Development server: python -m quillsail_site [--port 8000].

Open http://localhost:8000 for the site and http://docs.localhost:8000 for the
docs. Python, template and Markdown changes reload the server.
"""

from __future__ import annotations

import argparse
import os

from werkzeug.serving import run_simple

from quillsail_site import create_app
from quillsail_site.config import DEFAULT_REPO_ROOT, Config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    defaults = {"QUILLSAIL_SITE_URL": f"http://localhost:{args.port}",
                "QUILLSAIL_DOCS_URL": f"http://docs.localhost:{args.port}",
                "QUILLSAIL_UPDATES_URL": f"http://updates.localhost:{args.port}"}
    environ = {**defaults, **os.environ}
    config = Config.from_env(environ)
    patterns = ("docs/**/*.md", "*.md", "website/docs.toml", "website/quillsail_site/templates/**/*",
                "website/quillsail_site/install/*")
    watched = [str(path) for pattern in patterns for path in DEFAULT_REPO_ROOT.glob(pattern) if path.is_file()]
    print(f"QuillSail website: {config.site.url}  ·  docs: {config.docs.url}")
    run_simple(args.host, args.port, create_app(config), use_reloader=True, extra_files=watched, threaded=True)


if __name__ == "__main__":
    main()
