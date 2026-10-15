# Changelog

Registro retroativo baseado em todos os commits da branch `master`.

## Unreleased

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
