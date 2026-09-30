# Changelog

Registro retroativo baseado em todos os commits da branch `master`.

## Unreleased

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
