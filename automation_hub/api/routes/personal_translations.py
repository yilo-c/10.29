from __future__ import annotations

from automation_hub.api.db import (
    delete_personal_translation_record,
    list_personal_translation_history,
    upsert_personal_translation_record,
)
from automation_hub.api.routes.personal_factory import PersonalEntity, create_personal_router

router = create_personal_router(PersonalEntity(
    prefix="/personal/translations",
    tag="personal-translations",
    scope_read="translation:read",
    scope_write="translation:write",
    response_key="records",
    detail_not_found="translation record not found",
    db_list=list_personal_translation_history,
    db_upsert=upsert_personal_translation_record,
    db_delete=delete_personal_translation_record,
))
