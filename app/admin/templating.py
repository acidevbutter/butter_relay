from datetime import UTC, datetime

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/admin/templates")
templates.env.globals["now"] = lambda: datetime.now(UTC)


def _is_expired(moment: datetime | None) -> bool:
    """SQLite (tests) hands back naive datetimes; Postgres returns aware ones."""
    if moment is None:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment < datetime.now(UTC)


templates.env.globals["is_expired"] = _is_expired
