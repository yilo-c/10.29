from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ._protocols import *

logger = logging.getLogger("siyi.repositories")
class LocalImprovementProposalRepository:

    """本地监督式自优化提案仓储，直接读写 improvement_proposals.json。"""



    def __init__(self, data_store):

        self.data_store = data_store



    def list_proposals(self) -> list[dict[str, Any]]:

        if self.data_store is None:

            return []

        proposals = self.data_store.load_json("improvement_proposals", [])

        changed = False

        max_id = max((item.get("id", 0) or 0 for item in proposals), default=0)

        for item in proposals:

            if not item.get("id"):

                max_id += 1

                item["id"] = max_id

                changed = True

            if not item.get("created_at"):

                item["created_at"] = datetime.now(timezone.utc).isoformat()

                changed = True

            if not item.get("updated_at"):

                item["updated_at"] = item.get("created_at")

                changed = True

            if not item.get("status"):

                item["status"] = "pending_review"

                changed = True

        if changed:

            self.data_store.save_json("improvement_proposals", proposals)

        return sorted(

            proposals,

            key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),

            reverse=True,

        )



    def add_proposal(self, proposal_info: dict[str, Any]) -> int:

        if self.data_store is None:

            return 0

        now = datetime.now(timezone.utc).isoformat()

        proposal = dict(proposal_info)

        new_id: list[int] = [0]



        def _apply(proposals: Any) -> list[dict[str, Any]]:

            if not isinstance(proposals, list):

                proposals = []

            new_id[0] = self.data_store.next_id(proposals)

            proposal["id"] = new_id[0]

            proposal.setdefault("title", "未命名提案")

            proposal.setdefault("content", "")

            proposal.setdefault("status", "pending_review")

            proposal.setdefault("created_at", now)

            proposal.setdefault("updated_at", now)

            proposals.append(proposal)

            return proposals



        self.data_store.update_json("improvement_proposals", [], _apply)

        return new_id[0]





    def update_proposal(self, proposal_id: int, new_info: dict[str, Any]) -> None:

        if self.data_store is None:

            return

        now = datetime.now(timezone.utc).isoformat()



        def _apply(proposals: Any) -> list[dict[str, Any]]:

            if not isinstance(proposals, list):

                return []

            for proposal in proposals:

                if proposal.get("id") != proposal_id:

                    continue

                proposal.update(new_info)

                proposal["updated_at"] = now

                break

            return proposals



        self.data_store.update_json("improvement_proposals", [], _apply)



    def get_proposal(self, proposal_id: int) -> dict[str, Any] | None:

        for proposal in self.list_proposals():

            if proposal.get("id") == proposal_id:

                return dict(proposal)

        return None





