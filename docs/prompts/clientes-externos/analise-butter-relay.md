# Análise do butter_relay para uso pela EDIY (14/10/2026)

Branch analisada: `feature/relay-delivery-reliability` @ `ca4340d` (10/10/2026). Leitura de código e docs. Os testes não rodaram nesta análise (o ambiente tem Python 3.10 e o projeto exige 3.11 com Poetry).

## O que já funciona

- Multi-tenant por `Project`, uma fila Celery por projeto (`relay-<slug>`), Redis no Coolify.
- Templates Jinja2 versionados (sandbox, validação, rollback), log por mensagem, reenvio manual no admin.
- Retry com backoff (5x, até 300 s), idempotência por `broker_message_id` (task id).
- Canais de envio: Brevo (chave **global** `BREVO_API_KEY`) ou SMTP.
- Admin HTMX: projetos, chaves, templates, mensagens, envio de teste.

## Gaps para o caso EDIY (plugin WP → relay)

| # | Gap | Impacto | Sev. |
|---|---|---|---|
| 1 | **Não há endpoint HTTP de envio.** Só entra via `send_task` direto no Redis | WordPress na GoDaddy não fala Celery e não deve acessar o Redis do Coolify | Bloqueante |
| 2 | **Só envia por template** (`template_key` + payload) | WooCommerce já gera o HTML pronto; falta modo "raw" (subject/html/text prontos) | Bloqueante |
| 3 | **SMTP sem TLS e sem autenticação** (`smtplib.SMTP` puro) | Não serve para GoDaddy, Gmail etc., só Mailpit | Bloqueante p/ opção SMTP |
| 4 | **Brevo com chave global única** | Todos os clientes compartilham a conta e a reputação. Conteúdo adulto viola a AUP da Brevo e pode derrubar todos | Alto |
| 5 | `send()` só aceita **1 destinatário**, sem reply-to, cc/bcc, anexos, texto puro ou headers | Pedidos WooCommerce usam reply-to; alguns e-mails têm cópia | Médio |
| 6 | **Erros 4xx permanentes da Brevo são re-tentados** (400/401 viram `EmailSendError` → retry 5x) | Desperdício e atraso; erro de config só aparece depois de ~10 min | Médio |
| 7 | **Sem webhooks de entrega/bounce/spam** | Status para em `sent`; não sabe se entregou | Médio |
| 8 | **H-02 da auditoria segue aberto**: o worker aceita `project_id` do produtor sem validar a chave | Quem acessa o Redis pode enviar como outro projeto/remetente | Alto |
| 9 | Remetente fixo por projeto (`default_sender_*`), sem override por mensagem | OK para a EDIY (um remetente) | Baixo |
| 10 | Segredos por projeto (chave Brevo, senha SMTP) **sem criptografia** (não há Fernet/KMS) | Bloqueia os itens 3 e 4 | Alto |

## Proposta mínima para habilitar a EDIY

1. `POST /api/v1/messages/send`, autenticado por API key do projeto. O `project_id` vem da chave, nunca do body (resolve o item 8 para quem usar HTTP). O endpoint enfileira na fila do projeto e responde **202** com `message_id`. Aceita `Idempotency-Key`.
2. **Modo raw**: `template_key` opcional; se ausente, exige `subject` + `html` (+ `text`). `Message.template_id` passa a ser nullable.
3. Ampliar `EmailProvider.send` com `reply_to`, `cc`, `bcc`, `text`, `headers` e `attachments` (base64, com limite de tamanho).
4. SMTP com STARTTLS/SSL + usuário/senha, com segredos de `delivery_config` cifrados (Fernet com chave em env).
5. Brevo: chave por projeto (cifrada) com fallback na global. Classificar 4xx como permanente (sem retry), 429/5xx como transitório.
6. Webhook de eventos do provedor → `Message.status` (`delivered`, `bounced`, `complained`).

Seguir o padrão do fastapi-builder (router → service → repository, migração Alembic timestamped).

## Canal recomendado para a EDIY

**Decisão (14/10/2026): Brevo, uma conta própria por cliente.** O EDIY usa conta Brevo separada (chave por projeto, cifrada), nunca a chave global, para isolar reputação e risco de suspensão (AUP 1.D.3 proíbe conteúdo sexual explícito; o EDIY enviará só e-mail transacional, sem conteúdo explícito). Antes de produção: confirmar por escrito com o suporte da Brevo que transacional de site adulto é aceito, e autenticar o domínio (SPF/DKIM/DMARC) nessa conta. Fallback opcional: SMTP autenticado do GoDaddy (`no-reply@ediyporn.com`), que depende do item 3.
