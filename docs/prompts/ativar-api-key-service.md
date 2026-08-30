Corrija o achado 2 de `docs/auditoria-seguranca-2026-08-29.md`: `ApiKeyService.authenticate`
(`app/api_keys/service.py:37-42`) está implementado e correto, mas não é chamado em nenhum lugar
do código -- é código morto. Este prompt garante que ele fique de fato ligado nos dois pontos que
deveriam usá-lo. Depende de `docs/prompts/autenticar-api-v1.md` já ter sido feito (ou faça os
dois juntos).

## 1. Confirmar que a API HTTP usa `authenticate`

Depois de aplicar `docs/prompts/autenticar-api-v1.md`, rode:

```
grep -rn "api_key_service.authenticate\|ApiKeyService(.*).authenticate\|CurrentApiKey" app
```

e confirme que `authenticate` aparece referenciado pela dependência nova (`get_current_api_key`
ou nome equivalente) usada nos 4 routers de `/api/v1/*`. Se não aparecer, o prompt anterior não
foi concluído -- pare e resolva isso primeiro.

## 2. Decidir e implementar autenticação do worker SQS

`app/worker/consumer.py:18-20` importa `ApiKeyRepository` com o comentário
`# noqa: F401 (SQS auth is IAM, not API key -- see plan)`. Hoje isso está correto para o
transporte (IAM autentica quem pode fazer `SendMessage`/`ReceiveMessage` na fila), mas não
autentica *qual projeto* a mensagem representa (isso é o achado 3,
`docs/prompts/validar-project-id-worker-sqs.md`). Duas opções -- escolha uma e documente a
decisão em `docs/butter-relay-fluxos.md`:

- **Opção A (recomendada, menor mudança):** deixe a autenticação de transporte como IAM (como
  já está) e resolva o vínculo produtor→projeto exclusivamentena validação de assinatura/allowlist
  do prompt `validar-project-id-worker-sqs.md`. Neste caso, **remova** o import morto
  `ApiKeyRepository` de `app/worker/consumer.py` e o comentário `noqa`, já que ele não é mais
  necessário nem verdadeiro (a decisão real está documentada, não "see plan").
- **Opção B:** se decidir que produtores externos (ex. `asaas_payment`, `devbutter_backend`)
  devem incluir a API key do projeto no corpo da mensagem SQS (ex. `body["api_key"]`), adicione a
  validação em `_parse_sqs_message`/`process_sqs_message`: extraia a key, chame
  `ApiKeyService(ApiKeyRepository(session)).authenticate(...)` e confirme que
  `api_key.project_id == project_id` do atributo da mensagem antes de prosseguir; em caso de
  falha, trate como `MalformedMessageError` (não deleta a mensagem, não processa).

## 3. Remover ambiguidade do comentário

Independente da opção escolhida, remova qualquer comentário do tipo "see plan" que não aponte
para um documento real -- referencie `docs/auditoria-seguranca-2026-08-29.md` (achado 2/3) no
lugar.

## 4. Testes

- Em `tests/test_api_keys.py`: teste unitário de `ApiKeyService.authenticate` com key válida,
  key revogada e key inexistente (se ainda não existir nenhum teste direto do service).
- Se optar pela Opção B: em `tests/test_worker_consumer.py`, adicione um caso de mensagem SQS
  com key ausente/inválida/de outro projeto -- deve virar `MalformedMessageError` e a mensagem
  SQS não deve ser deletada (mesmo padrão dos outros testes desse arquivo).

Rode `poetry run pytest -q` ao final.
