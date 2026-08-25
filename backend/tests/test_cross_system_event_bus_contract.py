import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db.db_models import CrossSystemEventDelivery, Project
from app.services.cross_system_event_bus import CrossSystemEventBus


@pytest.mark.asyncio
async def test_expired_delivery_claim_is_recovered_instead_of_lost(db):
    project_id = uuid.uuid4()
    db.add(Project(id=project_id, name="event-lease-project"))
    await db.commit()
    bus = CrossSystemEventBus()
    await bus.publish_event(
        db,
        str(project_id),
        "CHAPTER_SYNCED.v1",
        "test",
        payload={"chapter_number": 1},
    )
    await db.commit()

    first_claim = await bus.claim_deliveries(
        db, str(project_id), "outline", "consumer-one", lease_ttl_seconds=5,
    )
    assert len(first_claim) == 1
    delivery_id = first_claim[0].id
    first_claim[0].claimed_at = datetime.now(timezone.utc) - timedelta(seconds=30)
    await db.commit()

    second_claim = await bus.claim_deliveries(
        db, str(project_id), "outline", "consumer-two", lease_ttl_seconds=5,
    )
    await db.commit()

    assert [row.id for row in second_claim] == [delivery_id]
    stored = await db.get(CrossSystemEventDelivery, delivery_id)
    assert stored.claimed_by == "consumer-two"
    assert stored.status == "delivered"
