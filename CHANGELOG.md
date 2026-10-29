# Changelog

Registro retroativo baseado em todos os commits da branch `master`.

## Unreleased

- S5, cadastro de clientes e manifesto JSON (DVB-310, API; Admin UI de importar/exportar na tela do projeto,
  com preview do diff):
  - Token de serviço: `RELAY_ADMIN_TOKEN_HASH` (SHA-256 do token) autoriza `/api/v1/admin/*`
    via `Authorization: Bearer`, sem sessão de admin. Só essas rotas o aceitam; mensagens,
    templates e tipos de e-mail continuam exigindo chave de projeto.
  - `POST /api/v1/admin/clients`, chaves por slug (criar/listar/revogar/rotacionar; texto
    claro só na criação), `GET /manifest/schema`, `GET|PUT /clients/{slug}/manifest`.
  - `PUT` idempotente e atômico (uma transação), com `dry_run` (diff sem gravar) e `prune`
    (desativa tipos/templates ausentes, nunca apaga). 422 com `errors[].loc` por caminho.
    Segredos write-only; export nunca os devolve e pode ser reaplicado como está.

- S4, API HTTPS de envio (DVB-309):
  - `POST /api/v1/messages/send` com `Authorization: Bearer` e escopo `send`. O
    `project_id` vem sempre da chave (fecha o H-02 para quem usa HTTP); `from` não é
    aceito (campos extras dão 422).
  - Modo template (`type` + `payload`, validado contra o schema do template) e modo raw
    (`subject` + `html` [+ `text`]); `to`/`cc`/`bcc`, `reply_to`, `headers` e anexos em
    base64. Tipo não declarado: 422 `unknown_email_type` (salvo `allow_undeclared_types`).
  - Limites: 50 destinatários, HTML 1 MB, anexos 2 MB, corpo 3 MB (413 antes de
    interpretar o JSON).
  - A `Message` é gravada antes de ir para a fila; resposta 202 `{message_id, status,
    type}`. Se o enqueue falhar: 503 `enqueue_failed` com `Retry-After`, e repetir a
    mesma `Idempotency-Key` reenfileira a mesma mensagem.
  - `Idempotency-Key`: mesma chave e mesmo corpo devolvem a mesma mensagem (200); corpo
    diferente dá 409. Índice único parcial `(project_id, idempotency_key)`.
  - Migration `c7e3a9b5d1f4`: colunas em `messages` (destinatários, assunto/HTML/texto
    raw, headers, origem, metadata, hash da requisição) e tabela `message_attachments`.
  - Worker: nova task kwarg `message_id` (processa a mensagem gravada) sem quebrar o
    `send_task` legado; `ALLOW_LEGACY_SEND_TASK=false` recusa o legado.
  - `ADMIN_ALLOWED_HOSTS`: com a variável definida, o host público só responde
    `/health` e a API de cliente; `/admin` e a API de serviço dão 404 fora dos hosts
    de admin. Não há CORS habilitado.
  - Retenção (LGPD): a limpeza de corpos agora também apaga HTML/texto raw e anexos.

- S3, escopos, validade, rotação e rate limit (DVB-308):
  - `api_keys.scopes` (`send`/`read`/`manage`; migration `b5d1f7a9c3e4` dá os três às
    chaves existentes) e `require_scope` em todas as rotas `/api/v1/*`: leituras pedem
    `read`; chaves, templates (inclusive upload) e canal de entrega pedem `manage`.
    Sem o escopo, 403.
  - Chaves de projetos externos nascem `send+read` e sem validade; `expires_in_days` é
    configurável (o "30 dias fixo" virou só sugestão no admin).
  - Rotação sem downtime: `POST /api/v1/api-keys/{id}/rotate` (e botão no admin) cria
    chave nova com o mesmo label e escopos; a antiga vale até ser revogada ou até
    `grace_hours`.
  - Rate limit por chave no Redis (`RELAY_DEFAULT_RATE_PER_MINUTE=120`, override em
    `projects.rate_per_minute`): 429 com `Retry-After` e `code: rate_limited`. Se o
    Redis cair, o limite é liberado em vez de bloquear.

- S2, catálogo de tipos e upload (DVB-307):
  - Domínio `email_types` (migration `a3c9e5f7b1d2`) com chave por projeto, categoria
    (`transactional`/`marketing`/`system`), modo (`template`/`raw`/`both`), locale e
    `sample_payload`, além de `projects.allow_undeclared_types`.
  - `GET /api/v1/email-types` para auto-descoberta (só tipos ativos do próprio projeto).
    Tipo não declarado no envio gera 422 `unknown_email_type` (aplicado na S4).
  - `POST /api/v1/templates/upload` (multipart, .html até 512 KB, UTF-8): 201 cria
    versão, 200 quando o conteúdo é o mesmo (hash de subject + html + schema); upload
    inválido gera 422 sem tocar na versão ativa; `activate=false` cria rascunho.
  - 422 da API passa a ter `code` opcional.
  - Admin: card "E-mail types" (cadastrar, ativar/desativar, preview com o sample),
    upload de .html na publicação de template e checkbox de tipos não declarados.

- Validação de configuração (DVB-206, M-03):
  - `DEBUG=true` fora de development é recusado no boot (desligaria o cookie de sessão
    seguro).
  - `BREVO_API_KEY` deixa de ser obrigatória: no startup a API loga `brevo_key_missing`
    listando os projetos Brevo ativos sem chave própria, e o envio falha de forma
    permanente com uma mensagem clara.
  - `docker-compose.hostinger.yml` passa `RELAY_SECRETS_KEYS` (e `SESSION_SECRET` no
    worker) só a partir do ambiente, sem default.

- S1, provider completo (DVB-306):
  - `EmailProvider.send(OutboundEmail)` com to/cc/bcc, reply-to, HTML + texto, headers e
    anexos, no Brevo e no SMTP. O SMTP monta multipart/alternative com Message-ID próprio.
  - SMTP com `smtp_security` (`none` só em development; fora dele o padrão é `starttls`),
    `ssl`, `smtp_username` e senha.
  - Segredos por projeto cifrados com MultiFernet (`RELAY_SECRETS_KEYS`, obrigatório fora
    de development; migration `f1a8c4d6b3e9`). A chave Brevo do projeto tem prioridade
    sobre a global. Admin e API só mostram `has_smtp_password`/`has_brevo_api_key`.
  - `EmailSendError.retryable`.

- Proteção de dados (DVB-208, M-05):
  - `GET /api/v1/messages*` não devolve mais o `payload`, mascara o destinatário
    (`a***@dominio`) e redige o `last_error`.
  - Erro do Brevo guarda só o `code` e a mensagem redigida e truncada (300 caracteres),
    nunca o corpo inteiro; erro do SMTP também é redigido.
  - O log de destinatário bloqueado mascara o endereço.
  - Retenção: `MESSAGE_BODY_RETENTION_DAYS` (30). `python -m scripts.purge_message_bodies`
    (ou a task `butter_relay.purge_message_bodies` no `celery beat`) limpa o payload das
    mensagens finalizadas e o destinatário das rejeitadas.

- Rastreabilidade e limites (DVB-188):
  - Histórico de tentativas em `message_attempts` (migration `e7c3a5b8d2f6`), mostrado no
    detalhe da mensagem no admin.
  - Mensagem reentregue ainda em `processing` (worker caiu no meio do envio) vira falha
    terminal "delivery outcome unknown" em vez de ser enviada de novo: sem e-mail duplicado;
    o admin decide o Resend.
  - Limites: payload da task até `RELAY_MAX_PAYLOAD_BYTES` (64 KB, maior vai para
    `rejected_messages`), payload precisa ser objeto, assunto do template até 500 caracteres,
    HTML até 512 KB, `limit` da listagem de mensagens entre 1 e 200.
  - Dashboard: status das últimas 24h, falhas terminais, rejeitadas, presas em `processing`
    há mais de 15 min e backlog de cada fila no Redis.

- Mensagem malformada não some mais (DVB-456): task com `project_id` que não é UUID,
  ou sem `template_key`/`to`, é gravada em `rejected_messages` (migration `d2b9e6a4f1c8`;
  guarda motivo, project id enviado, template, destinatário e só as chaves do payload) e
  a task termina como FAILURE no Celery, com log `relay_message_rejected`, em vez de
  "succeeded". Nova página no admin: `/admin/rejected-messages`.

- Corrigido (#63): o `BlobClient` abre um `httpx.AsyncClient` por chamada. O cliente
  global ficava preso ao event loop da primeira task e as seguintes falhavam com
  "Event loop is closed".

- Falhas sempre registradas (DVB-344): a `Message` passa a ser criada antes da consulta
  ao Blob (falha do Blob aparece no log e o retry reaproveita a linha), o payload
  renderizado (com `cta_link`) é gravado, e qualquer exceção inesperada marca a
  mensagem como `failed` terminal (`unexpected error: <Tipo>`) em vez de deixá-la presa
  em `processing`.

- Idempotência e falha terminal de mensagens (DVB-457): `UNIQUE (project_id,
  broker_message_id)` em `messages` (migration `c5a8d1f3e7b2`, que deduplica linhas
  antigas antes de criar a constraint) e nova coluna `terminal_failure`. Erros
  permanentes do provider (Brevo 400/401/403/404/422, SMTP 5xx, destinatário recusado,
  canal desconhecido) e destinatário bloqueado pela política de não produção falham de
  vez, sem gastar retries; a última tentativa também marca a falha como terminal. Uma
  redelivery de mensagem com falha terminal é ignorada. Erros de transporte do Brevo
  (`httpx.HTTPError`) viram `EmailSendError` retentável em vez de exceção crua.

- Adicionado publicador idempotente dos sete templates transacionais `devbutter_*`
  por Project (`scripts/seed_devbutter_templates.py`). A operação cria somente
  templates sem versão ativa, usa `TemplateService` para validar Jinja e registra
  versões no Relay sem enviar e-mails; o script também está disponível na imagem da API.

- Admin (DVB-205): CSP restritiva com nonce por requisição nas páginas HTML
  (`script-src 'nonce-…' https://unpkg.com`, sem `unsafe-inline`/`unsafe-eval`,
  `frame-ancestors 'none'`, `object-src 'none'`), mais `X-Frame-Options`,
  `X-Content-Type-Options` e `Referrer-Policy` em toda resposta. Os handlers `onclick`
  foram trocados por `data-*` + um único script com nonce. Previews continuam em
  `iframe sandbox` e herdam a CSP. Testes cobrem script, evento inline, URL externa e
  payload que tenta escapar do atributo `srcdoc`.

- Removidos OpenTelemetry (traces, métricas e export OTLP de logs) e as linhas JSON
  `metric emitted` (`app/core/telemetry.py`, `app/core/metrics.py`, variáveis `OTEL_*`,
  dependências `opentelemetry-*`): o export de métricas ao New Relic falhava com `401` e
  poluía os logs de produção. O stdout passa a ter só logs legíveis (uma linha por evento,
  `TerminalFormatter`), no API e no worker Celery. Falhas do provedor de e-mail continuam
  registradas como `email_provider_error` (`provider=brevo|smtp`), sem endereços nem corpos.

- Docs: novo `docs/GUIA_FLUXO_E_IMPLEMENTACAO.md` (fluxo validado, canal de disparo por
  company, passo a passo do relay e checklist do que falta no admin/backend DevButter);
  README, `.env.example` (`SMTP_HOST`/`SMTP_PORT`) e `API_ROUTES_CURL.md` atualizados. O log
  de mensagens do admin mostra o canal usado.

- O worker Celery passa a usar o mesmo `setup_logging` da API (sinal `setup_logging` do
  Celery), emitindo logs/métricas estruturados com `service`/`environment` (#14).

- Novos `GET/PUT /api/v1/projects/{id}/delivery`, autenticados pela API key do próprio
  projeto: o admin do DevButter (ou o app dono) lê e troca o canal de disparo da sua
  company sem sessão de admin do relay. Só `delivery_provider` é gravável por chave;
  host/porta SMTP e remetente continuam restritos ao admin.

- Admin: a página do projeto ganhou "Settings & delivery channel" (nome, remetente, canal
  Brevo/SMTP, host/porta SMTP, externo, ativo), envio de teste pelo canal do projeto
  (`POST /admin/projects/{id}/test-send`), criação de projeto já com o canal, e gestão de
  templates com `variables_schema`, "Edit as new version" e "Activate" (rollback).
- Admin: formulários nunca mais devolvem 500/JSON cru — entrada inválida, slug duplicado e
  falhas do provider re-renderizam a página com a mensagem e os valores digitados (#37).
  O usuário admin da requisição é destacado da sessão (o rollback de um conflito derrubava
  a renderização com `MissingGreenlet`). Reenvio manual passou a respeitar a política de
  destinatários de não-produção e a marcar falhas de render/provider como `failed`.
- Admin: previews de template em `iframe sandbox` (H-04 da auditoria).

- Templates: a publicação agora valida o Jinja2 (assunto e HTML), o `variables_schema`
  (JSON Schema) e o formato da `key` (422 em vez de ativar uma versão quebrada); erros de
  renderização do Jinja viram `ValidationFailedError` (#38). Novo
  `POST /api/v1/templates/{id}/activate` para voltar a uma versão anterior.

- Corrigido: cada retry do Celery criava uma nova linha `Message` com o mesmo
  `broker_message_id`; na tentativa seguinte `MultipleResultsFound` derrubava a
  mensagem antes de `MAX_RETRIES`. Agora há uma única linha por mensagem lógica,
  com `attempts` contando as tentativas (#35).

- Cada company (Project) passa a definir o próprio meio de disparo: novos campos
  `Project.delivery_provider` (`brevo` por padrão, inclusive em desenvolvimento —
  o Mailpit deixou de ser forçado pelo ambiente) e `Project.delivery_config`
  (`smtp_host`/`smtp_port`, sem segredos), expostos em `POST/PATCH /api/v1/projects`.
  O worker e o reenvio do admin resolvem o provider pelo projeto
  (`get_email_provider_for_project`), e cada `Message` registra o `provider` usado.
  Falhas de conexão SMTP agora viram `EmailSendError` (mensagem marcada `failed` e
  reenfileirada) em vez de uma exceção crua. Migration `b7d4e2a91c3f`.
- Corrigido: o worker do `docker-compose.yml` não montava `./alembic` e não subia após
  qualquer migration nova (#33).

- Corrigido: o worker perdia toda mensagem após a primeira de cada processo Celery
  (`RuntimeError: ... attached to a different loop`), porque cada task usa um event
  loop novo mas o pool asyncpg do engine global sobrevivia à task anterior. O engine
  agora é descartado ao fim de cada task (`process_message_isolated`, #29).
- Corrigido: após reiniciar/deploy, o worker só consumia `relay-shared`; o broadcast
  `add_consumer` feito dentro de `worker_ready` nunca chegava ao próprio worker. As
  filas por projeto agora são anexadas direto ao consumer que está subindo (#30).

- Probes de saúde não geram mais logs ou métricas de requisição. O terminal
  passa a exibir uma linha curta por evento, enquanto o handler OTLP envia os
  atributos estruturados diretamente ao New Relic quando há endpoint configurado.

- Adicionada instrumentação OpenTelemetry de FastAPI, HTTPX e SQLAlchemy, com
  traces e métricas APM enviados por OTLP/HTTP diretamente ao New Relic quando
  `OTEL_ENABLED=true`. O stdout continua legível em logfmt e separado do canal
  OTLP; nenhuma chave de licença é persistida no repositório.

- Removida a dependência `aws-embedded-metrics` (não utilizada desde a
  migração de `core/metrics.py` para logs JSON estruturados); `poetry.lock`
  regenerado, removendo também `aiohttp` e suas dependências transitivas, que
  só existiam por causa dela. Docstrings que ainda descreviam o pipeline
  antigo (ECS `awslogs` → assinatura do CloudWatch Logs) foram corrigidas para
  descrever o pipeline atual (stdout → New Relic `nrdot-collector` na VPS).

- Logging de requisições HTTP passa a ser JSON estruturado (`timestamp`, `level`,
  `logger`, `event`, `service`, `environment`, `request_id`, `method`, `route`,
  `status_code`, `duration_ms`), com exatamente um evento de conclusão
  (`request_completed`/`request_failed`) por requisição. O logger
  `uvicorn.access` foi silenciado (`WARNING`, `propagate=False`) para eliminar
  a duplicata que ele produzia ao lado do log da própria
  `RequestContextMiddleware`. Nenhum payload, header ou segredo é incluído no
  log de erro (`app/settings/logging_config.py`, `app/settings/middleware.py`).

- Conexão Postgres passa a ser `DATABASE_HOST` / `PORT` / `USER` / `PASSWORD` /
  `NAME`; o Pydantic monta `postgresql+asyncpg://`. `DATABASE_URL` residual
  impede o boot fora de development.

- Adicionado `docker-compose.hostinger.yml` com `api` + `worker` no mesmo Postgres `relay` para o Coolify staging.

- Adicionado Mailpit ao Compose de desenvolvimento, com SMTP na porta `1025` e interface web na porta `8025`.

- Adicionada `docs/API_SPEC.md`: mapa da API v1, dashboard e worker do Relay.

- Adicionada restrição de destinatários para Relay em staging e pré-produção.

## [0.1.0] — 2026-08-21

### Adicionado

- Serviço Butter Relay com FastAPI, SQLAlchemy assíncrono, Alembic e Poetry.
- Projetos multi-tenant, API keys, templates Jinja2 versionados e log de mensagens.
- Autenticação administrativa por bcrypt e sessão assinada.
- Worker SQS/ElasticMQ com renderização de templates e provider Brevo.
- Dashboard administrativo, preview e reenvio manual de mensagens.
- Testes unitários, testes HTTP e script de smoke test via curl.
- Dockerfile, Docker Compose local e diagramas de arquitetura/fluxo.

### Alterado

- Dashboard passou a oferecer links para o detalhe de cada mensagem e visualização do e-mail renderizado.
- API e worker passaram a usar o PostgreSQL compartilhado `shared-postgres`.
- Compose deixou de provisionar o PostgreSQL local próprio e passou a usar a rede externa `devbutter_shared`.
- Adicionado Docker Compose Watch para sincronização de código da API, worker e migrations.

### Commits incluídos

- `8c67afd` — inicialização do projeto, aplicação, testes e infraestrutura local.
- `68cc684` — detalhe de mensagem e melhorias de navegação do dashboard.
- `9602fdb` — PostgreSQL compartilhado e limpeza do Docker Compose.

Não foi criada tag Git para esta versão.
