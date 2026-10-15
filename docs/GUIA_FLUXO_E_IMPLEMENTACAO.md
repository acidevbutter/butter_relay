# Butter Relay — fluxo validado, canal de disparo por company e guia de implementação

**Data:** 2026-09-30 · **Escopo:** `butter_relay` (feito e mergeado em `master`) + o que falta
implementar do lado do `devbutter_backend` / admin DevButter (checklist na seção 4).

## 1. Resumo do que mudou

| Antes | Agora |
|---|---|
| O provider era escolhido pelo **ambiente** (`development` → Mailpit, senão Brevo) | **Brevo é o padrão em qualquer ambiente**; cada company (Project) escolhe o **próprio canal** (`brevo` ou `smtp`) |
| Só dava para criar projeto/template “às cegas” (erro = 500 ou JSON cru) | Admin do relay cria app, edita configurações, troca canal, **testa o envio**, publica/edita/ativa versões de template, tudo com erros legíveis |
| Template quebrado (`{{ a`) era publicado e virava a versão ativa | Publicação valida Jinja2, `variables_schema` e `key`; rollback por `activate` |
| Worker perdia mensagens (ver seção 5) | Corrigido |

Modelo mental: **App = Project = company no relay.** Cada Project tem: remetente padrão, fila
(`relay-<slug>`), API keys, templates versionados e o **canal de disparo**
(`delivery_provider` + `delivery_config`).

| Canal | Como envia | Configuração |
|---|---|---|
| `brevo` (padrão) | API transacional do Brevo | `BREVO_API_KEY` global do deployment (obrigatória fora de dev) |
| `smtp` | SMTP puro, sem TLS/auth | `SMTP_HOST`/`SMTP_PORT` do deployment (Mailpit no dev) **ou** `smtp_host`/`smtp_port` do projeto |

Segredos nunca ficam em `delivery_config`. Chave Brevo por company e SMTP autenticado **não**
existem ainda (seção 6).

## 2. Passo a passo no relay (validado ao vivo)

Pré-requisitos: Docker; Postgres/Redis compartilhados de `../infra` no ar.

```bash
cp .env.example .env            # BREVO_API_KEY vazio = envios via Brevo falham com 401 (esperado)
docker compose --profile dev up -d --build
curl -s localhost:8010/health/ready
```

As migrations rodam sozinhas no boot (`docker-entrypoint.sh`). Depois, crie o primeiro admin:

```bash
docker compose exec -T api python -c "
import asyncio
from app.auth.repository import AdminUserRepository
from app.auth.service import AuthService
from app.db.session import session_factory
async def main():
    async with session_factory() as s:
        await AuthService(AdminUserRepository(s)).register('admin@devbutter.com', 'TroqueEstaSenha')
asyncio.run(main())"
```

### 2.1 Cadastrar uma nova app (company)

1. Abra <http://localhost:8010/admin> → **Projects** → **New project**.
2. Preencha nome, **slug** (minúsculas e hífens; vira a fila `relay-<slug>`), remetente padrão.
3. Escolha o **Delivery channel**: `Brevo` (padrão) ou `SMTP`. Para dev local, use SMTP e deixe
   host/porta em branco (usa `mailpit:1025`).
4. **Create project.** O worker já passa a consumir `relay-<slug>` (sem restart).

Equivalente por API (precisa de sessão admin — `POST /admin/login` com cookie jar):

```bash
curl -s -b cookies.txt -X POST localhost:8010/api/v1/projects/ -H 'content-type: application/json' -d '{
  "name": "Minha App", "slug": "minha-app",
  "default_sender_email": "noreply@minhaapp.com", "default_sender_name": "Minha App",
  "delivery_provider": "smtp"
}'
```

### 2.2 Conferir o canal com um envio de teste

Na página do projeto → **Settings & delivery channel** → campo *Send a test e-mail* →
**Send test**. O envio usa exatamente o canal do projeto (sem fila, sem template). Com SMTP o
e-mail aparece em <http://localhost:8025> (Mailpit); com Brevo sem chave você verá o erro 401
na própria página.

### 2.3 Gerar a API key da app

Página do projeto → **API keys** → **Generate key**. A chave `brk_...` aparece **uma vez** — é o
Bearer de `/api/v1/templates`, `/api/v1/messages`, `/api/v1/api-keys` e
`/api/v1/projects/{id}/delivery` **da própria app**. Projetos “External” geram chaves que expiram
em 30 dias.

### 2.4 Salvar templates

Página do projeto → **Templates**:

- **Publish new version**: `key` (letras, dígitos, `_ - .`), assunto e HTML em Jinja2, e
  opcionalmente um `variables_schema` (JSON Schema; payload fora do schema é rejeitado). O botão
  *Use DevButter starter* preenche um layout de exemplo.
- **Preview** renderiza com um JSON de exemplo (iframe isolado).
- **Edit as new version** carrega uma versão existente no formulário; publicar cria a próxima.
- **Activate** volta uma versão antiga a ser a ativa (rollback).
- Template inválido (`{{ a`, schema malformado, key com espaço) **não** é publicado — a página
  mostra o motivo e mantém o que você digitou.

Por API (Bearer da app):

```bash
KEY=brk_...; PID=<project id>
curl -s -X POST localhost:8010/api/v1/templates/ -H "Authorization: Bearer $KEY" \
  -H 'content-type: application/json' -d "{
  \"project_id\": \"$PID\", \"key\": \"welcome_email\",
  \"subject_template\": \"Bem-vindo, {{ name }}!\", \"html_template\": \"<p>Oi {{ name }}</p>\",
  \"variables_schema\": {\"type\": \"object\", \"required\": [\"name\"]}}"
curl -s -X POST localhost:8010/api/v1/templates/<template_id>/activate -H "Authorization: Bearer $KEY"
```

### 2.5 Enviar de verdade (contrato do produtor)

```python
from celery import Celery
Celery("meu-app", broker=REDIS_URL).send_task(
    "butter_relay.process_message",
    kwargs={"project_id": PROJECT_ID, "template_key": "welcome_email",
            "to": "cliente@exemplo.com", "payload": {"name": "Marcos"}},
    queue="relay-minha-app",          # ou "relay-shared" (legado)
)
```

Acompanhe em **Messages** (`/admin/messages`): cada mensagem mostra status, tentativas, o canal
usado (`provider`) e o erro do provider; mensagens `failed` têm **Resend**.

### 2.6 Smoke test automatizado

```bash
ADMIN_EMAIL=admin@devbutter.com ADMIN_PASSWORD=<senha> ./scripts/curl_smoke_test.sh
```

Cobre login, criação de projeto, chave, template, preview, **e a entrega ponta a ponta pelo
worker** (12 checks; precisa da stack de pé).

## 3. Como o canal é definido: duas portas para a mesma configuração

1. **Admin do relay** (`/admin/projects/<id>`): edita tudo — canal, host/porta SMTP, remetente,
   ativo. É a fonte de verdade.
2. **Admin DevButter** (via API, sem sessão do relay): usa a API key **da própria company**:

```bash
curl -s localhost:8010/api/v1/projects/$PID/delivery -H "Authorization: Bearer $KEY"
# {"project_id": "...", "delivery_provider": "brevo", "delivery_config": {}, ...}

curl -s -X PUT localhost:8010/api/v1/projects/$PID/delivery -H "Authorization: Bearer $KEY" \
  -H 'content-type: application/json' -d '{"delivery_provider": "smtp"}'
```

Por desenho, a chave da company só troca o **canal** (`brevo`/`smtp`). Host/porta SMTP e remetente
continuam só no admin do relay — uma chave vazada não consegue redirecionar o e-mail daquela
company para um host arbitrário. A chave de uma company recebe 403 nas rotas de outra.

## 4. O que falta implementar (checklist para você)

Lado `devbutter_backend` (hoje: um único `RELAY_PROJECT_ID` global e `RelayClient` fixo em
`relay-shared`; `Company` é a entidade fiscal e o admin já tem `/admin/companies`):

- [ ] **Decidir o mapeamento** company → Project do relay. Sugestão: uma coluna
  `relay_project_id` (UUID) em `Company` (ou numa tabela `company_settings`), preenchida ao
  cadastrar a company. Quem tem “app própria” usa o Project dela; as demais caem no Project
  `devbutter-platform` (o `RELAY_PROJECT_ID` atual como *fallback*).
- [ ] **`RelayClient.send(...)` aceitar `project_id` e `queue`** por chamada
  (`relay-<slug>` do Project; hoje ele lê `settings.relay_project_id` e `relay_queue_name`).
  `NotificationService` passa a resolver o Project pela company do usuário/conta antes de enviar.
- [ ] **Cadastrar os templates de cada Project** com as mesmas `key` que o backend já usa
  (`devbutter_quote_approved`, `devbutter_payment_status_changed`,
  `devbutter_pending_account_link`, `devbutter_quote_received`, `devbutter_password_recovery`,
  `devbutter_subaccount_onboarding`, `devbutter_commercial_info_expiring`). Um Project novo não
  tem template nenhum — sem ele o worker falha com “No active template”.

Para criar os sete templates padrão que ainda não existem, execute no ambiente do Relay:

```bash
python scripts/seed_devbutter_templates.py <project-uuid>
```

O comando valida e publica somente as chaves sem versão ativa; não envia mensagens nem
substitui templates já ativos. A imagem da API inclui o script em `/app/scripts/`.
- [ ] **Tela “Configurações da company” no admin DevButter** com o campo *Meio de disparo*
  (`brevo` | `smtp`). Ao salvar: `PUT {RELAY_URL}/api/v1/projects/{relay_project_id}/delivery`
  com o Bearer da company. Guarde essa API key **criptografada** (ela dá acesso a templates e ao
  log de mensagens daquela company) e mostre o canal atual lendo o `GET` do mesmo caminho.
- [ ] Novo env no backend: `RELAY_BASE_URL` (o relay expõe HTTP em `relay-api:8000` na rede
  compartilhada / URL pública em produção).
- [ ] **Opcional:** criar a app no relay direto do admin DevButter. Hoje `POST /api/v1/projects/`
  exige sessão de admin do relay (cookie), o que é desconfortável para serviço-a-serviço; se
  quiser isso, o próximo passo natural é um token de serviço (issue a abrir) em vez de logar
  como humano.

## 5. Bugs encontrados na validação (todos corrigidos e mergeados)

| Issue | Problema | Impacto |
|---|---|---|
| #29 | Pool asyncpg preso ao event loop da task anterior | **Perda silenciosa** de toda mensagem após a 1ª por processo do worker |
| #30 | `add_consumer` por broadcast não chegava ao worker que estava subindo | Após todo restart/deploy só `relay-shared` era consumida |
| #35 | Cada retry criava outra linha `Message`; a seguinte estourava `MultipleResultsFound` | Mensagem abandonada antes de `MAX_RETRIES`; log duplicado |
| #33 | Worker do compose dev sem volume `./alembic` | Worker não subia após qualquer migration nova |
| #37 | Formulários do admin devolviam 500/JSON cru; rollback quebrava o header (`MissingGreenlet`) | Admin inutilizável em erro de entrada |
| #38 | Template inválido era publicado como versão ativa | Envios quebrados até publicar de novo |
| #17 | Smoke test sem autenticação correta | Nenhum check ponta a ponta confiável |
| #14 | Logs do worker fora do formato estruturado | Sem `service`/JSON nos logs do worker |

Também: preview em `iframe sandbox` (H-04 da auditoria); reenvio manual respeita a política de
destinatários de não-produção; falhas SMTP viram `EmailSendError` (retry + status `failed`).
Issues antigas obsoletas (#7, #11, #23) foram fechadas com justificativa.

## 6. Ainda não implementado / riscos conhecidos

- **Chave Brevo por company** e **SMTP autenticado/TLS**: exigiriam guardar segredo criptografado
  (não há KMS/Fernet no projeto). Hoje o Brevo usa a `BREVO_API_KEY` global.
- O canal `smtp` em **produção** usa `SMTP_HOST` (padrão `mailpit`, que não existe lá): só ative
  para uma company de produção depois de definir `SMTP_HOST` ou `smtp_host` no projeto.
- A API `POST/PATCH /api/v1/projects` continua exigindo sessão de admin (sem token de serviço).
- Ambiente **staging/preprod**: só entrega para `ALLOWED_RECIPIENT_DOMAINS` (vale também para o
  envio de teste e o reenvio).
- Itens abertos da auditoria em `docs/butter-relay-fluxos.md` (rate limit de login, CSRF, retenção
  de payload/PII) seguem valendo.

## 7. Troubleshooting

| Sintoma | Causa provável |
|---|---|
| Mensagem parada em `relay-<slug>` no Redis | Worker fora do ar ou sem a fila: `celery inspect active_queues`; reinicie o worker (seed no boot) |
| `failed` com `Brevo send failed (401)` | `BREVO_API_KEY` vazia/errada, ou a company está em `brevo` num ambiente sem chave |
| `failed` com `SMTP send failed (host:port)` | Host/porta SMTP inalcançáveis (ex.: `mailpit` fora do compose) |
| `No active template 'x' for project` | `template_key` não publicado (ou desativado) nesse Project |
| `recipient blocked by non-production delivery policy` | Staging/preprod sem o domínio em `ALLOWED_RECIPIENT_DOMAINS` |
| Worker “não sobe” após pull | Migration nova + volume `./alembic` ausente (corrigido no compose) |
