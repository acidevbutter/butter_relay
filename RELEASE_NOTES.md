# Release Notes — Butter Relay 0.1.0

**Data retroativa:** 2026-08-21  
**Base:** `master` até `9602fdb`  
**Tag:** não criada

## Resumo

Primeira versão funcional do Butter Relay: serviço de e-mail transacional orientado a SQS, com templates Jinja2 versionados, entrega via Brevo, persistência PostgreSQL e dashboard administrativo.

## Incluído

- API FastAPI para projetos, templates, API keys e mensagens.
- Login administrativo e dashboard server-rendered com Jinja2/HTMX.
- Worker separado para consumo SQS/ElasticMQ e envio ao Brevo.
- Histórico e detalhe de mensagens, preview de templates e reenvio manual.
- Testes automatizados e smoke test HTTP.
- Ambiente Docker local integrado à rede e ao PostgreSQL compartilhados do DevButter.

## Notas operacionais

- O Compose do serviço depende de `shared-postgres` e da rede externa `devbutter_shared`.
- `BREVO_API_KEY` é necessária para envio real; sem ela, o provider retorna falha de autenticação.
- A infraestrutura AWS, IAM, DLQ, Secrets Manager e a prova de entrega do provider não fazem parte destes commits.
- Esta documentação é retroativa e não representa uma publicação formal: não existe tag Git correspondente.
