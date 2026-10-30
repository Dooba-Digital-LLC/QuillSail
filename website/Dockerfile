# The QuillSail website. Build from the repository root, so the docs are included:
#
#   docker build -f website/Dockerfile -t quillsail-website .
#   docker run -p 8000:8000 -e QUILLSAIL_SITE_URL=https://quillsail.blog \
#     -e QUILLSAIL_DOCS_URL=https://docs.quillsail.blog -e QUILLSAIL_UPDATES_URL=https://updates.quillsail.blog \
#     -e QUILLSAIL_PROXY_HOPS=1 quillsail-website

FROM python:3.13-slim-bookworm AS builder
WORKDIR /build
COPY website/ website/
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels './website[server]'

FROM python:3.13-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    QUILLSAIL_REPO_ROOT=/srv/quillsail
WORKDIR /srv/quillsail
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels 'quillsail-website[server]' \
    && rm -rf /wheels
# The documentation site renders these files; the navigation lives in website/docs.toml.
COPY docs/ docs/
COPY website/docs.toml website/docs.toml
COPY README.md CHANGELOG.md CONTRIBUTING.md SECURITY.md CODE_OF_CONDUCT.md LICENSE ./
USER 10002:10002
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]
# Behind a reverse proxy, set QUILLSAIL_PROXY_HOPS so the site trusts exactly that many proxies.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--access-logfile", "-", \
     "quillsail_site:create_app()"]
LABEL org.opencontainers.image.title="QuillSail website" \
      org.opencontainers.image.description="quillsail.blog, docs.quillsail.blog and updates.quillsail.blog" \
      org.opencontainers.image.licenses="MIT"
