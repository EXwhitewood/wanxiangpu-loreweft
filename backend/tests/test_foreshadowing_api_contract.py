import uuid

import pytest
from fastapi import HTTPException

from app.api import foreshadowing
from app.db.db_models import ForeshadowingCausalEdge, ForeshadowingLine, Project


async def _seed_project_line(db, *, project_id: uuid.UUID, name: str) -> ForeshadowingLine:
    project = await db.get(Project, project_id)
    if not project:
        db.add(Project(id=project_id, name=f"project-{name}"))
    line = ForeshadowingLine(
        id=uuid.uuid4(),
        project_id=project_id,
        name=name,
        secret_canonical_statement=f"{name} 的真相",
    )
    db.add(line)
    await db.commit()
    return line


@pytest.mark.asyncio
async def test_line_reads_are_scoped_to_route_project(db):
    owner_id = uuid.uuid4()
    other_id = uuid.uuid4()
    line = await _seed_project_line(db, project_id=owner_id, name="owner-line")
    db.add(Project(id=other_id, name="other"))
    await db.commit()

    with pytest.raises(HTTPException) as missing:
        await foreshadowing.get_line(other_id, line.id, db)
    assert missing.value.status_code == 404


@pytest.mark.asyncio
async def test_cross_project_lines_cannot_be_linked(db):
    first_project = uuid.uuid4()
    second_project = uuid.uuid4()
    source = await _seed_project_line(db, project_id=first_project, name="source")
    target = await _seed_project_line(db, project_id=second_project, name="target")
    request = foreshadowing.EdgeCreateRequest(
        source_id=source.id,
        target_id=target.id,
        edge_type="depends_on",
    )

    with pytest.raises(HTTPException) as missing:
        await foreshadowing.create_edge(first_project, request, db)
    assert missing.value.status_code == 404


@pytest.mark.asyncio
async def test_edge_deletion_is_scoped_to_route_project(db):
    owner_id = uuid.uuid4()
    other_id = uuid.uuid4()
    source = await _seed_project_line(db, project_id=owner_id, name="source")
    target = await _seed_project_line(db, project_id=owner_id, name="target")
    db.add(Project(id=other_id, name="other"))
    edge = ForeshadowingCausalEdge(
        id=uuid.uuid4(),
        project_id=owner_id,
        source_id=source.id,
        target_id=target.id,
        edge_type="depends_on",
    )
    db.add(edge)
    await db.commit()

    with pytest.raises(HTTPException) as missing:
        await foreshadowing.delete_edge(other_id, edge.id, db)
    assert missing.value.status_code == 404
    assert await db.get(ForeshadowingCausalEdge, edge.id) is not None
