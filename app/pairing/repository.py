import uuid
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.pairing.models import PairingCode


class PairingRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        code_hash: str,
        project_id: uuid.UUID,
        label: str,
        site_host: str,
        approved_by: str,
        expires_at: datetime,
    ) -> PairingCode:
        pairing = PairingCode(
            code_hash=code_hash,
            project_id=project_id,
            label=label,
            site_host=site_host,
            approved_by=approved_by,
            expires_at=expires_at,
        )
        self.session.add(pairing)
        await self.session.commit()
        await self.session.refresh(pairing)
        return pairing

    async def get_by_hash(self, code_hash: str) -> PairingCode | None:
        result = await self.session.execute(
            select(PairingCode).where(PairingCode.code_hash == code_hash)
        )
        return result.scalar_one_or_none()

    async def consume(self, pairing_id: uuid.UUID) -> bool:
        """Marks the code used. False when another request already did (single use)."""
        result = await self.session.execute(
            update(PairingCode)
            .where(PairingCode.id == pairing_id, PairingCode.used_at.is_(None))
            .values(used_at=datetime.now(UTC))
        )
        await self.session.commit()
        return bool(cast(CursorResult[tuple[()]], result).rowcount)
