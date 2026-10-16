"""Clear personal data past MESSAGE_BODY_RETENTION_DAYS (DVB-208).

Run in the Relay environment (or its API container), e.g. daily from a scheduler:
    python -m scripts.purge_message_bodies [--days N]
"""

from __future__ import annotations

import argparse
import asyncio

import app.db.all_models  # noqa: F401  -- registers every table for the ORM
from app.worker.retention import purge_expired_bodies


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=None, help="override the retention")
    args = parser.parse_args()
    result = asyncio.run(purge_expired_bodies(args.days))
    print(
        f"Purged payloads of {result['messages']} messages and recipients of "
        f"{result['rejected']} rejected tasks older than {result['retention_days']} days"
    )


if __name__ == "__main__":
    main()
