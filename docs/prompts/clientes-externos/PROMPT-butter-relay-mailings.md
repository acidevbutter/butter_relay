# Prompt — butter_relay: mailings (marketing) para clientes externos via plugin WordPress

> Cole este prompt numa sessão do Claude Code aberta na raiz de `butter_relay`, numa branch nova.
> **Pré-requisito:** este prompt continua o `PROMPT-butter-relay-clientes-externos.md` (slices S1–S5: provider com TLS/segredos cifrados, escopos de chave, `POST /api/v1/messages/send`, tipos de e-mail e manifesto). Contexto do cliente: `docs/analise-butter-relay.md` e `docs/dossie-ediy-v1.md` §14 (repo ediy_v2). Primeiro cliente: **EDIY** (WordPress/WooCommerce na GoDaddy, plugin próprio, ~254 assinantes ativos e ~3,7 mil usuários).

---

## Papel e regras de trabalho

Você é o engenheiro responsável por evoluir o **butter_relay** (FastAPI async + SQLAlchemy 2 + Alembic + Celery/Redis + Jinja2/HTMX). Antes de codar, leia: `README.md`, `AGENTS.md`, `docs/GUIA_FLUXO_E_IMPLEMENTACAO.md`, `docs/butter-relay-fluxos.md`, `docs/auditoria-seguranca-2026-08-29.md`, `app/worker/tasks.py`, `app/core/email_provider.py`, `app/api_keys/*`, `app/projects/*`, `app/templates_/*`, `app/messages/*`, `app/email_types/*` (se existir), `app/admin/router.py`.

0. **Verifique os pré-requisitos primeiro.** Confirme no código que existem: `OutboundEmail` + SMTP com TLS/auth + segredos cifrados (S1), `api_keys.scopes` e rate limit (S3), `POST /api/v1/messages/send` com `Message` pré-criada (S4), `email_types` e manifesto (S2/S5). Liste o que falta. Se faltar algo, **pare e me diga** (não reimplemente por conta própria algo que o outro prompt cobre) — a menos que o item seja pequeno e você documente no PR.
1. **Siga o padrão fastapi-builder-api** (skill `fastapi-builder`): cada domínio novo tem `router.py`, `schemas.py`, `models.py`, `service.py`, `repository.py`, `dependencies.py`; camadas router → service → repository; DI só em `dependencies.py` (nunca importar `router` de outro domínio; `core/dependencies.py` só tem `DbSession`); exceções via `app/core/exceptions.py`; logs com `@log_call`/`@log_errors`, **sem e-mail de destinatário nem corpo nos logs**.
2. **Alembic**: nome timestamped (`YYYYMMDD_HHMM_<rev>_<slug>.py`), **um único head** (`alembic heads` = 1). Use o CLI `app db` (Typer) se existir. Migrations rodam em Postgres; testes em SQLite.
3. **Regra da organização — todo bug vira issue no GitHub (`acidevbutter/butter_relay`)**: antes de começar rode `gh issue list --state all --limit 100`. Para cada defeito confirmado (os abaixo e qualquer outro que achar no caminho) abra **uma issue**, referencie nos commits (`refs #N`) e **feche (`gh issue close`, comentando o commit/PR) ao terminar o slice em que foi resolvido**. Defeitos/lacunas já conhecidos para abrir (se ainda não existirem):
   - Fila única por projeto: um envio em massa bloquearia os e-mails transacionais (senha, pedido).
   - Não há lista de supressão nem tratamento de bounce/complaint (status para em `sent`).
   - Não há descadastro (`List-Unsubscribe`) nem registro de consentimento.
   - Provider sem noção de categoria (transacional × marketing): nada impede enviar marketing por canal que só aceita transacional.
4. **Slices pequenos, um commit/PR por slice**, quality gate verde (`.github/workflows/backend-quality.yml`: ruff, mypy, pytest; Python 3.11 + Poetry). Não avance com teste vermelho. **Não quebre** `send_task` legado, `POST /messages/send` nem a fila `relay-shared`.
5. Segredos só por env/cifrados; nunca inventar credenciais, hosts ou chaves; nunca commitar `.env`.

## Objetivo

Permitir que o **plugin WordPress da EDIY** (cliente fino) opere os mailings do site inteiramente pelo butter_relay, que passa a ser o **motor**: guarda contatos e consentimento, mantém listas e supressão, dispara campanhas em lotes com limite de vazão, aplica descadastro e devolve os eventos (descadastro, bounce, reclamação) ao WordPress. O WordPress **não** envia em massa, **não** guarda a lista de supressão como fonte da verdade e **não** fala com Celery/Redis — só HTTPS.

### Premissas do caso EDIY (não discuta, implemente respeitando)

- **Site adulto.** Decisão do Marcos: o canal Brevo da EDIY é **só transacional, sem conteúdo explícito**. Portanto o relay **precisa impedir** que marketing saia por um canal marcado como transacional-only (ver M1). O canal de marketing é outro e configurável (SMTP autenticado já suportado pela S1; outro provedor via adapter no futuro). **Não escolha nem hardcode provedor de marketing**; deixe `delivery.marketing` configurável e abra issue de melhoria para adapters.
- **Discrição do assinante é requisito de produto**: **sem pixel de abertura e sem reescrita de links por padrão** (tracking desligado), remetente/assunto neutros configuráveis por campanha, sem expor nome do site em anexos/nomes de arquivo.
- **LGPD**: só entra em lista de marketing quem tem consentimento registrado; o titular pode sair em 1 clique; existe rota de eliminação/anonimização. A decisão de reaproveitar a base atual do WordPress (legítimo interesse × opt-in) é do Marcos/jurídico: o relay **não importa ninguém como `subscribed` sem `consent`** (ver M2).

## Slices

### M1 — Categoria de envio, canais separados e fila isolada
- `EmailType.category` (`transactional|marketing|system`, já existe na S2) passa a **decidir o canal**: `delivery` do projeto vira `{"transactional": {...}, "marketing": {...}}` (retrocompatível: o formato plano atual é lido como `transactional`). Cada canal tem `provider`, config não secreta e segredos cifrados próprios; novo flag `allow_marketing: bool` (default `false` em Brevo, `true` apenas se o operador declarar) — o Brevo da EDIY fica `allow_marketing=false`.
- **Guard rail**: tentar enfileirar `category=marketing` (campanha, `/messages/send` com tipo marketing, teste) em canal sem `allow_marketing` → **422 `marketing_not_allowed_on_channel`** (nunca cair silenciosamente no canal transacional). O inverso também: tipo `transactional/system` nunca usa o canal de marketing.
- **Fila isolada**: marketing vai para `relay-<slug>-bulk` (consumer separado, concorrência e prefetch baixos), transacional continua em `relay-<slug>`. Ajuste `add_consumer_for_project` e o `docker-compose`/Coolify docs. Teste: 5.000 mensagens bulk na fila não atrasam uma transacional (asserção por fila/rota, não por tempo).
- Testes: resolução de canal por categoria, 422 do guard rail, retrocompatibilidade do `delivery` plano, rota de fila.

### M2 — Contatos, listas, consentimento e supressão (API para o plugin)
Domínios novos `contacts/`, `lists/`, `suppressions/` (padrão fastapi-builder).

- `contacts`: `id, project_id FK, email (normalizado lower/trim, único por projeto), name, external_id (id do usuário WP, único por projeto, nullable), locale, attributes JSON (ex.: `{"plan":"assinante","wp_roles":[...],"age_verified":true}`; ≤ 8 KB), status (`pending|subscribed|unsubscribed|bounced|complained|erased`), created_at, updated_at`. Índices por `(project_id,status)` e `(project_id,external_id)`.
- `consents` (1..n por contato): `contact_id, list_id, source ("wp-signup-form"|"wp-profile"|"checkout"|"import-optin"...), text_version, consented_at, ip_hash (HMAC com `RELAY_SECRETS_KEYS`, **nunca IP em claro**), user_agent_hash`, `revoked_at`. **Sem consentimento não existe `subscribed`** — a API rejeita (422 `consent_required`).
- `lists`: `id, project_id, key (^[a-z0-9][a-z0-9_-]{1,63}$), name, description, double_opt_in bool, is_active`; `list_members(list_id, contact_id, status, subscribed_at, unsubscribed_at)`.
- `suppressions`: `(project_id, email_hash/email, reason: unsubscribe|hard_bounce|complaint|manual|erasure, source, created_at)` — **por projeto e global ao projeto** (vale para todas as listas de marketing). Verificada **no momento do envio**, não só na entrada.
- Rotas (`/api/v1`, chave do projeto, escopo novo **`contacts`**; adicione `contacts` e `campaigns` ao catálogo de escopos da S3 e **não** os dê às chaves existentes por padrão):
  - `PUT /contacts/by-external-id/{external_id}` e `PUT /contacts/by-email` → upsert idempotente (mesmo corpo = sem mudança); aceita `subscriptions: [{list, consent:{source,text_version,consented_at,ip,user_agent}}]`.
  - `POST /contacts/bulk` → até 1.000 itens, resposta por item (`created|updated|unchanged|rejected` + motivo), atômico por item, idempotente. É o caminho do backfill do WordPress.
  - `GET /contacts/{id}`, `GET /contacts?list=&status=&q=&cursor=` (paginação por cursor, sem `OFFSET` em tabela grande).
  - `POST /contacts/{id}/unsubscribe` (do WP quando o usuário descadastra na área da conta) e `DELETE /contacts/{id}` → **eliminação LGPD**: anonimiza (`email`→hash, remove nome/attributes/consents/ip_hash), mantém só a linha de supressão com hash para não reenviar, `status=erased`, remove da campanha em andamento. Resposta 202 + registro auditável.
  - `GET/POST /lists`, `GET /lists/{key}` com contagens (`subscribed`, `pending`, `unsubscribed`, `bounced`).
- **Dupla confirmação (double opt-in)**: se `lists.double_opt_in`, o contato fica `pending`, o relay envia e-mail tipo `system` (template do cliente, `{{ confirm_url }}`) com token assinado e expira em 72 h; `GET /c/{token}` (público, sem sessão) confirma e grava o consentimento com `source` do pedido original.
- Limites: ≤ 1.000 itens por bulk, rate limit próprio de contatos (reusa o da S3, contador separado).
- Testes: upsert idempotente, `consent_required`, normalização de e-mail, isolamento multi-tenant (chave A nunca lê/atualiza contato do projeto B), supressão impede `subscribed`, bulk com itens ruins não derruba o lote, eliminação anonimiza e preserva a supressão.

### M3 — Descadastro e conformidade em todo marketing
- **Token de descadastro**: HMAC assinado (`RELAY_UNSUBSCRIBE_SECRET` em env, comparação em tempo constante, sem expiração, contendo `project_id, contact_id, list_id|campaign_id`).
- Rotas **públicas** (sem auth, sem cookie, sem JS obrigatório): `GET /u/{token}` → página mínima de confirmação com botão (evita descadastro por *prefetch* de scanner); `POST /u/{token}` → **one-click RFC 8058** (aceita o corpo `List-Unsubscribe=One-Click`) e a confirmação da página; resposta idempotente. Efeito: `list_member.status=unsubscribed`, cria `suppressions(reason=unsubscribe)` quando for "sair de tudo", dispara evento `contact.unsubscribed` (M5). Página em pt-BR, neutra (sem marca explícita), com link "voltar atrás" (re-inscrever exige novo consentimento).
- **Injeção automática em toda mensagem `category=marketing`** (o remetente do plugin não controla isso): headers `List-Unsubscribe: <https://…/u/{token}>, <mailto:…>` e `List-Unsubscribe-Post: List-Unsubscribe=One-Click`; rodapé obrigatório no HTML e no texto (variável `{{ unsubscribe_url }}`, mais identificação do remetente e contato configurados no projeto). Se o corpo da campanha não contiver `{{ unsubscribe_url }}`, o relay **acrescenta** o rodapé padrão; campanha só pode ser agendada se o projeto tiver `legal.sender_identity` preenchido (422 `legal_footer_missing`).
- `mailto:` de descadastro: opcional atrás de flag (`UNSUBSCRIBE_MAILTO_ENABLED`); se desligado, só o link HTTPS.
- Domínios públicos: `/u/*`, `/c/*` e `/api/v1/messages|contacts|campaigns|events` entram na lista pública do `ADMIN_ALLOWED_HOSTS` (S4); `/admin*` continua restrito. CORS fechado.
- Testes: token adulterado → 404 neutro (sem vazar existência), POST one-click idempotente, GET não descadastra, headers presentes só em marketing, rodapé inserido quando ausente, `legal_footer_missing`, descadastro de um projeto nunca afeta outro.

### M4 — Campanhas, despachante e controle de vazão
Domínio `campaigns/`.

- `campaigns`: `id, project_id, name, email_type_key (category=marketing), list_ids/segment (JSON: listas + filtro simples por `attributes`, ex.: `{"plan":"assinante"}`; operadores `eq|in|exists`, sem SQL livre), mode ("template"|"raw"), subject, html, text, from_name_override (só nome, **nunca** endereço), reply_to, status (`draft|scheduled|sending|paused|completed|canceled|failed`), scheduled_at, throttle JSON (`per_minute`, `daily_cap`), counters (queued/sent/failed/skipped), created_by, created_at`. **O corpo da campanha fica na campanha e é renderizado por destinatário na hora de enviar** (Jinja sandbox com `contact.{name,attributes}`, `unsubscribe_url`, `campaign`); `Message` da campanha **não** duplica `raw_html` (guarda só `campaign_id, contact_id`).
- `campaign_recipients` (ou reuse `messages` com `campaign_id`): **unicidade `(campaign_id, contact_id)`** — reexecução, retry ou re-despacho **nunca** envia 2× ao mesmo contato.
- **Despachante** (Celery): ao agendar/iniciar, expande destinatários em **lotes por cursor** (500 por lote), filtrando a cada lote: `subscribed` ∧ membro da lista ∧ **não suprimido** ∧ não `erased`; mensagem ignorada vira `skipped` com motivo (`suppressed|unsubscribed|invalid_email`). Antes de cada lote relê `campaign.status` (pause/cancel valem em ≤ 1 lote).
- **Vazão**: token bucket em Redis por projeto/canal (`marketing_rate_per_minute`, default 60) + `daily_cap`; **warm-up opcional** (`warmup`: cap diário que cresce, ex.: 200→500→1.000…) pois o domínio da EDIY é novo para envio em massa. Respeitar 429/4xx transitório do canal (S1) reduzindo o ritmo.
- Rotas (escopo **`campaigns`**): `POST /campaigns` (rascunho), `PATCH /campaigns/{id}`, `POST /campaigns/{id}/test` (até 5 destinatários, **fora** de lista/supressão/contadores, assunto prefixado `[TESTE]`), `POST /campaigns/{id}/schedule` (`scheduled_at` ou agora), `POST /campaigns/{id}/pause|resume|cancel`, `GET /campaigns`, `GET /campaigns/{id}`, `GET /campaigns/{id}/stats` (contagens por status; **sem** aberturas/cliques), `GET /campaigns/{id}/recipients?status=&cursor=`.
- Agendamento: `celery beat` (tick de 1 min) enfileira campanhas com `scheduled_at <= now`. Fuso do projeto (`timezone`, default `America/Sao_Paulo`).
- **Não aceitar `from` (endereço)**: segue o remetente do canal marketing (pode ser outro subdomínio, ex.: `news@mail.ediyporn.com`, configurado no manifesto; autenticação SPF/DKIM/DMARC é responsabilidade do operador e fica documentada).
- Testes: expansão em lotes sem duplicar sob retry, filtro de supressão no envio (suprimir *depois* de agendar ainda barra), pause/resume/cancel, `daily_cap`, teste não altera contadores, 422 se campanha usa tipo não-marketing ou canal sem `allow_marketing`, `legal_footer_missing`, render com `contact.attributes`, isolamento multi-tenant.

### M5 — Eventos: bounce, reclamação, entrega e retorno ao WordPress
Domínio `events/`.

- Modelo `message_events(id, project_id, message_id, contact_id, type, occurred_at, raw JSON mínimo, provider_event_id único por provedor)` com `type ∈ delivered|hard_bounce|soft_bounce|complaint|unsubscribed|blocked`. `Message.status` ganha `delivered|bounced|complained` (extensível; **não** quebrar os valores existentes).
- **Entrada** (adapters atrás de uma interface `ProviderEventAdapter`, um por canal):
  - `POST /webhooks/providers/{project_slug}/{secret}` — segredo por canal (cifrado, rotacionável), valida assinatura quando o provedor oferecer; **dedup por `provider_event_id`**; responde 200 rápido e processa em task. Implemente o adapter **Brevo** (eventos do transacional) e um adapter **genérico assinado** (HMAC) para outros provedores.
  - **SMTP sem webhook**: `return-path` VERP (`bounce+<message_id>@<domínio-de-bounce>`) e **poller IMAP opcional** (`BOUNCE_IMAP_*`, desligado por padrão) que interpreta DSN (stdlib `email`, sem dependência nova) e gera `hard_bounce|soft_bounce`. Se não conseguir implementar com segurança neste slice, entregue a interface + o adapter genérico e abra issue.
- **Regras**: `hard_bounce` e `complaint` → **supressão automática imediata** (`reason`), `contact.status=bounced|complained`; `soft_bounce` → conta; 3 em 14 dias vira `hard` (configurável). Eventos de projeto A nunca tocam contatos do B.
- **Saída para o WordPress** (duas vias, o plugin escolhe):
  1. **Pull (recomendado, GoDaddy-friendly):** `GET /api/v1/events?since_id=&types=&limit=` (escopo `contacts`), cursor monotônico, retenção de 30 dias; o plugin consulta via WP-Cron a cada poucos minutos e atualiza user-meta (`unsubscribed`, `bounced`).
  2. **Push opcional:** `projects.event_webhook_url` + segredo (write-only, cifrado); POST assinado `X-Relay-Signature: sha256=…` + `X-Relay-Timestamp`, retry com backoff, desativa após N falhas e avisa no admin. SSRF: bloquear IPs privados/loopback/link-local e exigir HTTPS.
- Testes: dedup de evento, suprimir após hard_bounce/complaint, soft→hard, assinatura inválida → 401, cursor de eventos sem perda nem repetição, SSRF bloqueado, isolamento entre projetos.

### M6 — Manifesto, Admin UI, contrato do plugin e fechamento
- **Manifesto** (`manifest_version` continua 1; campos novos **opcionais**, schema `extra="forbid"` atualizado em `GET /manifest/schema`): `client.delivery.transactional|marketing` (com `allow_marketing`), `client.legal` (`sender_identity`, `contact_email`, `postal_address` opcional), `client.timezone`, `lists[]` (`key,name,double_opt_in`), `email_types[]` com `category=marketing`, `client.event_webhook` (`url`, `secret` write-only), `limits.marketing_rate_per_minute|daily_cap|warmup`. O `PUT /manifest` segue idempotente/atômico/dry-run/prune, e o export **omite segredos**. Atualize `docs/examples/ediy.manifest.json` com: canal transacional Brevo (`allow_marketing:false`), canal marketing **placeholder** `${EDIY_MARKETING_SMTP_*}` (o importador rejeita placeholder não substituído), lista `newsletter` com `double_opt_in:true` e um tipo `ediy.newsletter` (`marketing`, `raw`).
- **Admin UI (HTMX)**: abas Contatos (busca, status, exportar CSV **sem** dados sensíveis além de e-mail/status), Listas, Campanhas (rascunho, preview com contato de exemplo, teste, agendar, pausar, progresso) e Supressões (adicionar/remover manual com motivo, auditado). Remover da supressão exige motivo e fica no log de auditoria.
- **Contrato do plugin — `docs/PLUGIN_WORDPRESS_CONTRATO.md`** (o plugin é outro repositório; aqui só o contrato): autenticação (`Authorization: Bearer`, chave `send,read,contacts,campaigns` nunca no front-end), mapeamento WordPress→relay (`user_register` e `profile_update` → `PUT /contacts/by-external-id`; checkbox de opt-in no cadastro/checkout → `consent`; mudança de assinatura WooCommerce → `attributes.plan`; descadastro na área da conta → `POST /contacts/{id}/unsubscribe`; `delete_user` → `DELETE /contacts/{id}`), **WP-Cron a cada 5 min** consumindo `GET /events`, backfill via `/contacts/bulk` em lotes de 500 com retomada, formulário/shortcode de inscrição (público → plugin → relay; **nunca** expor a chave ao navegador), timeouts (5 s) e retry com `Idempotency-Key`, o que fazer quando o relay está fora (fila local do plugin, nunca bloquear o checkout). Inclua um exemplo mínimo em PHP (`wp_remote_post`) como **apêndice de documentação**, não como código do repositório.
- Atualizar: `README.md`, `.env.example` (`RELAY_UNSUBSCRIBE_SECRET`, `UNSUBSCRIBE_MAILTO_ENABLED`, `BOUNCE_IMAP_*`, `MARKETING_DEFAULT_RATE_PER_MINUTE`, `EVENT_RETENTION_DAYS`), `docs/API_SPEC.md`, `docs/API_ROUTES_CURL.md`, `docs/GUIA_FLUXO_E_IMPLEMENTACAO.md`, `docs/CLIENTES_EXTERNOS.md` (seção "Mailings"), `CHANGELOG.md`.
- Estender `scripts/curl_smoke_test.sh`: importa manifesto com lista e canal marketing apontando ao **Mailpit**, cria 3 contatos (1 sem consentimento → 422), double opt-in confirmado via `/c/{token}`, cria campanha, testa, agenda, confere 202 e entrega no Mailpit **com `List-Unsubscribe` e `List-Unsubscribe-Post`**, descadastra por `POST /u/{token}`, reagenda e confirma que o suprimido **não** recebe, injeta evento `hard_bounce` e confere supressão + `GET /events`.
- Fechar as issues resolvidas; listar no PR as que ficaram abertas (adapters de provedores de marketing, tracking opcional de abertura/clique, segmentação avançada, editor visual).

## Testes obrigatórios (pytest + httpx.AsyncClient, SQLite em memória)
Auth/escopos (`contacts`, `campaigns` ausentes nas chaves antigas → 403), isolamento multi-tenant em **todas** as rotas novas, guard rail marketing×canal, fila bulk isolada, consentimento obrigatório, supressão aplicada no envio, unicidade `(campaign, contact)` sob retry, descadastro one-click idempotente, eventos deduplicados, eliminação LGPD, segredos nunca em response/log, manifesto idempotente com os campos novos, rate limit e `daily_cap`.

## Definição de pronto
1. `ruff`, `mypy`, `pytest` verdes; `alembic upgrade head` limpo em Postgres e `alembic heads` = 1.
2. `scripts/curl_smoke_test.sh` passa de ponta a ponta com Mailpit, incluindo cenário de descadastro e bounce.
3. Manifesto da EDIY (com lista + canal marketing placeholder) importa em `dry_run` e em apply; Brevo da EDIY continua **recusando** marketing (422).
4. Nenhum envio em massa atrasa e-mail transacional (fila separada, comprovada por teste).
5. Issues abertas para cada defeito, referenciadas nos commits e **fechadas** as resolvidas; PR com decisões, riscos e pendências.
6. Nada de segredo, IP em claro ou e-mail em logs, respostas, fixtures ou repositório.

## Decisões que você pode tomar sem perguntar (documente no PR)
Formato exato do 422/503 e `Retry-After`; limites numéricos (ajuste se inviável); lib de rate limit (preferir Redis já existente); nomes de colunas dentro do descrito; estratégia de lote/cursor. **Pergunte antes** se precisar: mudar o contrato público acima, escolher provedor de marketing, ligar tracking de abertura/clique, ou importar contatos como `subscribed` sem consentimento registrado.

## Decisões em aberto do Marcos (não bloqueiam o código, bloqueiam produção)
1. **Qual canal de marketing da EDIY** (SMTP próprio/GoDaddy ou outro provedor que aceite o conteúdo) e o subdomínio de envio com SPF/DKIM/DMARC.
2. **Base inicial**: só quem der opt-in novo, ou migrar usuários atuais com base legal documentada (validar com jurídico).
3. **Endereço/identificação do remetente** para o rodapé (`client.legal`).
4. **Política de tracking**: manter desligado (recomendado para site adulto) ou liberar por campanha.
