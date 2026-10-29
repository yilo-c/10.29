from __future__ import annotations

from automation_hub.api.db import (
    delete_personal_news_item,
    list_personal_news_items,
    upsert_personal_news_item,
)
from automation_hub.api.routes.personal_factory import PersonalEntity, create_personal_router

router = create_personal_router(PersonalEntity(
    prefix="/personal/news",
    tag="personal-news",
    scope_read="news:read",
    scope_write="news:write",
    response_key="items",
    detail_not_found="news item not found",
    db_list=list_personal_news_items,
    db_upsert=upsert_personal_news_item,
    db_delete=delete_personal_news_item,
))
