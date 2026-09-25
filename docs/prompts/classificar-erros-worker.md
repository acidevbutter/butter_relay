**Atualização (2026-09-24) -- M-01/M-02 resolvidos por outra via, não pelas mudanças abaixo:** o
transporte migrou de SQS para Celery/Redis. Não existe mais `run_forever`/loop manual nem
`delete_message` -- `app/worker/consumer.py` foi removido, e Celery cuida do ack via
`acks_late=True` (`app/worker/celery_app.py`/`tasks.py`). A classificação de erro permanente vs.
transitório que este prompt pedia já existe, de outra forma: `autoretry_for=(NotFoundError,
EmailSendError, ValidationFailedError)` + `MAX_RETRIES=5` + `retry_backoff`/`retry_backoff_max`/
`retry_jitter` em `app/worker/tasks.py:147-156` cobre o "erro transitório com backoff"; já
`MalformedMessageError` é logada e descartada sem retry em `process_message_task`
(`app/worker/tasks.py:191-193`), que é o "erro permanente não reprocessa" do finding original.
Não há mais uma chamada de delete separada que possa falhar e derrubar o processo (achado da
seção 2 abaixo) -- Celery já trata isso internamente. O corpo original abaixo (referências a
`consumer.py`/SQS) é mantido como registro histórico do raciocínio, mas não deve ser seguido ao
pé da letra hoje.

---

Corrija os achados M-01/M-02 de `docs/butter-relay-fluxos.md` (detalhado em
`docs/auditoria-seguranca-2026-08-29.md`, item 10): `run_forever` só trata 3 tipos de exceção e
não distingue erro permanente de transitório.

## 1. Capturar e classificar todos os erros do laço principal

Em `app/worker/consumer.py::run_forever`, o `try/except` hoje é:

```python
except (MalformedMessageError, NotFoundError, EmailSendError):
    continue
```

Amplie para capturar `Exception` genérico também, mas **classifique** antes de decidir o que
fazer com a mensagem SQS:

```python
except (MalformedMessageError, NotFoundError, EmailSendError):
    # erro permanente conhecido -- já registrado em Message.status pelo service
    continue
except Exception:
    logger.exception(
        "unexpected error processing sqs message %s", sqs_message.get("MessageId")
    )
    continue
```

Garanta que o `except Exception` **não** apaga a mensagem SQS (deixe expirar o visibility timeout
para redrive/DLQ, igual ao comportamento já documentado para os outros erros) e que ele loga com
`request_id`/`MessageId` suficiente para correlação.

## 2. Cobrir falha do próprio `delete_message`

Hoje, se `sqs.delete_message` (chamado no `else` do loop, fora do `try`) falhar, a exceção sobe
e mata `run_forever` inteiro. Envolva essa chamada em seu próprio `try/except`, logando o erro
sem derrubar o processo -- a mensagem já foi processada com sucesso (`Message.status = SENT`);
uma falha ao deletar só significa que o SQS pode reentregá-la, o que o worker já sabe tratar via
o lookup de idempotência de `docs/prompts/idempotencia-mensagens-sqs.md` (aplique esse prompt
antes ou junto deste, para que a reentrega pós-`delete_message` falho não gere e-mail duplicado).

## 3. Distinguir erro transitório de permanente (para backoff)

`EmailSendError` hoje trata 401 do Brevo, template ausente e payload inválido da mesma forma
(deixa expirar visibility timeout). Adicione um campo/atributo em `EmailSendError` (ver
`app/core/email_provider.py`) marcando se o erro é retryable (ex. 5xx/timeout do Brevo) ou
permanente (ex. 401/422 do Brevo, credencial inválida). Para erro permanente, marque
`Message.status = FAILED` (já faz isso) mas **delete** a mensagem SQS em vez de deixá-la para
redrive -- retry não vai resolver um 401. Para erro transitório, mantenha o comportamento atual
(não deleta, deixa redeliverar).

## 4. Testes

Em `tests/test_worker_consumer.py`:

- Erro genérico não classificado (ex. `RuntimeError` simulado dentro de `process_sqs_message`)
  -- confirme que `run_forever` não propaga e continua o loop.
- Falha simulada de `delete_message` após sucesso de envio -- confirme que não derruba o
  processo e que a `Message` já está `SENT` no banco.
- `EmailSendError` marcado como permanente (ex. 401) -- confirme que a mensagem SQS é deletada
  mesmo com falha; `EmailSendError` transitório (ex. 503) -- confirme que não é deletada.

Rode `poetry run pytest -q` ao final.
