Corrija o achado H-01 de `docs/butter-relay-fluxos.md` (detalhado em
`docs/auditoria-seguranca-2026-08-29.md`, item 1): os routers `/api/v1/*` não exigem nenhuma
autenticação hoje. Leia os dois documentos antes de começar. Isto é bloqueante -- não fazer
release externo antes disso.

## 1. Criar dependência de autenticação por API key

Em `app/api_keys/service.py`, `ApiKeyService.authenticate(plaintext_key: str) -> ApiKey` já
existe e funciona (hash SHA-256 + checagem de `revoked_at` + `touch_last_used`) -- não reescreva
essa lógica, só ligue-a. Crie uma dependência FastAPI nova, ex. em `app/core/dependencies.py`:

```python
from fastapi import Header
from app.api_keys.models import ApiKey
from app.api_keys.repository import ApiKeyRepository
from app.api_keys.service import ApiKeyService

async def get_current_api_key(
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> ApiKey:
    if authorization is None or not authorization.startswith("Bearer "):
        raise UnauthorizedError("Missing or malformed Authorization header")
    plaintext_key = authorization.removeprefix("Bearer ").strip()
    service = ApiKeyService(ApiKeyRepository(db))
    return await service.authenticate(plaintext_key)

CurrentApiKey = Annotated[ApiKey, Depends(get_current_api_key)]
```

Ajuste o nome/local se já existir um padrão melhor no projeto -- mas mantenha a dependência num
lugar reutilizável pelos 4 routers.

## 2. Aplicar a dependência e o escopo por projeto

- `app/projects/router.py`: proteja `create_project`/`update_project` com `CurrentApiKey` (ou
  `CurrentAdminUser`, se a decisão for que só admin gerencia projetos -- ver seção "Decisão" abaixo).
- `app/api_keys/router.py`: proteja todas as rotas. Como uma API key já pertence a um
  `project_id` (`ApiKey.project_id`), `list_api_keys`/`create_api_key`/`revoke_api_key` devem
  conferir que `payload.project_id` (ou `project_id` do query param) bate com
  `current_api_key.project_id` -- senão uma key do projeto A gerencia keys do projeto B. Levante
  `UnauthorizedError` (ou 403) se não bater.
- `app/templates_/router.py`: mesma coisa -- proteja com `CurrentApiKey` e confira
  `project_id`/`template.project_id` contra `current_api_key.project_id`.
- `app/messages/router.py`: proteja `list_messages`/`get_message` com `CurrentApiKey` e filtre
  por `current_api_key.project_id` (não deixe `project_id` ser passado livremente pelo chamador
  para ver mensagens de outro projeto -- ignore o query param se o resultado não bater com o
  escopo da key, ou force `project_id = current_api_key.project_id`).

### Decisão a registrar no PR

Escolha explicitamente e documente em `docs/butter-relay-fluxos.md` (seção "Falhas e riscos
priorizados", fechando H-01) qual identidade cobre criação de projeto (`POST /api/v1/projects/`)
-- se for algo que só o admin autenticado por sessão deve poder fazer, use `CurrentAdminUser` ali
em vez de `CurrentApiKey` (uma API key de projeto não deveria poder criar outro projeto do zero).

## 3. Testes

Depois de proteger as rotas, cubra em `tests/test_api_keys.py` (ver
`docs/prompts/cobertura-testes-messages-api-keys.md` para o arquivo completo) pelo menos:

- Requisição sem `Authorization` -- 401.
- Requisição com key revogada -- 401.
- Requisição com key válida do projeto A tentando listar/ler mensagens ou API keys do projeto B
  -- 403/404 (não vazar dado de outro tenant).
- Requisição com key válida do próprio projeto -- 200, e confirme `touch_last_used` foi chamado
  (via `last_used_at` atualizado).

Rode `poetry run pytest -q`, `poetry run ruff check .` e `poetry run mypy app` antes de
considerar concluído.
