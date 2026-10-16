"""Admin dashboard controllers: form errors re-render readable pages, never JSON/500."""

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.email_provider import EmailSendError, ProviderResult
from app.messages.models import MessageStatus
from app.messages.repository import MessageRepository
from app.messages.schemas import MessageCreate
from app.projects.repository import ProjectRepository
from app.templates_.repository import TemplateRepository

FORM = {
    "name": "Acme",
    "slug": "acme",
    "default_sender_email": "noreply@acme.com",
    "default_sender_name": "Acme",
}


async def _create_project(admin_client, **overrides) -> str:
    response = await admin_client.post("/admin/projects", data={**FORM, **overrides})
    assert response.status_code == 200, response.text
    return response.text


async def _project_id(db_session: AsyncSession, slug: str = "acme") -> uuid.UUID:
    project = await ProjectRepository(db_session).get_by_slug(slug)
    assert project is not None
    return project.id


# --- projects ------------------------------------------------------------------


async def test_admin_pages_require_a_session(client) -> None:
    for method, path in [
        ("get", "/admin/projects"),
        ("post", f"/admin/projects/{uuid.uuid4()}/settings"),
        ("post", f"/admin/projects/{uuid.uuid4()}/test-send"),
        ("post", f"/admin/templates/{uuid.uuid4()}/activate"),
    ]:
        response = await getattr(client, method)(path, follow_redirects=False)
        assert response.status_code in (303, 422), (path, response.status_code)
    unauth = await client.get("/admin/projects", follow_redirects=False)
    assert unauth.headers["location"] == "/admin/login"


async def test_create_project_from_form_defaults_to_brevo(admin_client, db_session) -> None:
    page = await _create_project(admin_client)
    assert "acme" in page
    project = await ProjectRepository(db_session).get_by_slug("acme")
    assert project is not None
    assert project.delivery_provider == "brevo"
    assert project.delivery_config == {}


async def test_create_project_with_smtp_channel(admin_client, db_session) -> None:
    await _create_project(
        admin_client, delivery_provider="smtp", smtp_host="smtp.acme.test", smtp_port="2525"
    )
    project = await ProjectRepository(db_session).get_by_slug("acme")
    assert project is not None
    assert project.delivery_provider == "smtp"
    assert project.delivery_config == {"smtp_host": "smtp.acme.test", "smtp_port": 2525}


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"default_sender_email": "not-an-email"}, "default_sender_email"),
        ({"slug": "Bad Slug"}, "slug"),
        ({"delivery_provider": "carrier-pigeon"}, "carrier-pigeon"),
        ({"delivery_provider": "smtp", "smtp_port": "abc"}, "invalid literal"),
        ({"delivery_provider": "smtp", "smtp_port": "70000"}, "smtp_port"),
    ],
)
async def test_create_project_invalid_input_rerenders_with_error(
    admin_client, overrides, expected
) -> None:
    response = await admin_client.post("/admin/projects", data={**FORM, **overrides})
    assert response.status_code == 422
    assert "text/html" in response.headers["content-type"]
    assert expected in response.text
    assert 'class="error"' in response.text


async def test_create_project_duplicate_slug_rerenders_with_conflict(admin_client) -> None:
    await _create_project(admin_client)
    response = await admin_client.post("/admin/projects", data=FORM)
    assert response.status_code == 409
    assert "text/html" in response.headers["content-type"]
    assert "already exists" in response.text


async def test_update_settings_switches_channel_and_deactivates(
    admin_client, db_session
) -> None:
    await _create_project(admin_client)
    project_id = await _project_id(db_session)

    response = await admin_client.post(
        f"/admin/projects/{project_id}/settings",
        data={
            "name": "Acme Inc",
            "default_sender_email": "hello@acme.com",
            "default_sender_name": "Acme Inc",
            "delivery_provider": "smtp",
            "smtp_host": "mailpit",
            "smtp_port": "1025",
        },
    )

    assert response.status_code == 200
    assert "Settings saved." in response.text
    db_session.expire_all()
    project = await ProjectRepository(db_session).get(project_id)
    assert project is not None
    assert (project.name, project.delivery_provider, project.is_active) == (
        "Acme Inc",
        "smtp",
        False,  # is_active checkbox left unchecked in the submitted form
    )
    assert project.delivery_config == {"smtp_host": "mailpit", "smtp_port": 1025}


async def test_update_settings_invalid_email_returns_422_page(admin_client, db_session) -> None:
    await _create_project(admin_client)
    project_id = await _project_id(db_session)

    response = await admin_client.post(
        f"/admin/projects/{project_id}/settings",
        data={
            "name": "Acme",
            "default_sender_email": "nope",
            "default_sender_name": "Acme",
            "delivery_provider": "brevo",
        },
    )

    assert response.status_code == 422
    assert "default_sender_email" in response.text


async def test_project_detail_shows_settings_and_channel(admin_client, db_session) -> None:
    await _create_project(admin_client, delivery_provider="smtp")
    project_id = await _project_id(db_session)

    response = await admin_client.get(f"/admin/projects/{project_id}")

    assert response.status_code == 200
    assert "Settings &amp; delivery channel" in response.text
    assert 'name="delivery_provider"' in response.text
    assert "delivery: <code>smtp</code>" in response.text


# --- test send -----------------------------------------------------------------


async def test_test_send_uses_the_project_provider(
    admin_client, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.admin.router as admin_module

    await _create_project(admin_client, delivery_provider="smtp")
    project_id = await _project_id(db_session)
    fake = AsyncMock()
    fake.send.return_value = ProviderResult(provider_message_id="test-1")
    seen: list[str] = []

    def factory(provider: str, config: dict, _settings: object) -> AsyncMock:
        seen.append(provider)
        return fake

    monkeypatch.setattr(admin_module, "get_email_provider_for_project", factory)

    response = await admin_client.post(
        f"/admin/projects/{project_id}/test-send", data={"to": "me@acme.com"}
    )

    assert response.status_code == 200
    assert seen == ["smtp"]
    assert "Test e-mail sent to me@acme.com via smtp" in response.text
    assert fake.send.await_args.kwargs["to"] == "me@acme.com"


async def test_test_send_provider_failure_is_reported(
    admin_client, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.admin.router as admin_module

    await _create_project(admin_client)
    project_id = await _project_id(db_session)
    fake = AsyncMock()
    fake.send.side_effect = EmailSendError("Brevo send failed (401): unauthorized")
    monkeypatch.setattr(admin_module, "get_email_provider_for_project", lambda *a: fake)

    response = await admin_client.post(
        f"/admin/projects/{project_id}/test-send", data={"to": "me@acme.com"}
    )

    assert response.status_code == 502
    assert "Test send failed: Brevo send failed (401)" in response.text


async def test_test_send_rejects_invalid_recipient(admin_client, db_session) -> None:
    await _create_project(admin_client)
    project_id = await _project_id(db_session)

    response = await admin_client.post(
        f"/admin/projects/{project_id}/test-send", data={"to": "not-an-email"}
    )

    assert response.status_code == 422
    assert "not a valid e-mail" in response.text


# --- templates -----------------------------------------------------------------

TEMPLATE_FORM = {
    "key": "welcome_email",
    "subject_template": "Welcome, {{ name }}!",
    "html_template": "<p>Hi {{ name }}</p>",
}


async def test_publish_template_with_variables_schema(admin_client, db_session) -> None:
    await _create_project(admin_client)
    project_id = await _project_id(db_session)

    response = await admin_client.post(
        f"/admin/projects/{project_id}/templates",
        data={**TEMPLATE_FORM, "variables_schema": '{"type": "object", "required": ["name"]}'},
    )

    assert response.status_code == 200
    assert "Published &#39;welcome_email&#39; v1" in response.text
    template = (await TemplateRepository(db_session).list_for_project(project_id))[0]
    assert template.variables_schema == {"type": "object", "required": ["name"]}


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"subject_template": "Hi {{ name"}, "Invalid Jinja2 in subject"),
        ({"html_template": "{% if x %}<p>"}, "Invalid Jinja2 in html"),
        ({"variables_schema": "{not json"}, "not valid JSON"),
        ({"variables_schema": '{"type": "nope"}'}, "Invalid variables_schema"),
        ({"variables_schema": "[1, 2]"}, "must be a JSON object"),
        ({"key": "has space"}, "key"),
    ],
)
async def test_publish_template_errors_rerender_the_form(
    admin_client, db_session, overrides, expected
) -> None:
    await _create_project(admin_client)
    project_id = await _project_id(db_session)

    response = await admin_client.post(
        f"/admin/projects/{project_id}/templates", data={**TEMPLATE_FORM, **overrides}
    )

    assert response.status_code == 422
    assert "text/html" in response.headers["content-type"]
    assert expected in response.text
    assert await TemplateRepository(db_session).list_for_project(project_id) == []
    # the typed values survive the failed submit
    assert overrides.get("key", "welcome_email") in response.text


async def test_activate_older_version_from_admin(admin_client, db_session) -> None:
    await _create_project(admin_client)
    project_id = await _project_id(db_session)
    for subject in ("v1 {{ name }}", "v2 {{ name }}"):
        await admin_client.post(
            f"/admin/projects/{project_id}/templates",
            data={**TEMPLATE_FORM, "subject_template": subject},
        )
    templates = await TemplateRepository(db_session).list_for_project(project_id)
    v1 = next(t for t in templates if t.version == 1)

    response = await admin_client.post(f"/admin/templates/{v1.id}/activate")

    assert response.status_code == 200
    assert "v1 is now the active version" in response.text
    db_session.expire_all()
    active = await TemplateRepository(db_session).get_active(project_id, "welcome_email")
    assert active is not None and active.version == 1


async def test_preview_reports_render_problems_instead_of_500(admin_client, db_session) -> None:
    await _create_project(admin_client)
    project_id = await _project_id(db_session)
    await admin_client.post(
        f"/admin/projects/{project_id}/templates",
        data={**TEMPLATE_FORM, "variables_schema": '{"type": "object", "required": ["name"]}'},
    )
    template = (await TemplateRepository(db_session).list_for_project(project_id))[0]

    response = await admin_client.post(
        f"/admin/templates/{template.id}/preview", data={"sample_payload": "{}"}
    )

    assert response.status_code == 200
    assert "does not match template schema" in response.text


async def test_preview_iframe_is_sandboxed(admin_client, db_session) -> None:
    await _create_project(admin_client)
    project_id = await _project_id(db_session)
    await admin_client.post(f"/admin/projects/{project_id}/templates", data=TEMPLATE_FORM)
    template = (await TemplateRepository(db_session).list_for_project(project_id))[0]

    response = await admin_client.post(
        f"/admin/templates/{template.id}/preview", data={"sample_payload": '{"name": "A"}'}
    )

    assert '<iframe sandbox=""' in response.text


# --- resend --------------------------------------------------------------------


async def test_resend_uses_project_provider_and_marks_sent(
    admin_client, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.admin.router as admin_module

    await _create_project(admin_client, delivery_provider="smtp")
    project_id = await _project_id(db_session)
    message = await MessageRepository(db_session).create(
        MessageCreate(project_id=project_id, to_email="x@acme.com", provider="smtp")
    )
    await MessageRepository(db_session).mark_failed(message, "boom")
    fake = AsyncMock()
    fake.send.return_value = ProviderResult(provider_message_id="re-1")
    seen: list[str] = []

    def factory(provider: str, config: dict, _settings: object) -> AsyncMock:
        seen.append(provider)
        return fake

    monkeypatch.setattr(admin_module, "get_email_provider_for_project", factory)

    message_id = message.id

    response = await admin_client.post(f"/admin/messages/{message_id}/resend")

    assert response.status_code == 200
    assert seen == ["smtp"]
    db_session.expire_all()
    refreshed = await MessageRepository(db_session).get(message_id)
    assert refreshed is not None and refreshed.status == MessageStatus.SENT


async def test_resend_provider_failure_marks_failed_without_500(
    admin_client, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.admin.router as admin_module

    await _create_project(admin_client)
    project_id = await _project_id(db_session)
    message = await MessageRepository(db_session).create(
        MessageCreate(project_id=project_id, to_email="x@acme.com")
    )
    fake = AsyncMock()
    fake.send.side_effect = EmailSendError("nope")
    monkeypatch.setattr(admin_module, "get_email_provider_for_project", lambda *a: fake)

    message_id = message.id

    response = await admin_client.post(f"/admin/messages/{message_id}/resend")

    assert response.status_code == 200
    db_session.expire_all()
    refreshed = await MessageRepository(db_session).get(message_id)
    assert refreshed is not None
    assert refreshed.status == MessageStatus.FAILED and refreshed.last_error == "nope"


async def test_message_log_shows_the_delivery_channel(admin_client, db_session) -> None:
    await _create_project(admin_client, delivery_provider="smtp")
    project_id = await _project_id(db_session)
    message = await MessageRepository(db_session).create(
        MessageCreate(project_id=project_id, to_email="x@acme.com", provider="smtp")
    )
    message_id = message.id

    listing = await admin_client.get("/admin/messages")
    detail = await admin_client.get(f"/admin/messages/{message_id}")

    assert "<th>Channel</th>" in listing.text
    assert '<td class="muted">smtp</td>' in listing.text
    assert "<th>Channel</th><td>smtp</td>" in detail.text


async def test_rejected_messages_page_lists_unparseable_tasks(admin_client, db_session) -> None:
    from app.rejected_messages.repository import RejectedMessageRepository
    from app.rejected_messages.schemas import RejectedMessageCreate

    await RejectedMessageRepository(db_session).create(
        RejectedMessageCreate(
            broker_message_id="task-bad-admin",
            reason="Malformed relay message: badly formed hexadecimal UUID string",
            raw_project_id="",
            template_key="devbutter_quote_received",
            to_email="ana@example.com",
            payload_keys=["name"],
        )
    )

    page = await admin_client.get("/admin/rejected-messages")

    assert page.status_code == 200
    assert "task-bad-admin" in page.text
    assert "badly formed hexadecimal UUID string" in page.text
    assert "(empty)" in page.text
    assert 'href="/admin/rejected-messages"' in page.text


async def test_rejected_messages_page_requires_login(client) -> None:
    response = await client.get("/admin/rejected-messages", follow_redirects=False)
    assert response.status_code in (302, 303, 401)
