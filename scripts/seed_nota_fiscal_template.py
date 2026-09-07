"""Publish nota_fiscal_emitida for a Project (admin API or one-shot).

    poetry run python scripts/seed_nota_fiscal_template.py <project-uuid>
"""

from __future__ import annotations

import asyncio
import sys
import uuid

from app.db.session import session_factory
from app.templates_.repository import TemplateRepository
from app.templates_.schemas import TemplateCreate
from app.templates_.service import TemplateService

HTML = """<!doctype html>
<html lang="pt-BR">
<head><meta charset="utf-8"><title>{{ title }}</title></head>
<body style="margin:0;padding:0;background-color:#f2f2f2;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center" style="padding:24px 12px;">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0">
        <tr><td align="center" style="padding:16px 24px;">
          <img src="{{ logo_url }}" alt="{{ sender_name | default('DevButter') }}" width="140">
        </td></tr>
        <tr><td style="background-color:#141628;border-radius:8px;padding:32px 40px;">
          <p style="color:#a9adc1;">Olá, {{ name }}</p>
          <h1 style="color:#ffffff;font-size:22px;">{{ title }}</h1>
          <p style="color:#e4e1cd;">{{ message }}</p>
          {% if nfse_number %}<p style="color:#e4e1cd;">Número: {{ nfse_number }}</p>{% endif %}
          <p>
            <a href="{{ cta_link }}"
               style="background-color:#79E59B;color:#141628;
                      padding:14px 28px;text-decoration:none;border-radius:6px;">
              {{ cta_text }}
            </a>
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
"""

VARIABLES_SCHEMA = {
    "type": "object",
    "required": ["name", "title", "message", "cta_text", "cta_link", "logo_url"],
    "properties": {
        "name": {"type": "string"},
        "title": {"type": "string"},
        "message": {"type": "string"},
        "preheader": {"type": "string"},
        "cta_text": {"type": "string"},
        "cta_link": {"type": "string", "format": "uri"},
        "logo_url": {"type": "string", "format": "uri"},
        "blob_id": {"type": "string"},
        "nfse_id": {"type": "string"},
        "nfse_number": {"type": "string"},
        "sender_name": {"type": "string"},
        "project_name": {"type": "string"},
    },
}


async def seed(project_id: uuid.UUID) -> None:
    async with session_factory() as session:
        service = TemplateService(TemplateRepository(session))
        await service.publish(
            TemplateCreate(
                project_id=project_id,
                key="nota_fiscal_emitida",
                subject_template="Sua nota fiscal {{ nfse_number | default('') }} foi emitida",
                html_template=HTML,
                variables_schema=VARIABLES_SCHEMA,
            )
        )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python scripts/seed_nota_fiscal_template.py <project-uuid>")
    asyncio.run(seed(uuid.UUID(sys.argv[1])))
