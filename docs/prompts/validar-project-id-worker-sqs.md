Corrija o achado H-02 de `docs/butter-relay-fluxos.md` (detalhado em
`docs/auditoria-seguranca-2026-08-29.md`, item 3): o worker aceita o `project_id` do payload da
task como autoridade, sem vincular essa identidade a quem publicou/de qual fila a mensagem veio.

**Atualização (2026-09-17):** o transporte migrou de SQS para Celery/Redis
(`app/worker/tasks.py`), e a pré-condição da Opção A abaixo -- fila por projeto -- já existe:
cada `Project` tem sua própria fila derivada do slug (`Project.queue_name`, `relay-<slug>`),
provisionada dinamicamente em `app/worker/queues.py` (sem `Project.sqs_queue_url`, que foi
removido -- ver `acidevbutter/butter_relay#16`). O que falta é só a parte de *validação*: nada em
`app/worker/tasks.py::process_message_task` hoje verifica que o `project_id` recebido nos kwargs
da task corresponde à fila de onde a task foi efetivamente consumida. Leia
`app/worker/tasks.py` inteiro antes de começar.

## 1. Parar de confiar isoladamente no `project_id` dos kwargs da task

Em `process_message_task` (`app/worker/tasks.py:157-193`), o `project_id` chega como kwarg da
task e é usado sem checagem de origem. Implemente uma das duas abordagens abaixo (escolha uma;
registre a decisão em `docs/butter-relay-fluxos.md`, fechando H-02):

**Opção A -- validar contra a fila de origem (a infra já existe):** dentro de
`process_message_task`, leia a fila de onde a task foi entregue (Celery expõe isso via
`self.request.delivery_info.get("routing_key")` com o transporte Redis) e compare com
`queue_name_for(...)` do `project_id` declarado nos kwargs. Rejeite (trate como
`MalformedMessageError`) qualquer task cujo `project_id` não corresponda à fila em que chegou --
isso só protege mensagens publicadas na fila por-projeto; produtores que ainda publicam em
`relay-shared` (a fila legada/fallback) ficam fora dessa checagem até migrarem.

**Opção B -- envelope assinado (funciona também na fila legada `relay-shared`):** exija que o
produtor inclua um HMAC ou assinatura nos kwargs da task, calculado com um segredo por projeto
(reaproveite `ApiKey`/`_hash_key` como base, ou adicione um `Project.signing_secret`). Em
`process_message_task`, valide a assinatura contra o `project_id` declarado antes de aceitar a
mensagem; se inválida, levante `MalformedMessageError` (mesmo tratamento que payload malformado
hoje -- não reprocessa, loga e segue).

## 2. Registrar a decisão no plano de fila compartilhada

Depois de implementar, atualize a tabela de riscos em `docs/butter-relay-fluxos.md` marcando
H-02 como resolvido (ou "mitigado -- ver detalhe") e explique brevemente qual das opções foi
adotada e por quê, já que o próprio documento observa que "H-02 depende da política efetiva de
IAM da fila compartilhada; essa política não está no checkout".

## 3. Testes

Em `tests/test_worker_tasks.py`, adicione:

- Mensagem com `project_id` que não corresponde à fila/assinatura esperada -- deve virar
  `MalformedMessageError`, sem envio (mesmo padrão dos testes de payload malformado já
  existentes nesse arquivo).
- Mensagem legítima (project_id correto e, se Opção B, assinatura válida) -- processa
  normalmente, sem regressão no caminho feliz já coberto.

Rode `poetry run pytest -q` ao final.
