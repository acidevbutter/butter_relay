# Butter Relay — API Route Map & curl Test Cases

Butter Relay is DevButter's company-wide email dispatch service (`fastapibuilder-api`
pattern: router/schemas/models/service/repository per domain). No real production
domain is recorded in this repo (`.env.example`, `PROJETO_RESUMO.md`, `CHANGELOG.md`
only describe infra hostnames like `shared-postgres` / `redis` for the Docker
network — there is no `PROD_BASE_URL`-style env var). Use a placeholder and swap it
for the actual prod host when you run these.

There are two independent auth schemes in this app — don't mix them up:

- **`/api/v1/*`** (JSON API): `Authorization: Bearer <API key>`. The key is a
  per-project secret (see `app/api_keys/dependencies.py::get_current_api_key`),
  plaintext-prefixed `brk_` (see `app/api_keys/service.py::_KEY_PREFIX`). It is
  shown only once, at creation time (`ApiKeyCreated.plaintext_key`) — it's hashed
  (SHA-256) before being stored, so it can never be retrieved again.
- **`/admin/*`** (HTML dashboard, Jinja2/HTMX, `app/admin/`, `app/auth/`): signed
  **session cookie** set by `POST /admin/login`, cookie name from
  `SESSION_COOKIE_NAME` (default `butter_relay_session`), signed with
  `SESSION_SECRET`. curl needs `-c cookies.txt -b cookies.txt` to carry the
  session across requests.

Sending is over HTTPS: `POST /api/v1/messages/send` (section "Messages" below), with a key
that has the `send` scope. The legacy Celery task (`celery_app.send_task("butter_relay.process_message",
...)`) still works while `ALLOW_LEGACY_SEND_TASK=true`, but it trusts the producer's `project_id`.

## Env vars for the commands below

```bash
export BASE_URL="https://relay.devbutter.tech"      # placeholder — replace with the real prod host
export API_KEY="brk_REPLACE_WITH_REAL_PROJECT_KEY"    # from POST /api/v1/api-keys (plaintext_key), prefixed brk_
export ADMIN_EMAIL="admin@devbutter.tech"
export ADMIN_PASSWORD="REPLACE_WITH_REAL_PASSWORD"
export PROJECT_ID="00000000-0000-0000-0000-000000000000"   # fill in after creating a project
export TEMPLATE_ID="00000000-0000-0000-0000-000000000000"  # fill in after publishing a template
export API_KEY_ID="00000000-0000-0000-0000-000000000000"   # fill in after creating an api key
export MESSAGE_ID="00000000-0000-0000-0000-000000000000"   # fill in from a message log entry
export COOKIE_JAR="/tmp/butter_relay_cookies.txt"
```

Admin-session (`/admin/*`) requests below assume you've already run the login
command in the "Auth" section and reuse `$COOKIE_JAR`.

---

## Projects — `/api/v1/projects` (`app/projects/router.py`)

Projects are global/cross-tenant entities. All endpoints require an **admin
session** (`CurrentAdminUserApi` → `get_current_admin_user_for_api`), **not** an
API key — an API key cannot manage projects, even its own.

### GET /api/v1/projects/

List all projects.

**Auth:** admin session cookie (401 JSON if missing/invalid — no HTML redirect on this API-only variant).

```bash
curl -s -b "$COOKIE_JAR" "$BASE_URL/api/v1/projects/" | jq
```

Edge cases:
```bash
# No session cookie -> 401
curl -s -o /dev/null -w '%{http_code}\n' "$BASE_URL/api/v1/projects/"
```

### POST /api/v1/projects/

Create a project. Body (`ProjectCreate`):
- `name: str` (required)
- `slug: str` (required) — lowercase alphanumeric segments separated by single hyphens (`^[a-z0-9]+(-[a-z0-9]+)*$`); doubles as the Celery queue name
- `external: bool` (optional, default `false`)
- `default_sender_email: EmailStr` (required)
- `default_sender_name: str` (required)

**Auth:** admin session cookie. **Success:** `201 Created`, body = `ProjectRead`
(`id, name, slug, external, queue_name, default_sender_email, default_sender_name,
is_active, created_at`).

```bash
curl -s -X POST "$BASE_URL/api/v1/projects/" \
  -b "$COOKIE_JAR" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Sele Libertaria",
    "slug": "sele-libertaria",
    "external": false,
    "default_sender_email": "no-reply@selolibertaria.com.br",
    "default_sender_name": "Sele Libertaria"
  }' | jq
```

Edge cases:
```bash
# No session -> 401
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$BASE_URL/api/v1/projects/" \
  -H "Content-Type: application/json" -d '{}'

# Invalid slug (uppercase/underscore) -> 422
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$BASE_URL/api/v1/projects/" \
  -b "$COOKIE_JAR" -H "Content-Type: application/json" \
  -d '{"name":"Bad","slug":"Not_Valid","default_sender_email":"a@b.com","default_sender_name":"x"}'

# Missing required field (default_sender_email) -> 422
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$BASE_URL/api/v1/projects/" \
  -b "$COOKIE_JAR" -H "Content-Type: application/json" \
  -d '{"name":"Bad","slug":"bad-slug","default_sender_name":"x"}'

# Duplicate slug -> 409
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$BASE_URL/api/v1/projects/" \
  -b "$COOKIE_JAR" -H "Content-Type: application/json" \
  -d '{"name":"Dup","slug":"sele-libertaria","default_sender_email":"a@b.com","default_sender_name":"x"}'
```

### GET /api/v1/projects/{project_id}

Get a single project by id.

**Auth:** admin session cookie. **Success:** `200`, body = `ProjectRead`.

```bash
curl -s -b "$COOKIE_JAR" "$BASE_URL/api/v1/projects/$PROJECT_ID" | jq
```

Edge cases:
```bash
# Unknown id -> 404
curl -s -o /dev/null -w '%{http_code}\n' -b "$COOKIE_JAR" \
  "$BASE_URL/api/v1/projects/00000000-0000-0000-0000-000000000099"

# Malformed UUID -> 422
curl -s -o /dev/null -w '%{http_code}\n' -b "$COOKIE_JAR" "$BASE_URL/api/v1/projects/not-a-uuid"
```

### PATCH /api/v1/projects/{project_id}

Partially update a project. Body (`ProjectUpdate`, all optional):
`name`, `external`, `default_sender_email` (EmailStr), `default_sender_name`, `is_active`.

**Auth:** admin session cookie. **Success:** `200`, body = `ProjectRead`.

```bash
curl -s -X PATCH "$BASE_URL/api/v1/projects/$PROJECT_ID" \
  -b "$COOKIE_JAR" \
  -H "Content-Type: application/json" \
  -d '{"default_sender_name": "Sele Libertaria (updated)", "is_active": true}' | jq
```

Edge cases:
```bash
# Unknown id -> 404
curl -s -o /dev/null -w '%{http_code}\n' -X PATCH "$BASE_URL/api/v1/projects/00000000-0000-0000-0000-000000000099" \
  -b "$COOKIE_JAR" -H "Content-Type: application/json" -d '{"name":"x"}'

# Invalid email -> 422
curl -s -o /dev/null -w '%{http_code}\n' -X PATCH "$BASE_URL/api/v1/projects/$PROJECT_ID" \
  -b "$COOKIE_JAR" -H "Content-Type: application/json" -d '{"default_sender_email":"not-an-email"}'
```

---

### GET / PUT /api/v1/projects/{project_id}/delivery

Read / choose **this project's** delivery channel. **Auth:** the project's own API key
(`Authorization: Bearer brk_...`) — not the admin session; another project's key gets 403.
Only `delivery_provider` (`brevo` | `smtp`) is writable this way; SMTP host/port and sender are
admin-only (`PATCH /api/v1/projects/{id}` or the admin UI).

```bash
curl -s "$BASE_URL/api/v1/projects/$PROJECT_ID/delivery" -H "Authorization: Bearer $API_KEY" | jq
curl -s -X PUT "$BASE_URL/api/v1/projects/$PROJECT_ID/delivery" \
  -H "Authorization: Bearer $API_KEY" -H 'content-type: application/json' \
  -d '{"delivery_provider": "smtp"}' | jq
```

---

## API Keys — `/api/v1/api-keys` (`app/api_keys/router.py`)

All endpoints require a **valid API key** (`CurrentApiKey`), and every one of them
scopes to the *caller's own project* — `_require_same_project` returns **403** if
`project_id` in the query/body doesn't match the calling key's project. This means
you need at least one existing key (created via the `/admin` dashboard, or
bootstrapped some other way) before you can call these with curl.

### GET /api/v1/api-keys/

List API keys for a project (metadata only — never returns plaintext).

**Auth:** `Authorization: Bearer <API key>`. Query param: `project_id: UUID`
(required). **Success:** `200`, body = `list[ApiKeyRead]` (`id, project_id, label,
expires_at, revoked_at, last_used_at, created_at`).

```bash
curl -s "$BASE_URL/api/v1/api-keys/?project_id=$PROJECT_ID" \
  -H "Authorization: Bearer $API_KEY" | jq
```

Edge cases:
```bash
# Missing Authorization header -> 401
curl -s -o /dev/null -w '%{http_code}\n' "$BASE_URL/api/v1/api-keys/?project_id=$PROJECT_ID"

# Malformed header (no "Bearer ") -> 401
curl -s -o /dev/null -w '%{http_code}\n' "$BASE_URL/api/v1/api-keys/?project_id=$PROJECT_ID" \
  -H "Authorization: $API_KEY"

# project_id belongs to a different project than the key -> 403
curl -s -o /dev/null -w '%{http_code}\n' \
  "$BASE_URL/api/v1/api-keys/?project_id=11111111-1111-1111-1111-111111111111" \
  -H "Authorization: Bearer $API_KEY"

# Missing required query param -> 422
curl -s -o /dev/null -w '%{http_code}\n' "$BASE_URL/api/v1/api-keys/" \
  -H "Authorization: Bearer $API_KEY"
```

### POST /api/v1/api-keys/

Create a new API key for a project. Body (`ApiKeyCreate`): `project_id: UUID`
(required), `label: str` (required).

**Auth:** `Authorization: Bearer <API key>` (must match `project_id` in the body).
**Success:** `201 Created`, body = `ApiKeyCreated` (`id, label, plaintext_key,
expires_at`) — `plaintext_key` (prefixed `brk_`) is shown **only this once**.
Note: if the target project is `external: true`, the key expires in 30 days
(`_EXTERNAL_KEY_TTL`); otherwise `expires_at` is `null`.

```bash
curl -s -X POST "$BASE_URL/api/v1/api-keys/" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"project_id": "'"$PROJECT_ID"'", "label": "ci-integration-key"}' | jq
```

Edge cases:
```bash
# No auth -> 401
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$BASE_URL/api/v1/api-keys/" \
  -H "Content-Type: application/json" -d '{"project_id":"'"$PROJECT_ID"'","label":"x"}'

# project_id not the caller's own project -> 403
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$BASE_URL/api/v1/api-keys/" \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"project_id":"11111111-1111-1111-1111-111111111111","label":"x"}'

# project_id does not exist -> 404
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$BASE_URL/api/v1/api-keys/" \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"project_id":"00000000-0000-0000-0000-000000000099","label":"x"}'

# Missing label -> 422
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$BASE_URL/api/v1/api-keys/" \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"project_id":"'"$PROJECT_ID"'"}'
```

### POST /api/v1/api-keys/{api_key_id}/revoke

Revoke an API key (soft — sets `revoked_at`; the key stops authenticating
immediately afterward). Query param: `project_id: UUID` (required, must match
the caller's own project).

**Auth:** `Authorization: Bearer <API key>`. **Success:** `200`, body = `ApiKeyRead`
with `revoked_at` populated.

```bash
curl -s -X POST "$BASE_URL/api/v1/api-keys/$API_KEY_ID/revoke?project_id=$PROJECT_ID" \
  -H "Authorization: Bearer $API_KEY" | jq
```

Edge cases:
```bash
# key id not found for this project -> 404
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  "$BASE_URL/api/v1/api-keys/00000000-0000-0000-0000-000000000099/revoke?project_id=$PROJECT_ID" \
  -H "Authorization: Bearer $API_KEY"

# project_id mismatched with caller -> 403
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  "$BASE_URL/api/v1/api-keys/$API_KEY_ID/revoke?project_id=11111111-1111-1111-1111-111111111111" \
  -H "Authorization: Bearer $API_KEY"
```

---

## Templates — `/api/v1/templates` (`app/templates_/router.py`)

All endpoints require an API key, scoped to the calling key's own project via
`_require_same_project` (403 on mismatch). Publishing a template with an existing
`(project_id, key)` pair creates a **new version** and deactivates the previous
active one (not a conflict) — see `TemplateService.publish`.

### GET /api/v1/templates/

List templates for a project. Query param: `project_id: UUID` (required).

**Auth:** `Authorization: Bearer <API key>`. **Success:** `200`, body =
`list[TemplateRead]`.

```bash
curl -s "$BASE_URL/api/v1/templates/?project_id=$PROJECT_ID" \
  -H "Authorization: Bearer $API_KEY" | jq
```

Edge cases:
```bash
# No auth -> 401
curl -s -o /dev/null -w '%{http_code}\n' "$BASE_URL/api/v1/templates/?project_id=$PROJECT_ID"

# project_id belongs to another project -> 403
curl -s -o /dev/null -w '%{http_code}\n' \
  "$BASE_URL/api/v1/templates/?project_id=11111111-1111-1111-1111-111111111111" \
  -H "Authorization: Bearer $API_KEY"
```

### POST /api/v1/templates/

Publish a new template version. Body (`TemplateCreate`):
- `project_id: UUID` (required, must match caller's project)
- `key: str` (required) — logical template name, e.g. `"welcome-email"`
- `subject_template: str` (required) — Jinja2 (sandboxed, autoescaped)
- `html_template: str` (required) — Jinja2 (sandboxed, autoescaped)
- `variables_schema: dict` (optional, default `{}`) — JSON Schema used to
  validate the payload at render/preview/send time

**Auth:** `Authorization: Bearer <API key>`. **Success:** `201 Created`, body =
`TemplateRead` (`id, project_id, key, subject_template, html_template,
variables_schema, version, is_active, created_at`).

```bash
curl -s -X POST "$BASE_URL/api/v1/templates/" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "'"$PROJECT_ID"'",
    "key": "welcome-email",
    "subject_template": "Bem-vindo(a), {{ name }}!",
    "html_template": "<p>Ola {{ name }}, obrigado por se cadastrar em {{ product }}.</p>",
    "variables_schema": {
      "type": "object",
      "required": ["name", "product"],
      "properties": {
        "name": {"type": "string"},
        "product": {"type": "string"}
      }
    }
  }' | jq
```

Edge cases:
```bash
# No auth -> 401
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$BASE_URL/api/v1/templates/" \
  -H "Content-Type: application/json" -d '{"project_id":"'"$PROJECT_ID"'","key":"x","subject_template":"s","html_template":"h"}'

# project_id not caller's own -> 403
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$BASE_URL/api/v1/templates/" \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"project_id":"11111111-1111-1111-1111-111111111111","key":"x","subject_template":"s","html_template":"h"}'

# Missing required field (html_template) -> 422
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$BASE_URL/api/v1/templates/" \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"project_id":"'"$PROJECT_ID"'","key":"x","subject_template":"s"}'
```

### GET /api/v1/templates/{template_id}

Get a single template version by id.

**Auth:** `Authorization: Bearer <API key>` (403 if the template belongs to
another project). **Success:** `200`, body = `TemplateRead`.

```bash
curl -s "$BASE_URL/api/v1/templates/$TEMPLATE_ID" \
  -H "Authorization: Bearer $API_KEY" | jq
```

Edge cases:
```bash
# Unknown id -> 404
curl -s -o /dev/null -w '%{http_code}\n' \
  "$BASE_URL/api/v1/templates/00000000-0000-0000-0000-000000000099" \
  -H "Authorization: Bearer $API_KEY"
```

### POST /api/v1/templates/{template_id}/preview

Render a template against a sample payload (does not send an email or persist
anything). Body (`TemplatePreviewRequest`): `payload: dict` (optional, default `{}`).

**Auth:** `Authorization: Bearer <API key>` (403 if the template belongs to
another project). **Success:** `200`, body = `TemplatePreviewResponse`
(`subject: str, html: str`).

```bash
curl -s -X POST "$BASE_URL/api/v1/templates/$TEMPLATE_ID/preview" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"payload": {"name": "Maria", "product": "Sele Libertaria"}}' | jq
```

Edge cases:
```bash
# Payload fails the template's variables_schema (JSON Schema) -> 422
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$BASE_URL/api/v1/templates/$TEMPLATE_ID/preview" \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"payload": {}}'

# Unknown template id -> 404
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  "$BASE_URL/api/v1/templates/00000000-0000-0000-0000-000000000099/preview" \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" -d '{}'
```

---

### POST /api/v1/templates/{template_id}/activate

Rollback: makes this (older) version the active one and deactivates the current active version
of the same key. **Auth:** the owning project's API key (403 for another project's key).

```bash
curl -s -X POST "$BASE_URL/api/v1/templates/$TEMPLATE_ID/activate" -H "Authorization: Bearer $API_KEY" | jq
```

Publishing (`POST /api/v1/templates/`) now returns 422 for invalid Jinja2 (subject/html), an
invalid `variables_schema`, or a `key` outside `[A-Za-z0-9][A-Za-z0-9_.-]{0,149}`.

---

## Messages — `/api/v1/messages` (`app/messages/router.py`)

Read-only — this is the delivery log for messages enqueued via the Celery worker
(see the intro). There is **no create/send endpoint here**; do not expect to be
able to POST a message via HTTP.

### POST /api/v1/messages/send

Send an e-mail. **Auth:** project API key with scope `send`. `project_id` is always the key's
project; `from` is not accepted (422). Answers `202 {message_id, status, type}` after storing the
message and queuing it.

Template mode (`type` + `payload`) or raw mode (`type` + `subject` + `html`, optional `text`):

```bash
curl -s -i -X POST "$BASE_URL/api/v1/messages/send" \
  -H "Authorization: Bearer $API_KEY" -H "Idempotency-Key: order-123-processing" \
  -H "Content-Type: application/json" \
  -d '{"type":"woocommerce.order","to":["cliente@example.com"],"reply_to":"contato@acme.com",
       "subject":"Pedido #123","html":"<p>Recebemos seu pedido</p>","text":"Recebemos seu pedido",
       "attachments":[{"filename":"nota.pdf","content_type":"application/pdf","content_base64":"JVBERi0xLjQ="}]}'
```

Limits: 50 recipients (to+cc+bcc), HTML 1 MB, attachments 2 MB, body 3 MB.

| Case | Result |
| --- | --- |
| No key / wrong key | 401 |
| Key without `send` | 403 |
| Same `Idempotency-Key` and body | 200, same `message_id` (no second e-mail) |
| Same `Idempotency-Key`, different body | 409 |
| Undeclared `type` | 422 `unknown_email_type` |
| `from` or any unknown field | 422 |
| Body over 3 MB, HTML over 1 MB, attachments over 2 MB | 413 |
| Rate limit | 429 + `Retry-After` |
| Stored but not queued (Redis down) | 503 `enqueue_failed` + `Retry-After`; repeat with the same `Idempotency-Key` |

### GET /api/v1/messages/

List messages (the email log), scoped to the calling API key's own project —
there is no way to pass a different `project_id`, by design (see the security
note in the router source). Query params: `status_filter` (optional, one of the
`MessageStatus` enum values — check `app/messages/models.py` for the exact
strings, e.g. `pending`/`processing`/`sent`/`failed`), `limit: int` (optional,
default `100`).

**Auth:** `Authorization: Bearer <API key>`. **Success:** `200`, body =
`list[MessageRead]`.

```bash
curl -s "$BASE_URL/api/v1/messages/?limit=50" \
  -H "Authorization: Bearer $API_KEY" | jq

# filter by status
curl -s "$BASE_URL/api/v1/messages/?status_filter=failed&limit=50" \
  -H "Authorization: Bearer $API_KEY" | jq
```

Edge cases:
```bash
# No auth -> 401
curl -s -o /dev/null -w '%{http_code}\n' "$BASE_URL/api/v1/messages/"

# Invalid status_filter value -> 422
curl -s -o /dev/null -w '%{http_code}\n' "$BASE_URL/api/v1/messages/?status_filter=not-a-real-status" \
  -H "Authorization: Bearer $API_KEY"
```

### GET /api/v1/messages/{message_id}

Get a single message by id.

**Auth:** `Authorization: Bearer <API key>`; **403** (not 404) if the message
belongs to a different project than the caller's key — existence of the id is
not itself sensitive, ownership is (explicit design note in the router source).
**Success:** `200`, body = `MessageRead` (`id, project_id, template_id, channel,
to_email, payload, rendered_subject, status, attempts, last_error,
provider_message_id, broker_message_id, received_at, sent_at`).

```bash
curl -s "$BASE_URL/api/v1/messages/$MESSAGE_ID" \
  -H "Authorization: Bearer $API_KEY" | jq
```

Edge cases:
```bash
# Unknown id -> 404
curl -s -o /dev/null -w '%{http_code}\n' \
  "$BASE_URL/api/v1/messages/00000000-0000-0000-0000-000000000099" \
  -H "Authorization: Bearer $API_KEY"

# Message belongs to another project's key -> 403
curl -s -o /dev/null -w '%{http_code}\n' "$BASE_URL/api/v1/messages/$MESSAGE_ID" \
  -H "Authorization: Bearer <a-different-projects-key>"
```

---

## Auth — `/admin/login`, `/admin/logout` (`app/auth/router.py`)

HTML session endpoints for the `/admin` dashboard — not JSON API, but included
since everything else under `/admin` depends on the resulting cookie.

### GET /admin/login

Renders the login form (HTML).

**Auth:** none.

```bash
curl -s "$BASE_URL/admin/login"
```

### POST /admin/login

Authenticates and starts an admin session. **Form-encoded body** (not JSON):
`email: str` (required), `password: str` (required).

**Auth:** none (this *creates* the session). **Success:** `303 See Other` →
redirects to `/admin`, and sets the session cookie (`Set-Cookie:
butter_relay_session=...`). **Failure:** `401` and re-renders `login.html` with
an error (still HTML, not JSON).

```bash
curl -s -i -c "$COOKIE_JAR" -X POST "$BASE_URL/admin/login" \
  --data-urlencode "email=$ADMIN_EMAIL" \
  --data-urlencode "password=$ADMIN_PASSWORD"
```

Edge cases:
```bash
# Wrong password -> 401 (HTML body, not JSON)
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$BASE_URL/admin/login" \
  --data-urlencode "email=$ADMIN_EMAIL" --data-urlencode "password=wrong-password"

# Missing field -> 422
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$BASE_URL/admin/login" \
  --data-urlencode "email=$ADMIN_EMAIL"
```

### POST /admin/logout

Clears the session. **Auth:** existing session cookie (not strictly enforced by
a dependency here — it just clears whatever session exists). **Success:** `303`
→ redirects to `/admin/login`.

```bash
curl -s -i -b "$COOKIE_JAR" -c "$COOKIE_JAR" -X POST "$BASE_URL/admin/logout"
```

---

## Admin dashboard — `/admin/*` (`app/admin/router.py`)

Server-rendered HTML/HTMX views for managing projects, API keys, templates, and
the message log by hand. Every route below requires the admin session cookie
(`CurrentAdminUser` — redirects to `/admin/login` with `303` if missing/invalid,
rather than returning a JSON 401). Form bodies are `application/x-www-form-urlencoded`.
These are primarily for humans/browsers, but curl works for smoke-testing.

### GET /admin

Dashboard home (recent messages).

```bash
curl -s -b "$COOKIE_JAR" "$BASE_URL/admin"
```

### GET /admin/projects

List + create projects (HTML page with a create form).

```bash
curl -s -b "$COOKIE_JAR" "$BASE_URL/admin/projects"
```

### POST /admin/projects

Create a project via the dashboard form. Form fields: `name`, `slug`,
`default_sender_email`, `default_sender_name` (all required strings), `external`
(optional bool checkbox, default `false`).

```bash
curl -s -b "$COOKIE_JAR" -X POST "$BASE_URL/admin/projects" \
  --data-urlencode "name=Sele Libertaria" \
  --data-urlencode "slug=sele-libertaria" \
  --data-urlencode "default_sender_email=no-reply@selolibertaria.com.br" \
  --data-urlencode "default_sender_name=Sele Libertaria"
```

### GET /admin/projects/{project_id}

Project detail: API keys + templates for that project.

```bash
curl -s -b "$COOKIE_JAR" "$BASE_URL/admin/projects/$PROJECT_ID"
```

Edge case: unknown `project_id` bubbles up `NotFoundError` → `404` JSON (this
route isn't wrapped in the HTML-redirect auth dependency's error path, so a
missing *project* still surfaces as the generic `NotFoundError` handler, `404`).

### POST /admin/projects/{project_id}/api-keys

Generate an API key for a project via the dashboard. Form field: `label: str`
(required). Response re-renders the project detail page with the plaintext key
shown once (`new_key_plaintext`).

```bash
curl -s -b "$COOKIE_JAR" -X POST "$BASE_URL/admin/projects/$PROJECT_ID/api-keys" \
  --data-urlencode "label=dashboard-issued-key"
```

### POST /admin/projects/{project_id}/api-keys/{api_key_id}/revoke

Revoke an API key from the dashboard.

```bash
curl -s -b "$COOKIE_JAR" -X POST \
  "$BASE_URL/admin/projects/$PROJECT_ID/api-keys/$API_KEY_ID/revoke"
```

### POST /admin/projects/{project_id}/templates

Publish a new template version via the dashboard. Form fields: `key`,
`subject_template`, `html_template` (all required strings). Note: unlike the
JSON API, this form has **no field for `variables_schema`** — dashboard-published
templates always get `variables_schema: {}`.

```bash
curl -s -b "$COOKIE_JAR" -X POST "$BASE_URL/admin/projects/$PROJECT_ID/templates" \
  --data-urlencode "key=welcome-email" \
  --data-urlencode "subject_template=Bem-vindo(a), {{ name }}!" \
  --data-urlencode "html_template=<p>Ola {{ name }}!</p>"
```

### POST /admin/templates/{template_id}/preview

Render a template preview as an HTMX partial. Form field: `sample_payload: str`
(required) — a JSON string, parsed server-side; on invalid JSON it re-renders
the partial with an inline error (still `200`, not `422` — this is form/HTMX,
not JSON API validation).

```bash
curl -s -b "$COOKIE_JAR" -X POST "$BASE_URL/admin/templates/$TEMPLATE_ID/preview" \
  --data-urlencode 'sample_payload={"name": "Maria", "product": "Sele Libertaria"}'
```

### GET /admin/messages

Message log with filters. Query params: `project_id: UUID` (optional),
`status_filter` (optional, `MessageStatus` enum).

```bash
curl -s -b "$COOKIE_JAR" "$BASE_URL/admin/messages?status_filter=failed"
```

### GET /admin/messages/{message_id}

View a single message's rendered email.

```bash
curl -s -b "$COOKIE_JAR" "$BASE_URL/admin/messages/$MESSAGE_ID"
```

### POST /admin/messages/{message_id}/resend

Manually resend a failed message — re-renders the template (or reuses the
stored `rendered_subject` for non-template messages), calls Brevo directly
(synchronously, not via the queue), and updates status to `sent` or `failed`.
**This has a real side effect (sends an actual email via Brevo)** — be careful
running it against prod with a real `to_email`.

```bash
curl -s -b "$COOKIE_JAR" -X POST "$BASE_URL/admin/messages/$MESSAGE_ID/resend"
```

---

## Suggested test run order

Run against a non-prod environment first if at all possible — several of these
calls have real side effects (creating projects/keys/templates, sending real
email via `/admin/messages/{id}/resend`).

1. `GET /health` and `GET /health/ready` — confirm the instance is up and the DB
   is reachable before anything else.
2. `POST /admin/login` — establish the admin session cookie; everything else
   downstream (project/key/template creation) needs either this cookie or an
   API key that only exists after step 3–4.
3. `POST /api/v1/projects/` (or `POST /admin/projects`) — create a test project;
   capture its `id` as `$PROJECT_ID`.
4. `POST /api/v1/api-keys/` — **note this itself requires an existing API key**
   (`CurrentApiKey`), so in practice bootstrap the first key for a new project
   via the dashboard (`POST /admin/projects/{project_id}/api-keys`), then use
   that key for further `/api/v1/*` calls. Capture the plaintext key as
   `$API_KEY` and its `id` as `$API_KEY_ID`.
5. `GET /api/v1/projects/{project_id}` and `GET /api/v1/api-keys/?project_id=...`
   — sanity-check what was just created.
6. `POST /api/v1/templates/` — publish a template; capture `id` as `$TEMPLATE_ID`.
7. `GET /api/v1/templates/{template_id}` and
   `POST /api/v1/templates/{template_id}/preview` — verify rendering before
   relying on it.
8. Trigger a real message some other way (Celery task — out of scope for curl),
   then: `GET /api/v1/messages/` and `GET /api/v1/messages/{message_id}` — read
   the resulting log entry.
9. Error/edge-case variants (401 without auth, 403 cross-project, 404 unknown
   id, 409 duplicate slug, 422 bad body) — run these anytime after the happy
   path, they don't depend on ordering except needing a valid `$API_KEY`/
   `$COOKIE_JAR` to exercise the "wrong scope" (403) cases meaningfully.
10. Cleanup last: `POST /api/v1/api-keys/{api_key_id}/revoke` (or the
    `/admin/.../revoke` dashboard equivalent) to revoke any test keys created.
    There is no delete endpoint for projects/templates/messages — templates are
    versioned (never deleted, `publish` deactivates the prior version), and
    projects can only be soft-disabled via `PATCH /api/v1/projects/{id}` with
    `{"is_active": false}`.
