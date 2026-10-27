#!/usr/bin/env bash
# Exercise the built image in an isolated Compose project; never use a live volume.
set -euo pipefail
cd "$(dirname "$0")/.."
export COMPOSE_PROJECT_NAME="copydesk-smoke-${RANDOM}-${RANDOM}"
export COPYDESK_PORT=18085
export COPYDESK_BASE_URL=http://localhost:18085/community
export COPYDESK_PROXY_HOPS=0
export COPYDESK_IMAGE=${COPYDESK_IMAGE:-copydesk:ci}
smoke_env=$(mktemp)
cleanup() {
    docker compose --env-file "$smoke_env" -p "$COMPOSE_PROJECT_NAME" down --volumes --remove-orphans
    rm -f "$smoke_env"
}
trap cleanup EXIT
wait_healthy() {
    for attempt in {1..90}; do
        web=$(docker compose --env-file "$smoke_env" -p "$COMPOSE_PROJECT_NAME" ps -q web)
        worker=$(docker compose --env-file "$smoke_env" -p "$COMPOSE_PROJECT_NAME" ps -q worker)
        if test -n "$web" && test -n "$worker"; then
            web_health=$(docker inspect --format '{{.State.Health.Status}}' "$web")
            worker_health=$(docker inspect --format '{{.State.Health.Status}}' "$worker")
            if test "$web_health" = healthy && test "$worker_health" = healthy; then return; fi
        fi
        sleep 2
    done
    docker compose --env-file "$smoke_env" -p "$COMPOSE_PROJECT_NAME" logs
    return 1
}
docker compose --env-file "$smoke_env" -p "$COMPOSE_PROJECT_NAME" up -d --no-build
wait_healthy
python3 - <<'PY'
import json
from urllib.request import urlopen
with urlopen('http://127.0.0.1:18085/community/healthz', timeout=10) as response:
    assert json.load(response)['database'] == 'ok'
with urlopen('http://127.0.0.1:18085/community/', timeout=10) as response:
    assert 'Setup code' in response.read().decode()
PY
docker compose --env-file "$smoke_env" -p "$COMPOSE_PROJECT_NAME" exec -T web python -c \
    'import os; from pathlib import Path; assert os.getuid() != 0; p=Path("/var/lib/copydesk/copydesk.toml"); assert p.stat().st_mode & 0o077 == 0; assert Path("/var/lib/copydesk/data/setup-code.txt").exists()'
before=$(docker compose --env-file "$smoke_env" -p "$COMPOSE_PROJECT_NAME" exec -T web python -c \
    'import hashlib; from pathlib import Path; print(hashlib.sha256(Path("/var/lib/copydesk/copydesk.toml").read_bytes()).hexdigest())')
docker compose --env-file "$smoke_env" -p "$COMPOSE_PROJECT_NAME" restart web worker
wait_healthy
after=$(docker compose --env-file "$smoke_env" -p "$COMPOSE_PROJECT_NAME" exec -T web python -c \
    'import hashlib; from pathlib import Path; print(hashlib.sha256(Path("/var/lib/copydesk/copydesk.toml").read_bytes()).hexdigest())')
test "$before" = "$after"
echo 'Container startup, non-root execution, subpath routing, worker health and secret persistence passed.'
