#!/usr/bin/env bash
# End-to-end smoke test for Butter Relay via curl.
# Assumes `docker compose --profile dev up -d` is already running.
#
# Usage:
#   ./scripts/curl_smoke_test.sh
#
# Env vars (all optional):
#   BASE_URL       default http://localhost:8010
#   ADMIN_EMAIL    default admin@devbutter.com
#   ADMIN_PASSWORD default change-me-please
#   PROJECT_SLUG   default crate-diggr (reused if it already exists)

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8010}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@devbutter.com}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-change-me-please}"
PROJECT_SLUG="${PROJECT_SLUG:-devbutter-project}"

COOKIE_JAR="$(mktemp)"
trap 'rm -f "$COOKIE_JAR"' EXIT

pass=0
fail=0

check() {
    local description="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then
        echo "OK   $description (HTTP $actual)"
        pass=$((pass + 1))
    else
        echo "FAIL $description (expected $expected, got $actual)"
        fail=$((fail + 1))
    fi
}

echo "== Butter Relay smoke test -- $BASE_URL =="

# --- health -----------------------------------------------------------------
code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/health")
check "GET /health" 200 "$code"

# --- admin user (idempotent bootstrap via docker compose) -------------------
# ConflictError is expected here on repeat runs (@log_errors logs it before
# re-raising, hence the stderr redirect) -- this step is pure setup, not
# asserted on its own.
docker compose exec -T api python -c "
import asyncio
from app.auth.repository import AdminUserRepository
from app.auth.service import AuthService
from app.core.exceptions import ConflictError
from app.db.session import session_factory

async def main():
    async with session_factory() as session:
        service = AuthService(AdminUserRepository(session))
        try:
            await service.register('$ADMIN_EMAIL', '$ADMIN_PASSWORD')
        except ConflictError:
            pass

asyncio.run(main())
" > /dev/null 2>&1

# --- login --------------------------------------------------------------------
code=$(curl -s -c "$COOKIE_JAR" -o /dev/null -w "%{http_code}" \
    -X POST "$BASE_URL/admin/login" \
    --data-urlencode "email=$ADMIN_EMAIL" \
    --data-urlencode "password=$ADMIN_PASSWORD")
check "POST /admin/login" 303 "$code"

# --- dashboard requires session ---------------------------------------------
code=$(curl -s -b "$COOKIE_JAR" -o /dev/null -w "%{http_code}" "$BASE_URL/admin")
check "GET /admin (with session)" 200 "$code"

code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/admin")
check "GET /admin (no session, redirect to login)" 303 "$code"

# --- create (or reuse) a project via the JSON API ---------------------------
create_resp=$(curl -s -b "$COOKIE_JAR" -w "\n%{http_code}" -X POST "$BASE_URL/api/v1/projects/" \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"Crate Diggr\", \"slug\": \"$PROJECT_SLUG\", \"default_sender_email\": \"noreply@devbutter.com\", \"default_sender_name\": \"DevButter\"}")
create_code=$(echo "$create_resp" | tail -1)
create_body=$(echo "$create_resp" | sed '$d')

if [ "$create_code" = "201" ]; then
    project_id=$(echo "$create_body" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
    check "POST /api/v1/projects/ (create)" 201 "$create_code"
elif [ "$create_code" = "409" ]; then
    list_resp=$(curl -s -b "$COOKIE_JAR" "$BASE_URL/api/v1/projects/")
    project_id=$(echo "$list_resp" | python3 -c "
import json, sys
projects = json.load(sys.stdin)
print(next(p['id'] for p in projects if p['slug'] == '$PROJECT_SLUG'))
")
    check "POST /api/v1/projects/ (already exists, reused)" 409 "$create_code"
else
    check "POST /api/v1/projects/" 201 "$create_code"
    project_id=""
fi
echo "     project_id=$project_id"

# --- mint a project API key via the admin dashboard --------------------------
# /api/v1/{templates,api-keys,messages} need `Authorization: Bearer <project key>`,
# and the very first key of a project can only come from the admin session.
key_page=$(curl -s -b "$COOKIE_JAR" -X POST "$BASE_URL/admin/projects/$project_id/api-keys" \
    --data-urlencode "label=smoke-test-$(date +%s)")
API_KEY=$(echo "$key_page" | grep -o 'brk_[A-Za-z0-9_-]*' | head -1)
if [ -n "$API_KEY" ]; then
    check "POST /admin/projects/{id}/api-keys (mint key)" ok ok
else
    check "POST /admin/projects/{id}/api-keys (mint key)" ok "no key in response"
fi
AUTH_HEADER="Authorization: Bearer $API_KEY"

# --- publish a template ------------------------------------------------------
template_resp=$(curl -s -w "\n%{http_code}" -X POST "$BASE_URL/api/v1/templates/" \
    -H "$AUTH_HEADER" -H "Content-Type: application/json" \
    -d "{\"project_id\": \"$project_id\", \"key\": \"welcome_email\", \"subject_template\": \"Welcome, {{ name }}!\", \"html_template\": \"<p>Hi {{ name }}</p>\"}")
template_code=$(echo "$template_resp" | tail -1)
template_body=$(echo "$template_resp" | sed '$d')
check "POST /api/v1/templates/ (publish)" 201 "$template_code"
template_id=$(echo "$template_body" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
echo "     template_id=$template_id"

# --- preview the template ----------------------------------------------------
preview_resp=$(curl -s -w "\n%{http_code}" -X POST "$BASE_URL/api/v1/templates/$template_id/preview" \
    -H "$AUTH_HEADER" -H "Content-Type: application/json" \
    -d '{"payload": {"name": "Marcos"}}')
preview_code=$(echo "$preview_resp" | tail -1)
preview_body=$(echo "$preview_resp" | sed '$d')
check "POST /api/v1/templates/{id}/preview" 200 "$preview_code"
echo "     rendered subject: $(echo "$preview_body" | python3 -c "import json,sys; print(json.load(sys.stdin)['subject'])")"

# --- generate an API key -----------------------------------------------------
api_key_resp=$(curl -s -w "\n%{http_code}" -X POST "$BASE_URL/api/v1/api-keys/" \
    -H "$AUTH_HEADER" -H "Content-Type: application/json" \
    -d "{\"project_id\": \"$project_id\", \"label\": \"smoke-test\"}")
api_key_code=$(echo "$api_key_resp" | tail -1)
check "POST /api/v1/api-keys/ (create)" 201 "$api_key_code"

# --- list messages (the email log) ------------------------------------------
messages_code=$(curl -s -H "$AUTH_HEADER" -o /dev/null -w "%{http_code}" "$BASE_URL/api/v1/messages/?project_id=$project_id")
check "GET /api/v1/messages/" 200 "$messages_code"

# --- end-to-end: publish a Celery task and wait for the worker to deliver it ---
# Redis -> worker -> render -> provider -> messages row. Requires the worker container.
docker compose exec -T api python -c "
from celery import Celery
from app.settings.config import settings
Celery('smoke-test', broker=settings.redis_url).send_task(
    'butter_relay.process_message',
    kwargs={'project_id': '$project_id', 'template_key': 'welcome_email',
            'to': 'smoke@example.com', 'payload': {'name': 'Smoke'}},
    queue=f'relay-$PROJECT_SLUG',
)
" > /dev/null 2>&1
final_status="timeout"
for _ in $(seq 1 20); do
    final_status=$(curl -s -H "$AUTH_HEADER" "$BASE_URL/api/v1/messages/?project_id=$project_id" \
        | python3 -c "
import json, sys
msgs = [m for m in json.load(sys.stdin) if m['to_email'] == 'smoke@example.com']
print(msgs[0]['status'] if msgs else 'waiting')
" 2>/dev/null || echo waiting)
    [ "$final_status" = "sent" ] || [ "$final_status" = "failed" ] && break
    sleep 1
done
check "worker delivered message (queue relay-$PROJECT_SLUG)" sent "$final_status"

# --- OpenAPI schema loads -----------------------------------------------------
code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/docs")
check "GET /docs" 200 "$code"

echo "=================================="
echo "Passed: $pass  Failed: $fail"
[ "$fail" -eq 0 ]
