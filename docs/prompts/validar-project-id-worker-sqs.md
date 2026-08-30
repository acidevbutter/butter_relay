Corrija o achado H-02 de `docs/butter-relay-fluxos.md` (detalhado em
`docs/auditoria-seguranca-2026-08-29.md`, item 3): o worker aceita `MessageAttributes.project_id`
da mensagem SQS como autoridade, sem vincular essa identidade a quem publicou. Leia
`app/worker/consumer.py` inteiro antes de começar.

## 1. Parar de confiar isoladamente em `MessageAttributes.project_id`

Em `_parse_sqs_message` (`app/worker/consumer.py:48-58`), hoje:

```python
project_id = uuid.UUID(sqs_message["MessageAttributes"]["project_id"]["StringValue"])
```

Isso é aceito sem checagem de origem. Implemente uma das duas abordagens abaixo (escolha uma;
registre a decisão em `docs/butter-relay-fluxos.md`, fechando H-02):

**Opção A -- fila por projeto (preferível a médio prazo):** em vez de uma única
`SQS_QUEUE_URL` compartilhada com `project_id` como atributo solto, provisione uma fila por
projeto (ou por grupo de confiança) e use `Project.sqs_queue_url` (já existe no modelo, ver
achado 8/`docs/prompts/respeitar-project-is-active-worker.md`) para o worker saber de qual fila
aquele projeto deveria vir. Rejeite (trate como `MalformedMessageError`) qualquer mensagem cujo
`project_id` no corpo não corresponda ao dono da fila de onde ela foi lida.

**Opção B -- envelope assinado (menor mudança de infra):** exija que o produtor inclua um HMAC
ou assinatura no corpo da mensagem, calculado com um segredo por projeto (reaproveite
`ApiKey`/`_hash_key` como base, ou adicione um `Project.signing_secret`). Em
`_parse_sqs_message`, valide a assinatura contra o `project_id` declarado antes de aceitar a
mensagem; se inválida, levante `MalformedMessageError` (mesmo tratamento que payload malformado
hoje -- não deleta a mensagem SQS, loga e segue).

## 2. Registrar a decisão no plano de fila compartilhada

Depois de implementar, atualize a tabela de riscos em `docs/butter-relay-fluxos.md` marcando
H-02 como resolvido (ou "mitigado -- ver detalhe") e explique brevemente qual das opções foi
adotada e por quê, já que o próprio documento observa que "H-02 depende da política efetiva de
IAM da fila compartilhada; essa política não está no checkout".

## 3. Testes

Em `tests/test_worker_consumer.py`, adicione:

- Mensagem com `project_id` que não corresponde à fila/assinatura esperada -- deve virar
  `MalformedMessageError`, mensagem SQS **não** deletada (mesmo padrão dos testes de payload
  malformado já existentes nesse arquivo).
- Mensagem legítima (project_id correto e, se Opção B, assinatura válida) -- processa
  normalmente, sem regressão no caminho feliz já coberto.

Rode `poetry run pytest -q` ao final.
