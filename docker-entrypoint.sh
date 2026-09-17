#!/usr/bin/env sh
# Runs before every container start (fresh boot, restart, or rebuild) --
# both the production CMD and the dev override's `uvicorn --reload`
# command arrive here as "$@". Applying migrations here, instead of asking
# whoever runs the stack to remember a separate step, is what keeps the
# schema from silently drifting behind app/*/models.py.
set -eu

alembic upgrade head

exec "$@"
