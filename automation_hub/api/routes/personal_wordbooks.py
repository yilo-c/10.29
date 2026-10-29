from __future__ import annotations

from automation_hub.api.db.vocabulary import (
    delete_personal_wordbook,
    list_personal_wordbooks,
    upsert_personal_wordbook,
)
from automation_hub.api.routes.personal_factory import PersonalEntity, create_personal_router

router = create_personal_router(PersonalEntity(
    prefix="/personal/wordbooks",
    tag="personal-wordbooks",
    scope_read="vocabulary:read",
    scope_write="vocabulary:write",
    response_key="wordbooks",
    detail_not_found="wordbook not found",
    db_list=list_personal_wordbooks,
    db_upsert=upsert_personal_wordbook,
    db_delete=delete_personal_wordbook,
))
