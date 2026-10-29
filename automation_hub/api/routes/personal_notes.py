from __future__ import annotations

from automation_hub.api.db import (
    delete_personal_note,
    get_personal_note,
    list_personal_notes,
    upsert_personal_note,
)
from automation_hub.api.routes.personal_factory import PersonalEntity, create_personal_router

router = create_personal_router(PersonalEntity(
    prefix="/personal/notes",
    tag="personal-notes",
    scope_read="note:read",
    scope_write="note:write",
    response_key="note",
    detail_not_found="note not found",
    db_list=list_personal_notes,
    db_get=get_personal_note,
    db_upsert=upsert_personal_note,
    db_delete=delete_personal_note,
))
