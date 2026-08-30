Corrija o achado M-06 de `docs/butter-relay-fluxos.md` (detalhado em
`docs/auditoria-seguranca-2026-08-29.md`, item 8): o worker ignora `Project.is_active` e
`Project.sqs_queue_url`.

## 1. Checar `is_active` antes de processar

Em `app/worker/consumer.py::process_sqs_message`, logo depois de
`project = await project_service.get(project_id)`, adicione:

```python
if not project.is_active:
    raise MalformedMessageError(f"Project {project_id} is not active")
```

(reaproveite `MalformedMessageError` -- já é tratado em `run_forever` como "não deleta, não
reprocessa infinitamente"; se preferir um comportamento diferente -- ex. deletar a mensagem sem
reprocessar, já que reprocessar um projeto inativo não vai adiantar -- ajuste `run_forever` para
tratar esse caso separadamente e chamar `sqs.delete_message` mesmo em erro deste tipo específico.
Documente a escolha.)

## 2. Decidir o uso de `Project.sqs_queue_url`

`Project.sqs_queue_url` existe no modelo (`app/projects/models.py`) e é setado na criação do
projeto (`app/admin/router.py` form de criação), mas o worker só usa
`settings.sqs_queue_url` (global, único). Duas opções -- escolher uma e documentar em
`docs/butter-relay-fluxos.md`:

- Se a arquitetura pretendida é realmente uma única fila compartilhada (como o nome
  `relay-shared` em `.env.example` sugere), **remova** `sqs_queue_url` de
  `app/projects/models.py`/`ProjectCreate`/formulário de criação -- é um campo morto que sugere
  uma garantia que não existe, o que é enganoso para quem lê o código achando que cada projeto
  tem isolamento de fila.
- Se a intenção é migrar para fila por projeto (ver também
  `docs/prompts/validar-project-id-worker-sqs.md`, Opção A), implemente o worker rodando N
  pollers (um por `project.sqs_queue_url` ativo) em vez de um único loop com
  `settings.sqs_queue_url`.

## 3. Testes

Em `tests/test_worker_consumer.py`:

- Mensagem SQS referenciando um projeto com `is_active=False` -- confirme que não é enviado
  e-mail (mock do `EmailProvider` não chamado) e que o comportamento de delete/retry da mensagem
  SQS segue a decisão tomada no passo 1.
- Mensagem referenciando projeto `is_active=True` -- caminho feliz sem regressão.

Rode `poetry run pytest -q` ao final.
