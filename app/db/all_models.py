"""Imported by alembic/env.py so Base.metadata sees every table before autogenerate.

Adding a new domain's `models` module here is required -- a domain added
without updating this import list is silently invisible to
`alembic revision --autogenerate`.
"""

from app.api_keys import models as api_keys_models  # noqa: F401
from app.auth import models as auth_models  # noqa: F401
from app.messages import models as messages_models  # noqa: F401
from app.projects import models as projects_models  # noqa: F401
from app.templates_ import models as templates_models  # noqa: F401
