# Relay API — especificação de navegação

**Dono:** projetos multi-tenant, chaves de API, templates versionados e log de
entrega. Envio é feito pelo worker Celery (`app/worker/tasks.py`), não por uma
rota HTTP. Contrato de campos e respostas: `/openapi.json` da instância.

## Acesso

- `/api/v1/*` exige `Authorization: Bearer <API key>`; a chave resolve o
  projeto chamador.
- `/admin/*` usa sessão administrativa assinada; login/logout também estão
  nesse prefixo.
- Não confunda a API administrativa HTML/HTMX com a API JSON v1.

## Rotas por domínio

| Prefixo | Operações | Módulo a abrir |
| --- | --- | --- |
| `/api/v1/projects` | listar, criar, consultar e alterar projetos | `app/projects/` |
| `/api/v1/api-keys` | listar, criar e revogar chaves do projeto | `app/api_keys/` |
| `/api/v1/templates` | listar, publicar, consultar e prever template | `app/templates_/` |
| `/api/v1/messages` | listar e consultar log de mensagens | `app/messages/` |
| `/admin/login`, `/admin/logout` | sessão administrativa | `app/auth/` |
| `/admin/projects`, `/admin/templates`, `/admin/messages` | dashboard HTML: gestão, preview e reenvio | `app/admin/router.py` |

`GET /health` prova processo; `GET /health/ready` verifica banco.

## Regras de integração

- Produtores chamam `celery_app.send_task("butter_relay.process_message",
  kwargs={project_id, template_key, to, payload}, queue="relay-shared")`;
  o worker valida, renderiza Jinja2 e chama Brevo.
- Template é versionado por projeto; não renderize template de outro projeto.
- A chave em texto puro só aparece na criação; persista/compare somente hash.

## Onde mudar

Comece pelo domínio da rota (`router.py` e `schemas.py`), depois `service.py`,
`repository.py` e `models.py`. Para entrega, abra `app/worker.py` e o domínio
da mensagem, sem procurar pelo repositório todo.
