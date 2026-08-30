Corrija o achado [NOVO] item 9 de `docs/auditoria-seguranca-2026-08-29.md`: os módulos
`messages` e `api_keys` -- incluindo a view de detalhe de mensagem adicionada no commit
`68cc684` -- não têm nenhum arquivo de teste. `tests/` hoje só tem `test_auth.py`,
`test_decorators.py`, `test_projects.py`, `test_templates.py` e `test_worker_consumer.py`. Siga
o estilo desses arquivos existentes (fixtures de `tests/conftest.py`, uso de `TestClient`/`httpx`
async client conforme já usado em `test_projects.py`/`test_templates.py`).

## 1. Criar `tests/test_messages.py`

Cubra, no mínimo:

- `MessageService.list`/`.get` (unitário, com `MessageRepository` real contra banco de teste,
  igual ao padrão de `test_projects.py`).
- `GET /api/v1/messages/` e `GET /api/v1/messages/{id}` via HTTP -- caminho feliz.
- `GET /api/v1/messages/{id}` com id inexistente -- 404 (`NotFoundError`).
- **Depois** de `docs/prompts/autenticar-api-v1.md` ser aplicado: caso sem `Authorization` -- 401;
  caso com key de outro projeto -- 403/404, não vaza mensagem de outro tenant. Se este prompt
  for executado antes daquele, adicione um `# TODO` explícito nesses dois casos referenciando
  `docs/prompts/autenticar-api-v1.md` em vez de omitir o teste silenciosamente.
- `GET /admin/messages/{id}` (a view HTML nova) autenticado como admin -- 200, contém
  `message.to_email` e o payload renderizado; sem sessão -- redireciona/401 (ver
  `get_current_admin_user` em `app/auth/router.py`).
- `POST /admin/messages/{id}/resend` para uma mensagem `FAILED` -- confirma que
  `mark_processing`/`mark_sent` (ou `mark_failed` de novo, se o mock do provider falhar) é
  chamado corretamente (mock `BrevoEmailProvider.send`, seguindo o padrão de mock já usado em
  `tests/test_worker_consumer.py`).

## 2. Criar `tests/test_api_keys.py`

Cubra, no mínimo:

- `ApiKeyService.create` -- gera key com prefixo `brk_`, hash SHA-256 persistido, plaintext
  retornado só na criação.
- `ApiKeyService.authenticate` -- key válida retorna o `ApiKey` e atualiza `last_used_at`; key
  revogada e key inexistente levantam `UnauthorizedError` (isto testa diretamente o achado 2 da
  auditoria -- o método existe e funciona isoladamente, o teste documenta isso mesmo antes/depois
  dele ser ligado num router).
- `ApiKeyService.revoke` -- marca `revoked_at`; revogar de novo ou revogar id de outro projeto
  levanta `NotFoundError`.
- `POST /api/v1/api-keys/` e `POST /api/v1/api-keys/{id}/revoke` via HTTP -- caminho feliz e,
  após `docs/prompts/autenticar-api-v1.md`, os casos de escopo por projeto.

## 3. Rodar a suíte completa

```
poetry run pytest -q
poetry run ruff check .
poetry run mypy app
```

Todos devem passar (ou falhar apenas nos erros pré-existentes já documentados em
`docs/butter-relay-fluxos.md`, seção "Estado documental e validação" -- não introduzir novos).
