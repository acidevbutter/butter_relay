#!/usr/bin/env sh
# Runs before every container start (fresh boot, restart, or rebuild) --
# both the production CMD and the dev override's `uvicorn --reload`
# command arrive here as "$@". Applying migrations here, instead of asking
# whoever runs the stack to remember a separate step, is what keeps the
# schema from silently drifting behind app/*/models.py.
#
# RUN_MIGRATIONS=false skips the upgrade: Celery workers and any extra API
# replica set it so only one process per deploy races for the alembic lock.
set -eu

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  alembic upgrade head
fi

exec "$@"
