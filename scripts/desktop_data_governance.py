from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _parse_ts(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            return datetime.fromisoformat(candidate).replace(tzinfo=None)
        except ValueError:
            continue
    return None


def _dedupe_inbox(entries: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], int]:
    seen: dict[str, Dict[str, Any]] = {}
    duplicates = 0
    for entry in entries:
        title = str(entry.get("title") or "").strip().lower()
        content = str(entry.get("content") or "").strip().lower()
        source = str(entry.get("source") or "").strip().lower()
        key = hashlib.sha1(f"{title}|{content}|{source}".encode("utf-8")).hexdigest()
        old = seen.get(key)
        if old is None:
            seen[key] = entry
            continue
        old_ts = _parse_ts(old.get("updated_at") or old.get("created_at")) or datetime.min
        new_ts = _parse_ts(entry.get("updated_at") or entry.get("created_at")) or datetime.min
        if new_ts >= old_ts:
            seen[key] = entry
        duplicates += 1
    return list(seen.values()), duplicates


def _backfill_inbox(entries: List[Dict[str, Any]]) -> int:
    changed = 0
    for entry in entries:
        now = datetime.utcnow().isoformat()
        if not entry.get("created_at"):
            entry["created_at"] = now
            changed += 1
        if not entry.get("updated_at"):
            entry["updated_at"] = entry.get("created_at")
            changed += 1
        if not entry.get("status"):
            entry["status"] = "new"
            changed += 1
        if not isinstance(entry.get("tags"), list):
            entry["tags"] = []
            changed += 1
    return changed


def _cleanup_entries(entries: List[Dict[str, Any]], *, status: str, retention_days: int) -> tuple[List[Dict[str, Any]], int]:
    cutoff = datetime.utcnow() - timedelta(days=max(1, retention_days))
    kept = []
    removed = 0
    for entry in entries:
        state = str(entry.get("status") or "").strip().lower()
        if state != status:
            kept.append(entry)
            continue
        ts = _parse_ts(entry.get("updated_at") or entry.get("created_at"))
        if ts is None or ts >= cutoff:
            kept.append(entry)
            continue
        removed += 1
    return kept, removed


def run_governance(data_dir: Path, dry_run: bool, archive_days: int, resolved_days: int) -> Dict[str, Any]:
    inbox_path = data_dir / "inbox_entries.json"
    proposal_path = data_dir / "improvement_proposals.json"

    inbox_entries = _read_json(inbox_path, [])
    proposals = _read_json(proposal_path, [])

    inbox_backfill = _backfill_inbox(inbox_entries)
    cleaned_inbox, removed_archived_inbox = _cleanup_entries(inbox_entries, status="archived", retention_days=archive_days)
    deduped_inbox, inbox_duplicates = _dedupe_inbox(cleaned_inbox)

    proposal_backfill = 0
    for proposal in proposals:
        now = datetime.utcnow().isoformat()
        if not proposal.get("created_at"):
            proposal["created_at"] = now
            proposal_backfill += 1
        if not proposal.get("updated_at"):
            proposal["updated_at"] = proposal.get("created_at")
            proposal_backfill += 1
        if not proposal.get("status"):
            proposal["status"] = "pending_review"
            proposal_backfill += 1

    resolved_status = {"accepted", "rejected", "implemented", "archived"}
    kept_proposals = []
    removed_resolved_proposals = 0
    cutoff = datetime.utcnow() - timedelta(days=max(1, resolved_days))
    for proposal in proposals:
        status = str(proposal.get("status") or "").strip().lower()
        if status not in resolved_status:
            kept_proposals.append(proposal)
            continue
        ts = _parse_ts(proposal.get("updated_at") or proposal.get("created_at"))
        if ts is None or ts >= cutoff:
            kept_proposals.append(proposal)
            continue
        removed_resolved_proposals += 1

    result = {
        "dry_run": bool(dry_run),
        "data_dir": str(data_dir),
        "inbox_backfill": inbox_backfill,
        "inbox_duplicates_removed": inbox_duplicates,
        "inbox_archived_removed": removed_archived_inbox,
        "proposal_backfill": proposal_backfill,
        "proposal_resolved_removed": removed_resolved_proposals,
        "inbox_total_after": len(deduped_inbox),
        "proposal_total_after": len(kept_proposals),
    }

    if not dry_run:
        _write_json(inbox_path, deduped_inbox)
        _write_json(proposal_path, kept_proposals)

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="思忆集本地数据治理：清理、去重、回填")
    parser.add_argument("--data-dir", default="", help="本地数据目录（默认 思忆集app/data）")
    parser.add_argument("--archive-days", type=int, default=45, help="收件箱归档保留天数")
    parser.add_argument("--resolved-days", type=int, default=30, help="提案终态保留天数")
    parser.add_argument("--dry-run", action="store_true", help="仅输出结果，不落盘")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    data_dir = Path(args.data_dir).expanduser().resolve() if args.data_dir else (repo_root / "思忆集app" / "data").resolve()

    if not data_dir.exists():
        print(f"[desktop-data-governance] missing data dir: {data_dir}")
        return 2

    result = run_governance(
        data_dir=data_dir,
        dry_run=bool(args.dry_run),
        archive_days=int(args.archive_days),
        resolved_days=int(args.resolved_days),
    )
    print("[desktop-data-governance] summary")
    for key, value in result.items():
        print(f"- {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
