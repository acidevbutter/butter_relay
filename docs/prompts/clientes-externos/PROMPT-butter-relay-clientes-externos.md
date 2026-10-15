# Prompt — butter_relay: clientes externos via HTTPS, chaves, templates e manifesto JSON

> Cole este prompt numa sessão do Claude Code aberta na raiz de `butter_relay` (branch nova a partir de `master`).
> Contexto: análise em `docs/analise-butter-relay.md` (repo ediy_v2). Primeiro cliente: **EDIY** (WordPress/WooCommerce na GoDaddy).

---

## Papel e regras de trabalho

Você é o engenheiro responsável por evoluir o **butter_relay** (FastAPI async + SQLAlchemy 2 + Alembic + Celery/Redis + Jinja2/HTMX). Leia antes de codar: `README.md`, `AGENTS.md`, `docs/GUIA_FLUXO_E_IMPLEMENTACAO.md`, `docs/butter-relay-fluxos.md`, `docs/auditoria-seguranca-2026-08-29.md`, `app/worker/tasks.py`, `app/core/email_provider.py`, `app/api_keys/*`, `app/projects/*`, `app/templates_/*`, `app/messages/*`, `app/admin/router.py`.

1. **Siga o padrão fastapi-builder-api** (skill `fastapi-builder`): cada domínio novo tem `router.py`, `schemas.py`, `models.py`, `service.py`, `repository.py`, `dependencies.py` (DI em `dependencies.py`, nunca importar de `router` de outro domínio; `core/dependencies.py` só tem `DbSession`). Camadas: router → service → repository. Exceções via `app/core/exceptions.py`. Logs com `@log_call`/`@log_errors`, sem endereços de e-mail nem corpos nos logs.
2. **Migrations Alembic**: nome timestamped (`YYYYMMDD_HHMM_<rev>_<slug>.py`), **um único head** (`alembic heads` deve listar 1). Se existir o CLI `app db` (Typer) use-o; senão use `alembic` direto. Migrations devem rodar em Postgres e os testes em SQLite.
3. **Regra da organização — bugs viram issue no GitHub (`acidevbutter/butter_relay`)**: antes de começar rode `gh issue list --state all --limit 100`. Para cada defeito confirmado abaixo (e qualquer outro que você encontrar no caminho) abra **uma issue** descrevendo o problema, referencie-a nos commits (`refs #N`) e **feche (`gh issue close`, comentando o commit/PR) ao terminar o slice em que foi resolvida**. Defeitos já conhecidos para abrir (se ainda não existirem):
   - H-02: worker aceita `project_id` do produtor sem validar identidade (Celery direto no Redis).
   - Brevo 4xx permanente (400/401/403) é re-tentado 5x pelo Celery.
   - `SmtpEmailProvider` sem TLS e sem autenticação.
   - Chave Brevo única/global para todos os projetos; segredos de projeto sem criptografia.
   - Chaves de projetos `external` expiram em 30 dias (quebra integrações de servidor-a-servidor sem aviso).
   - Chaves de projeto têm poder total (criar chaves, publicar templates) — sem escopos.
   - `send()` do provider só aceita 1 destinatário, sem reply-to/cc/bcc/texto/headers/anexos.
4. **Slices pequenos, um commit (ou PR) por slice**, com testes e quality gate verde (`.github/workflows/backend-quality.yml`: ruff, mypy e pytest — use Python 3.11 + Poetry). Não avance de slice com teste vermelho. Não quebre produtores existentes (`send_task` com `project_id/template_key/to/payload` e a fila `relay-shared` continuam funcionando).
5. Não invente credenciais, hosts ou chaves. Segredos só por env; nunca commitar `.env`.

## Objetivo

Hoje o relay só recebe por `send_task` direto no Redis, só envia a partir de template do próprio relay, SMTP é sem TLS/login e a chave Brevo é global. Precisamos que **qualquer cliente externo** (começando pelo plugin WordPress da EDIY) envie e-mail por **HTTPS**, com chave própria e isolada, e que o **cadastro do cliente, dos tipos de e-mail e dos templates possa ser feito inteiro por um JSON (manifesto)**.

## Slices

### S1 — Provider: erros classificados, TLS/auth e envio completo
- `EmailProvider.send(...)` passa a receber um dataclass `OutboundEmail`: `to: list[str]`, `cc`, `bcc`, `reply_to`, `from_email`, `from_name`, `subject`, `html`, `text | None`, `headers: dict[str,str]`, `attachments: list[Attachment(filename, content_type, data: bytes)]`. Atualize `BrevoEmailProvider` (campos `to/cc/bcc/replyTo/textContent/headers/attachment`) e `SmtpEmailProvider` (multipart/alternative, anexos, `Reply-To`, `Message-ID` próprio).
- `SmtpEmailProvider`: `security: "none" | "starttls" | "ssl"`, `username`, `password`, timeout configurável. `none` só é aceito em `environment=development`.
- Classificação: `EmailSendError(detail, retryable: bool)`. **Permanente (sem retry)**: Brevo 400/401/403/404/422; SMTP 5xx (inclui 550/553 destinatário recusado, 535 auth). **Transitório (retry com backoff)**: timeout, erro de rede, Brevo 408/429/5xx, SMTP 4xx. `autoretry_for` do Celery deve reter só os transitórios; os permanentes viram `failed` direto, `last_error` sem vazar segredos.
- **Segredos por projeto criptografados**: novo `app/core/secrets.py` (Fernet/`MultiFernet`, chaves em env `RELAY_SECRETS_KEYS` separadas por vírgula, a primeira cifra, todas decifram → rotação). Obrigatório fora de `development` (validar em `Settings`, como já é feito para `BREVO_API_KEY`). Coluna nova `projects.delivery_secrets_enc` (Text, nullable) com JSON cifrado `{ "smtp_password": ..., "brevo_api_key": ... }`. Fábrica `get_email_provider_for_project` recebe os segredos decifrados; Brevo usa a chave do projeto e **cai na global** se não houver. Nenhuma API/log/admin devolve segredo: só `has_smtp_password`, `has_brevo_api_key`.
- `delivery_config` (não secreto) passa a aceitar `smtp_host`, `smtp_port`, `smtp_security`, `smtp_username`, `timeout_seconds`.
- Testes: classificação por status/código, TLS/ssl/starttls (smtp server fake via `aiosmtpd` ou mock), cifra/rotação, que segredos nunca aparecem em response/log.

### S2 — Catálogo de tipos de e-mail e templates por upload
- Nova tabela/domínio `email_types/` (padrão fastapi-builder): `id, project_id FK, key (único por projeto, regex ^[a-z0-9][a-z0-9._-]{1,99}$), description, category ("transactional"|"marketing"|"system"), mode ("template"|"raw"|"both"), locale, sample_payload JSON, is_active, created_at`. Um tipo `template`/`both` referencia a `key` de template do mesmo nome (reusa `templates_`, versionado). **O relay passa a saber exatamente quais tipos cada cliente envia**; enviar tipo não declarado → 422 `unknown_email_type`, salvo `projects.allow_undeclared_types` (default `false`).
- Rotas (chave do projeto, escopo `read`): `GET /api/v1/email-types` (auto-descoberta pelo cliente).
- **Upload de template HTML**: `POST /api/v1/templates/upload` (multipart: `file` .html até 512 KB, `key`, `subject_template`, `variables_schema` JSON opcional, `activate` bool). Reusa a validação existente (Jinja sandbox, JSON Schema, regex da key; 422 em vez de ativar versão quebrada) e **só cria versão nova se o conteúdo mudou** (hash de subject+html+schema). Escopo `manage`.
- Admin UI: aba "Tipos de e-mail" (lista, ativar/desativar, ver `sample_payload`, preview com o sample), campo de upload de arquivo na publicação de template.

### S3 — Escopos de chave e chaves para clientes externos
- `api_keys.scopes` (JSON list, default `["send","read","manage"]` nas chaves **existentes** para não quebrar ninguém). Dependência `require_scope("send"|"read"|"manage")` em `app/api_keys/dependencies.py`; aplicar em todas as rotas `/api/v1/*` atuais (`manage` para criar/revogar chaves e publicar/ativar templates; `read` para leituras; `send` só para envio).
- Chaves de cliente externo nascem com `["send","read"]`. **Expiração configurável**: `expires_in_days: int | null` (null = não expira) no cadastro, no manifesto e no admin; remover o "30 dias fixo" para `external` (mantê-lo apenas como *default* sugerido no admin).
- **Rotação sem downtime**: `POST /api/v1/admin/clients/{slug}/keys/rotate` cria nova chave e mantém a antiga válida até `revoke` explícito (ou `grace_hours`). Chave em claro **só na resposta de criação**; armazenar apenas o hash (como hoje). `last_used_at` continua atualizado.
- Rate limit por chave (Redis INCR por minuto): default `RELAY_DEFAULT_RATE_PER_MINUTE=120`, override por projeto (`limits.rate_per_minute`); 429 com `Retry-After`.

### S4 — Rota HTTPS externa de envio (núcleo)
`POST /api/v1/messages/send` — `Authorization: Bearer <chave>`, escopo `send`, **`project_id` sempre derivado da chave** (nunca do body; fecha o H-02 para quem usar HTTP). Header opcional `Idempotency-Key` (≤ 100 chars).

Body (JSON), dois modos:
```json
{
  "type": "woocommerce.customer_processing_order",
  "to": ["cliente@exemplo.com"],
  "cc": [], "bcc": [], "reply_to": "contato@ediyporn.com",
  "subject": "Seu pedido #123", "html": "<p>…</p>", "text": "…",
  "headers": {"X-Entity-Ref-ID": "123"},
  "attachments": [{"filename": "nota.pdf", "content_type": "application/pdf", "content_base64": "…"}],
  "payload": {"order_id": 123},
  "metadata": {"source": "wp-plugin/0.1.0", "site": "ediyporn.com"}
}
```
- **Modo template**: `type` (tipo `template`/`both`) + `payload` validado pelo `variables_schema`; sem `subject/html`.
- **Modo raw**: `type` (tipo `raw`/`both`) + `subject` + `html` (+`text`). O relay **não** re-renderiza.
- Limites: ≤ 50 destinatários somados, subject ≤ 500, html ≤ 1 MB, anexos ≤ 2 MB no total (base64 decodificado), body total ≤ 3 MB → 413. `from` **não** é aceito: usa o remetente do projeto (`default_sender_*`); `reply_to` é permitido.
- Fluxo: valida → rate limit → **cria `Message` (status `pending`) antes de enfileirar** → `celery_app.send_task(TASK_NAME, kwargs={…}, task_id=str(message.id), queue=project.queue_name)` → responde **202** `{message_id, status:"pending", type}`. Com o `broker_message_id` = id da mensagem, o worker atual já encontra a linha pré-criada (`get_by_broker_message_id`). Se o enqueue falhar (Redis fora): marcar `failed` com `enqueue_failed`, responder **503** com `Retry-After`; repetir com a mesma `Idempotency-Key` reenfileira em vez de duplicar.
- **Idempotência**: coluna nova `messages.idempotency_key` + índice único parcial `(project_id, idempotency_key)` onde não nulo (não usar `broker_message_id` — há duplicatas legadas). Mesma chave + mesmo corpo → devolve a mensagem existente (200); mesma chave + corpo diferente → 409.
- Colunas novas em `messages` (todas nullable): `email_type`, `recipients` JSON (`{to,cc,bcc}`), `reply_to`, `raw_subject`, `raw_html`, `raw_text`, `headers` JSON, `source`, `metadata` JSON. `to_email` = primeiro destinatário (compatível com filtros atuais). Nova tabela `message_attachments(id, message_id FK, filename, content_type, size, data LargeBinary)`; **purgar anexos assim que `sent`** e corpos (`raw_html/raw_text/payload`) após `MESSAGE_BODY_RETENTION_DAYS` (default 30; comando CLI/`celery beat` simples) — são dados pessoais (LGPD).
- **Worker**: aceitar o novo task `kwargs` (`message_id`) sem remover a assinatura legada (`project_id/template_key/to/payload`). Se a `Message` tem `raw_html`, **pula o render** e envia como está; usa `OutboundEmail` completo; aplica `settings.is_recipient_allowed` por destinatário; respeita as classes permanente/transitório da S1. Mensagem de projeto inativo → `failed` com motivo claro (hoje retorna silencioso).
- **Caminho legado (H-02)**: nova setting `ALLOW_LEGACY_SEND_TASK` (default `true` para não quebrar produtores atuais). Quando `false`, o worker só processa tasks com `message_id` existente cujo `project_id` bate com a fila de origem. Documente no README que o legado deve ser desligado assim que os produtores migrarem.
- Exposição HTTPS: documentar no `docker-compose.hostinger.yml`/README o domínio público via Coolify (TLS). Adicionar middleware opcional `ADMIN_ALLOWED_HOSTS` (lista de hosts): `/admin` e `/api/v1/admin/*` só respondem nesses hosts (ex.: `relay-admin.devbutter…`), enquanto o host público serve apenas `/health`, `/api/v1/messages/*`, `/api/v1/email-types`, `/api/v1/templates/*`. Nenhuma rota pública pode vazar stack trace; CORS fechado (sem `Access-Control-Allow-Origin`).

### S5 — Cadastro de clientes e manifesto JSON
Autenticação das rotas admin por **service token**: `RELAY_ADMIN_TOKEN_HASH` (sha256 em env, comparação em tempo constante) via `Authorization: Bearer`, **ou** sessão de admin já existente. Dependência `CurrentAdminOrServiceToken` em `app/auth/dependencies.py`; as rotas atuais `/api/v1/projects*` também passam a aceitá-lo (resolve o "token de serviço" pendente em `GUIA_FLUXO` §6).

Rotas (`/api/v1/admin`):
- `POST /clients` → cria cliente externo (`Project` com `external=true`), remetente, canal de entrega e **primeira chave** (resposta única com a chave em claro).
- `GET /clients`, `GET /clients/{slug}`.
- `POST /clients/{slug}/keys`, `POST /clients/{slug}/keys/{id}/revoke`, `POST /clients/{slug}/keys/rotate` (S3).
- `GET /manifest/schema` → JSON Schema do manifesto (para o cliente/CI validar offline).
- `PUT /clients/{slug}/manifest?dry_run=true|false&prune=true|false` → **upsert idempotente** de tudo.
- `GET /clients/{slug}/manifest` → exporta o estado atual (segredos **omitidos**, só `has_*`).

Formato do manifesto (v1) — implemente com Pydantic, `extra="forbid"`:
```json
{
  "manifest_version": 1,
  "client": {
    "name": "EDIY Porn", "slug": "ediy", "external": true, "is_active": true,
    "sender": {"email": "no-reply@ediyporn.com", "name": "Ediyporn", "reply_to": "info@ediyporn.com"},
    "delivery": {
      "provider": "smtp",
      "smtp": {"host": "…", "port": 465, "security": "ssl", "username": "…", "password": "<write-only>"},
      "brevo": {"api_key": "<write-only>"}
    },
    "limits": {"rate_per_minute": 120, "max_attachment_bytes": 2097152},
    "allow_undeclared_types": false
  },
  "email_types": [
    {"key": "woocommerce.customer_processing_order", "description": "…", "category": "transactional",
     "mode": "raw", "locale": "pt-BR", "sample_payload": {}},
    {"key": "welcome", "mode": "template", "locale": "pt-BR",
     "template": {"subject": "Olá {{ name }}", "html": "<html>…</html>",
                  "variables_schema": {"type":"object","required":["name"],"properties":{"name":{"type":"string"}}}},
     "sample_payload": {"name": "Ana"}}
  ],
  "keys": [{"label": "wordpress-prod", "scopes": ["send","read"], "expires_in_days": null}]
}
```
Regras do `PUT`:
- **Idempotente**: aplicar o mesmo manifesto duas vezes não muda nada (templates só ganham versão nova se o hash mudar; tipos só atualizam campos diferentes; chaves só são criadas se o `label` ainda não existir ativo).
- **Atômico**: tudo numa transação; qualquer erro de validação → 422 com lista de erros por caminho (`email_types[3].template.html: …`) e **nada** é gravado.
- `dry_run=true` devolve o diff (`created/updated/unchanged/deactivated` por tipo, template e chave) sem gravar. `prune=true` desativa (não apaga) tipos que não estão no manifesto; sem `prune`, tipos ausentes são mantidos.
- Segredos do manifesto (`password`, `api_key`) entram só no `PUT`, são cifrados (S1) e **nunca** voltam. Chaves novas aparecem em claro **apenas** na resposta do `PUT` que as criou (`keys_created[].plaintext_key`).
- Cria a fila do cliente e chama `add_consumer_for_project` quando o projeto é novo.
- Admin UI: no projeto, "Importar manifesto" (textarea/upload + dry-run mostrando o diff + aplicar) e "Exportar manifesto".
- Exemplo real para testar: `docs/examples/ediy.manifest.json` (copie de `ediy_v2/docs/relay/ediy.manifest.json`; todos os tipos são `raw`, sem templates; `delivery` = Brevo com **conta Brevo própria do EDIY** (chave só no segredo, write-only; o exemplo usa o placeholder `${EDIY_BREVO_API_KEY}`, que o importador deve rejeitar se não for substituído). Nunca usar a chave global para o EDIY).

### S6 — Documentação, smoke test e fechamento
- Atualizar: `README.md` (arquitetura, novas rotas, env), `.env.example` (`RELAY_SECRETS_KEYS`, `RELAY_ADMIN_TOKEN_HASH`, `RELAY_DEFAULT_RATE_PER_MINUTE`, `ADMIN_ALLOWED_HOSTS`, `ALLOW_LEGACY_SEND_TASK`, `MESSAGE_BODY_RETENTION_DAYS`), `docs/API_SPEC.md`, `docs/API_ROUTES_CURL.md`, `docs/GUIA_FLUXO_E_IMPLEMENTACAO.md` (§4 e §6), `CHANGELOG.md` (Unreleased).
- Novo guia `docs/CLIENTES_EXTERNOS.md`: onboarding de um cliente em 5 passos (gerar manifesto → dry-run → apply → guardar chave → configurar plugin/SDK) + exemplo `curl` de envio e de import do manifesto.
- Estender `scripts/curl_smoke_test.sh`: cria cliente por service token, importa manifesto (dry-run + apply), envia raw e template por HTTPS, confere 202, idempotência (reenvio = mesma mensagem), 401/403 (sem chave, sem escopo), 413, 429 e que o e-mail chega no Mailpit com reply-to/anexo.
- Fechar as issues resolvidas e listar no PR as que ficaram abertas (ex.: webhooks de bounce/entrega — **fora do escopo**; deixe `MessageStatus` extensível e abra issue de melhoria).

## Testes obrigatórios (pytest + httpx.AsyncClient, SQLite em memória)
- Auth: sem Bearer → 401; chave revogada/expirada → 401; sem escopo → 403; chave de outro projeto nunca vê/gera mensagem de outro (isolamento multi-tenant); service token errado → 401.
- `/messages/send`: raw ok, template ok, tipo não declarado → 422, payload fora do schema → 422, limites (413), `from` ignorado/rejeitado, idempotência (mesma chave/mesmo corpo → mesma mensagem; corpo diferente → 409), enqueue falho → 503 e reenvio reenfileira, 202 com `message_id`.
- Worker: raw sem render, template com render, retry só em transitório, permanente → `failed` sem retry, destinatário bloqueado em não-produção, legado ainda funciona e `ALLOW_LEGACY_SEND_TASK=false` bloqueia.
- Manifesto: idempotência (2x = sem mudança), dry-run não grava, atomicidade em erro, prune, export sem segredos, chave em claro só na criação, versão de template só quando muda.
- Segredos: cifrados em repouso, nunca em response/log; rotação com `MultiFernet`.
- Rate limit 429 + `Retry-After`; middleware de hosts admin.

## Definição de pronto
1. `ruff`, `mypy`, `pytest` verdes; `alembic upgrade head` limpo em Postgres e `alembic heads` = 1.
2. `scripts/curl_smoke_test.sh` passa de ponta a ponta no compose local com Mailpit.
3. Manifesto da EDIY importado em `dry_run` e em apply sem erros; chave `send`-only gerada; envio raw via `curl` chega no Mailpit.
4. Issues abertas para cada defeito, referenciadas nos commits, **fechadas** as resolvidas; PR com resumo das decisões, riscos e o que ficou para depois.
5. Nada de segredo em logs, respostas, fixtures ou no repositório.

## Decisões que você pode tomar sem perguntar (documente no PR)
Formato exato do 503/Retry-After; limites numéricos acima (ajuste se algo for inviável); biblioteca de rate limit (preferir implementação simples sobre Redis já existente); nome das colunas dentro do que está descrito. **Pergunte** apenas se precisar mudar o contrato público descrito aqui.
