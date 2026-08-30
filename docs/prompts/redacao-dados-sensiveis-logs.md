Corrija o achado M-05 de `docs/butter-relay-fluxos.md` (detalhado em
`docs/auditoria-seguranca-2026-08-29.md`, item 7): payload/erro do provedor relayed vazam sem
redação em respostas de API e em log.

Pré-requisito: `docs/prompts/autenticar-api-v1.md` já resolve parte da exposição (a API deixa de
ser pública). Este prompt trata do que ainda fica exposto para chamadores autenticados e nos
logs, que também merece redação/retenção definida.

## 1. Truncar/redigir o corpo de erro do provedor

Em `app/core/email_provider.py:80-81`, o `response.text` completo do Brevo é gravado em
`last_error` e logado. Trunque para um tamanho razoável (ex. 500 caracteres) e remova qualquer
campo que pareça conter segredo (ex. se o Brevo ecoar o payload da requisição no erro). Ex.:

```python
detail = response.text[:500]
```

Documente no docstring/comentário por que o truncamento existe (referenciar
`docs/auditoria-seguranca-2026-08-29.md`, item 7).

## 2. Não logar `payload` completo em texto claro

Revise `app/core/decorators.py::log_call`/`log_errors` -- hoje eles logam `qualname` e (via
`log.exception`) o traceback, que pode incluir argumentos de função com o `payload` completo se
alguma lib de logging expandir frames. Adicione um filtro de logging (ou ajuste o formatter em
`app/settings/logging_config.py`) que redija chaves conhecidas de PII/segredo (ex. `password`,
`token`, `card`, `cpf`, `secret`) caso apareçam em `extra`/mensagem estruturada.

## 3. Definir e aplicar retenção/mascaramento de `payload` armazenado

Em `app/messages/models.py`, `Message.payload` (coluna `JSON`) guarda o payload relayed
indefinidamente. Decida e implemente uma das opções (documentar a escolha em
`docs/butter-relay-fluxos.md`):

- Job periódico (fora de escopo de implementar agora, só deixar o hook pronto) que apague/anonimize
  `payload` de mensagens `SENT` mais antigas que N dias; ou
- Mascarar campos conhecidos como sensíveis no `payload` antes de persistir (ex. função
  `redact_payload(payload: dict) -> dict` chamada em `MessageService.create`), se o schema de
  template (`variables_schema`) já identificar quais campos são sensíveis.

## 4. Testes

- Teste unitário de `email_provider.py` confirmando que um erro do Brevo com corpo > 500 chars
  é truncado em `last_error`.
- Se implementar `redact_payload`, teste unitário cobrindo um payload com uma chave sensível
  (ex. `{"token": "abc"}`) e confirmando que o valor persistido não é o original em claro.

Rode `poetry run pytest -q` ao final.
