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
PROJECT_SLUG="${PROJECT_SLUG:-crate-diggr}"

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
create_resp=$(curl -s -w "\n%{http_code}" -X POST "$BASE_URL/api/v1/projects/" \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"Crate Diggr\", \"slug\": \"$PROJECT_SLUG\", \"sqs_queue_url\": \"http://elasticmq:9324/000000000000/relay-shared\", \"default_sender_email\": \"noreply@devbutter.com\", \"default_sender_name\": \"DevButter\"}")
create_code=$(echo "$create_resp" | tail -1)
create_body=$(echo "$create_resp" | sed '$d')

if [ "$create_code" = "201" ]; then
    project_id=$(echo "$create_body" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
    check "POST /api/v1/projects/ (create)" 201 "$create_code"
elif [ "$create_code" = "409" ]; then
    list_resp=$(curl -s "$BASE_URL/api/v1/projects/")
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

# --- publish a template ------------------------------------------------------
template_resp=$(curl -s -w "\n%{http_code}" -X POST "$BASE_URL/api/v1/templates/" \
    -H "Content-Type: application/json" \
    -d "{\"project_id\": \"$project_id\", \"key\": \"welcome_email\", \"subject_template\": \"Welcome, {{ name }}!\", \"html_template\": \"<p>Hi {{ name }}</p>\"}")
template_code=$(echo "$template_resp" | tail -1)
template_body=$(echo "$template_resp" | sed '$d')
check "POST /api/v1/templates/ (publish)" 201 "$template_code"
template_id=$(echo "$template_body" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
echo "     template_id=$template_id"

# --- preview the template ----------------------------------------------------
preview_resp=$(curl -s -w "\n%{http_code}" -X POST "$BASE_URL/api/v1/templates/$template_id/preview" \
    -H "Content-Type: application/json" \
    -d '{"payload": {"name": "Marcos"}}')
preview_code=$(echo "$preview_resp" | tail -1)
preview_body=$(echo "$preview_resp" | sed '$d')
check "POST /api/v1/templates/{id}/preview" 200 "$preview_code"
echo "     rendered subject: $(echo "$preview_body" | python3 -c "import json,sys; print(json.load(sys.stdin)['subject'])")"

# --- generate an API key -----------------------------------------------------
api_key_resp=$(curl -s -w "\n%{http_code}" -X POST "$BASE_URL/api/v1/api-keys/" \
    -H "Content-Type: application/json" \
    -d "{\"project_id\": \"$project_id\", \"label\": \"smoke-test\"}")
api_key_code=$(echo "$api_key_resp" | tail -1)
check "POST /api/v1/api-keys/ (create)" 201 "$api_key_code"

# --- list messages (the email log) ------------------------------------------
messages_code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/api/v1/messages/?project_id=$project_id")
check "GET /api/v1/messages/" 200 "$messages_code"

# --- OpenAPI schema loads -----------------------------------------------------
code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/docs")
check "GET /docs" 200 "$code"

echo "=================================="
echo "Passed: $pass  Failed: $fail"
[ "$fail" -eq 0 ]
