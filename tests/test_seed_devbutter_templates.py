import uuid
from types import SimpleNamespace

from app.templates_.schemas import TemplateCreate
from app.templates_.service import TemplateService
from scripts.seed_devbutter_templates import HTML, TEMPLATES


def test_devbutter_templates_validate_and_render():
    service = TemplateService(repository=None)
    payload = {
        "name": "Ana",
        "quote_reference": "Q-123",
        "company": "Acme",
        "scope_summary": "Website redesign",
        "price_range": "R$ 5.000 a R$ 8.000",
        "timeline": "4 semanas",
        "amount": "R$ 500,00",
        "due_date": "2026-10-30",
        "status": "PAID",
        "status_label": "Pago",
        "scheduled_date": "2026-10-30",
        "expires_at": "2026-10-16T12:00:00",
        "payment_link": "https://example.com/payment",
        "password_setup_url": "https://example.com/setup",
        "password_recovery_url": "https://example.com/reset",
        "onboarding_url": "https://example.com/onboarding",
        "portal_url": "https://example.com/portal",
    }

    assert len({key for key, *_ in TEMPLATES}) == 7
    for key, subject, title, message in TEMPLATES:
        html = HTML.replace("__DEVButter_TITLE__", title).replace(
            "__DEVButter_MESSAGE__", message
        )
        template = TemplateCreate(
            project_id=uuid.uuid4(),
            key=key,
            subject_template=subject,
            html_template=html,
            variables_schema={"type": "object"},
        )
        service._validate_definition(template)
        rendered_subject, rendered_html = service.render(
            SimpleNamespace(
                subject_template=subject,
                html_template=html,
                variables_schema={"type": "object"},
            ),
            payload,
        )

        assert rendered_subject
        assert title in rendered_html
        assert message in rendered_html
        assert "{{" not in rendered_html
