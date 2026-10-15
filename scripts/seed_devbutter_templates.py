"""Create missing transactional templates for a DevButter Relay project.

Run in the Relay environment (or its API container):
    python scripts/seed_devbutter_templates.py <project-uuid>

Existing active templates are left untouched. No messages are sent.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from pathlib import Path

from app.db.session import session_factory
from app.projects.models import Project
from app.templates_.repository import TemplateRepository
from app.templates_.schemas import TemplateCreate
from app.templates_.service import TemplateService

HTML = (Path(__file__).parent / "templates" / "devbutter_base.html").read_text(
    encoding="utf-8"
)

TEMPLATES = (
    (
        "devbutter_quote_approved",
        "Seu orçamento foi aprovado",
        "Orçamento aprovado",
        "Seu orçamento foi aprovado. Confira os dados e acesse o pagamento abaixo.",
    ),
    (
        "devbutter_payment_status_changed",
        "Atualização do pagamento: {{ status | default('') }}",
        "Pagamento atualizado",
        "O status do seu pagamento foi atualizado.",
    ),
    (
        "devbutter_pending_account_link",
        "Seu orçamento está no portal DevButter",
        "Orçamento salvo",
        "Salvamos uma solicitação de orçamento vinculada à sua conta. "
        "Acesse o portal para acompanhar.",
    ),
    (
        "devbutter_quote_received",
        "Recebemos seu orçamento {{ quote_reference | default('') }}",
        "Orçamento recebido",
        "Recebemos sua solicitação de orçamento. Nossa equipe vai revisar os detalhes e "
        "atualizar o status no portal.",
    ),
    (
        "devbutter_password_recovery",
        "Redefina sua senha da DevButter",
        "Redefinição de senha",
        "Recebemos um pedido para redefinir a senha da sua conta.",
    ),
    (
        "devbutter_subaccount_onboarding",
        "Configure os recebimentos de {{ company | default('') }}",
        "Configure seus recebimentos",
        "Sua conta de recebimentos está pronta para configuração.",
    ),
    (
        "devbutter_commercial_info_expiring",
        "Atualize os dados comerciais de {{ company | default('') }}",
        "Atualização de dados comerciais",
        "Alguns dados comerciais precisam ser atualizados para manter os recebimentos ativos.",
    ),
)


async def seed(project_id: uuid.UUID) -> tuple[int, int]:
    created = skipped = 0
    async with session_factory() as session:
        if await session.get(Project, project_id) is None:
            raise SystemExit(f"Relay project {project_id} was not found")
        repository = TemplateRepository(session)
        service = TemplateService(repository)
        for key, subject, title, message in TEMPLATES:
            if await repository.get_active(project_id, key) is not None:
                skipped += 1
                continue
            await service.publish(
                TemplateCreate(
                    project_id=project_id,
                    key=key,
                    subject_template=subject,
                    html_template=HTML.replace("__DEVButter_TITLE__", title).replace(
                        "__DEVButter_MESSAGE__", message
                    ),
                    variables_schema={"type": "object"},
                )
            )
            created += 1
    return created, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_id", type=uuid.UUID)
    args = parser.parse_args()
    created, skipped = asyncio.run(seed(args.project_id))
    print(f"DevButter Relay templates: {created} created, {skipped} already active")


if __name__ == "__main__":
    main()
