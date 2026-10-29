from __future__ import annotations

from automation_hub.api.db.vocabulary import (
    delete_personal_word,
    list_personal_words,
    upsert_personal_word,
)
from automation_hub.api.routes.personal_factory import PersonalEntity, create_personal_router

router = create_personal_router(PersonalEntity(
    prefix="/personal/words",
    tag="personal-words",
    scope_read="vocabulary:read",
    scope_write="vocabulary:write",
    response_key="words",
    detail_not_found="word not found",
    db_list=list_personal_words,
    db_upsert=upsert_personal_word,
    db_delete=delete_personal_word,
))
