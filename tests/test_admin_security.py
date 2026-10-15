"""DVB-205: admin pages ship a restrictive CSP and previews never run template HTML."""

import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.projects.repository import ProjectRepository
from app.templates_.repository import TemplateRepository

FORM = {
    "name": "Acme",
    "slug": "acme",
    "default_sender_email": "noreply@acme.com",
    "default_sender_name": "Acme",
}
HOSTILE_HTML = (
    "<p>Hi {{ name }}</p><script>alert('xss-script')</script>"
    '<img src="https://evil.example/pixel.png" onerror="alert(\'xss-event\')">'
    '<a href="javascript:alert(1)">x</a>'
)


async def _project_with_template(
    admin_client, db_session: AsyncSession
) -> tuple[uuid.UUID, uuid.UUID]:
    await admin_client.post("/admin/projects", data=FORM)
    project = await ProjectRepository(db_session).get_by_slug("acme")
    assert project is not None
    project_id = project.id
    await admin_client.post(
        f"/admin/projects/{project_id}/templates",
        data={"key": "hostile", "subject_template": "Hi {{ name }}", "html_template": HOSTILE_HTML},
    )
    template = (await TemplateRepository(db_session).list_for_project(project_id))[0]
    return project_id, template.id


async def test_admin_pages_send_a_nonce_based_csp(admin_client) -> None:
    first = await admin_client.get("/admin/projects")
    second = await admin_client.get("/admin/projects")

    csp = first.headers["content-security-policy"]
    assert "script-src 'nonce-" in csp
    assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
    assert "'unsafe-eval'" not in csp
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    nonce = re.search(r"'nonce-([^']+)'", csp)
    assert nonce is not None
    assert f'nonce="{nonce.group(1)}"' in first.text  # the htmx script carries it
    assert nonce.group(1) not in second.headers["content-security-policy"]  # per request


async def test_security_headers_on_every_response(client) -> None:
    response = await client.get("/health")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "content-security-policy" not in response.headers  # JSON, not an HTML page


async def test_project_page_has_no_inline_event_handlers(admin_client, db_session) -> None:
    project_id, _ = await _project_with_template(admin_client, db_session)

    page = await admin_client.get(f"/admin/projects/{project_id}")

    # JSON/plain-text <script type=...> blocks are inert data (they hold the escaped
    # template source); everything else must have no on*= attribute on any real tag.
    markup = re.sub(r"<script[^>]*type=[^>]*>.*?</script>", "", page.text, flags=re.DOTALL)
    assert not re.search(r"<[a-z][^>]*\son[a-z]+\s*=", markup, flags=re.IGNORECASE)
    assert "data-edit-template=" in page.text
    for tag in re.findall(r"<script(?![^>]*type=)[^>]*>", page.text):
        assert "nonce=" in tag, tag


async def test_preview_renders_hostile_template_inert(admin_client, db_session) -> None:
    _, template_id = await _project_with_template(admin_client, db_session)

    response = await admin_client.post(
        f"/admin/templates/{template_id}/preview",
        data={"sample_payload": '{"name": "\\"><script>alert(\'xss-payload\')</script>"}'},
    )

    body = response.text
    assert '<iframe sandbox=""' in body  # opaque origin, scripts/forms/popups blocked
    # everything template- or payload-controlled lives escaped inside the srcdoc attribute
    assert "<script>alert(" not in body
    assert "<img" not in body
    assert "&lt;script&gt;" in body
    # a payload cannot break out of the attribute or the iframe
    assert body.count("<iframe") == 1
    assert "</iframe><script" not in body
    assert "content-security-policy" in {k.lower() for k in response.headers}
