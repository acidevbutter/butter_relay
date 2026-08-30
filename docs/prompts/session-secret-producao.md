Corrija o achado M-03 de `docs/butter-relay-fluxos.md` (detalhado em
`docs/auditoria-seguranca-2026-08-29.md`, item 5): `session_secret` tem default inseguro e a
configuração não falha fechado em produção.

## 1. Remover o default inseguro de `session_secret`

Em `app/settings/config.py:23`, troque:

```python
session_secret: str = "dev-only-insecure-secret-change-me"
```

por um campo obrigatório (sem default), ex.:

```python
session_secret: str
```

Isso faz o `pydantic-settings` levantar erro de validação no boot se `SESSION_SECRET` não
estiver setado -- comportamento de "falhar fechado" em vez de subir com segredo previsível.

## 2. Validar `debug`/`session_secret` juntos no startup

Em `app/settings/config.py`, adicione um `model_validator` (Pydantic v2, `@model_validator(mode="after")`)
que rejeite a combinação `debug=False` com `session_secret` igual a um valor de exemplo
conhecido (ex. `"change-me-in-production"`, o mesmo valor hoje presente em `.env`/`.env.example`).
Levante `ValueError` claro tipo `"SESSION_SECRET is a placeholder; set a real value before disabling DEBUG"`.

## 3. Atualizar `.env.example`

Em `.env.example`, troque `SESSION_SECRET=change-me-in-production` por um comentário explicando
como gerar um valor real (ex. `# gerar com: python -c "import secrets; print(secrets.token_urlsafe(32))"`)
e deixe o valor vazio ou claramente marcado como placeholder de dev. Não altere `.env` local (é
gitignored e só usado em dev/compose).

## 4. Conferir `DEBUG=true` como default de dev, não de prod

Confirme que nenhum ambiente de deploy real (verificar `Dockerfile`, `docker-compose.yml`,
qualquer manifest de deploy fora deste repo se houver) herda `DEBUG=true` do `.env` de exemplo.
Se houver dúvida, documente explicitamente em `README.md`/`docs/butter-relay-fluxos.md` que
`DEBUG` deve ser `false` em produção (isso também afeta `https_only` do cookie de sessão em
`app/settings/middleware.py:41-44`).

## 5. Testes

Em `tests/test_auth.py` ou novo `tests/test_config.py`:

- Instanciar `Settings` sem `SESSION_SECRET` no ambiente -- deve levantar erro de validação.
- Instanciar `Settings` com `debug=False` e `session_secret="change-me-in-production"` -- deve
  levantar o `ValueError` do passo 2.
- Instanciar `Settings` com `debug=False` e um `session_secret` real -- deve funcionar
  normalmente (sem regressão).

Rode `poetry run pytest -q` ao final -- e confirme que a suíte de testes existente já define
`SESSION_SECRET` (via `conftest.py`/env de teste) para não quebrar com a mudança do passo 1.
