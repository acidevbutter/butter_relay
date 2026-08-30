Corrija o achado H-03 de `docs/butter-relay-fluxos.md` (detalhado em
`docs/auditoria-seguranca-2026-08-29.md`, item 6): o envio de mensagem não é idempotente --
redelivery do SQS cria `Message` duplicada e pode reenviar e-mail já enviado.

## 1. Constraint de unicidade em `sqs_message_id`

Em `app/messages/models.py`, troque:

```python
sqs_message_id: Mapped[str | None] = mapped_column(String(200), default=None, index=True)
```

por `unique=True` (mantendo `nullable`, já que mensagens criadas por `POST /admin/messages/{id}/resend`
não têm `sqs_message_id`):

```python
sqs_message_id: Mapped[str | None] = mapped_column(String(200), default=None, unique=True, index=True)
```

Crie uma migration Alembic nova em `alembic/versions/` (seguir o padrão de
`alembic/versions/20260815_1351_dfb1bc6699a9_initial_schema.py`) adicionando o índice único
parcial (Postgres permite múltiplos `NULL` com `UNIQUE`, então não precisa de `WHERE`):

```python
op.create_index(
    "ix_messages_sqs_message_id_unique", "messages", ["sqs_message_id"], unique=True,
)
```

(remova o índice não-único antigo se ele tiver nome próprio na migration inicial).

## 2. Buscar antes de criar no worker

Em `app/worker/consumer.py::process_sqs_message`, antes de `message_service.create(...)`,
adicione um lookup por `sqs_message_id` (novo método `MessageRepository.get_by_sqs_message_id`,
seguindo o padrão de `get_by_hash` em `app/api_keys/repository.py`). Se já existir uma `Message`
com esse `sqs_message_id`:

- Se `status` já é `SENT`: apenas apague a mensagem SQS (idempotente -- já foi processada) e
  retorne sem reenviar.
- Se `status` é `PROCESSING`/`FAILED`: trate como reentrega de um processamento anterior --
  reaproveite a `Message` existente em vez de criar uma nova (não incrementar `attempts` em
  linhas separadas).

## 3. Capturar violação de unicidade

No `MessageRepository.create` (`app/messages/repository.py`), envolva o `INSERT` num
`try/except` para `sqlalchemy.exc.IntegrityError` na constraint nova -- em caso de corrida (duas
entregas concorrentes do mesmo `sqs_message_id`), faça `session.rollback()` e retorne a linha
existente via o lookup do passo 2, em vez de deixar o erro de banco cru subir.

## 4. Testes

Em `tests/test_worker_consumer.py`:

- Processar a mesma mensagem SQS (mesmo `MessageId`) duas vezes -- confirme que só existe uma
  `Message` no banco e que o e-mail não é enviado duas vezes (mock do `EmailProvider` chamado
  uma única vez na segunda rodada, ou nem chamado se já `SENT`).
- Teste de integridade: inserir duas `Message` com o mesmo `sqs_message_id` diretamente via
  repository -- confirme que a segunda não gera `IntegrityError` cru (é tratada).

Rode `poetry run pytest -q` e aplique a migration em dev (`alembic upgrade head`) antes de
considerar concluído.
