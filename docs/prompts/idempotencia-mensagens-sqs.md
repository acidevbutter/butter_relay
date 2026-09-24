**Atualização (2026-09-24):** o transporte migrou de SQS para Celery/Redis desde que este prompt
foi escrito. `app/worker/consumer.py` não existe mais (ver `app/worker/tasks.py`) e a coluna
`sqs_message_id` foi renomeada para `broker_message_id` (migration
`20260915_1400_a3f7c1d92b6e_rename_sqs_message_id_to_broker_`). O passo 2 abaixo (lookup antes de
criar) **já foi implementado** nessa migração de transporte — `app/worker/tasks.py:81-84` chama
`MessageService.get_by_broker_message_id` (`app/messages/service.py:29-30`,
`app/messages/repository.py:32-35`) e retorna sem reenviar se já `SENT`. O que falta de H-03
continua sendo só os passos 1 e 3: a coluna ainda não tem `unique=True` e `MessageRepository.create`
ainda não trata `IntegrityError` de corrida. O corpo abaixo foi atualizado para os nomes atuais.

---

Corrija o achado H-03 de `docs/butter-relay-fluxos.md` (detalhado em
`docs/auditoria-seguranca-2026-08-29.md`, item 6): o envio de mensagem não é idempotente --
redelivery/retry da task Celery pode criar `Message` duplicada e reenviar e-mail já enviado.

## 1. Constraint de unicidade em `broker_message_id`

Em `app/messages/models.py`, troque:

```python
broker_message_id: Mapped[str | None] = mapped_column(String(200), default=None, index=True)
```

por `unique=True` (mantendo `nullable`, já que mensagens criadas por `POST /admin/messages/{id}/resend`
não têm `broker_message_id`):

```python
broker_message_id: Mapped[str | None] = mapped_column(String(200), default=None, unique=True, index=True)
```

Crie uma migration Alembic nova em `alembic/versions/` (seguir o padrão de
`alembic/versions/20260915_1400_a3f7c1d92b6e_rename_sqs_message_id_to_broker_.py`) adicionando o
índice único (Postgres permite múltiplos `NULL` com `UNIQUE`, então não precisa de `WHERE`):

```python
op.create_index(
    "ix_messages_broker_message_id_unique", "messages", ["broker_message_id"], unique=True,
)
```

(remova o índice não-único antigo, criado por essa mesma migration de rename).

## 2. Buscar antes de criar no worker -- já feito

`app/worker/tasks.py::process_message` já faz o lookup por `broker_message_id` antes de criar
(linhas 81-84) e retorna sem reenviar se o status já é `SENT`. Nenhuma ação aqui, a não ser
confirmar que o comportamento de `PROCESSING`/`FAILED` (reaproveitar a `Message` existente em vez
de criar uma nova linha, sem fragmentar `attempts`) já está correto -- se não estiver, ajuste.

## 3. Capturar violação de unicidade

No `MessageRepository.create` (`app/messages/repository.py`), envolva o `INSERT` num
`try/except` para `sqlalchemy.exc.IntegrityError` na constraint nova -- em caso de corrida (duas
entregas concorrentes da mesma task, mesmo `broker_message_id`), faça `session.rollback()` e
retorne a linha existente via o lookup do passo 2, em vez de deixar o erro de banco cru subir.

## 4. Testes

Em `tests/test_worker_tasks.py`:

- Processar a mesma task (mesmo `broker_message_id`) duas vezes -- confirme que só existe uma
  `Message` no banco e que o e-mail não é enviado duas vezes (mock do `EmailProvider` chamado
  uma única vez na segunda rodada, ou nem chamado se já `SENT`).
- Teste de integridade: inserir duas `Message` com o mesmo `broker_message_id` diretamente via
  repository -- confirme que a segunda não gera `IntegrityError` cru (é tratada).

Rode `poetry run pytest -q` e aplique a migration em dev (`alembic upgrade head`) antes de
considerar concluído.
