from datetime import UTC, datetime

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/admin/templates")
templates.env.globals["now"] = lambda: datetime.now(UTC)
