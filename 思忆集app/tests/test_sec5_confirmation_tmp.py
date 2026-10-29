"""SEC-5 回归：ConfirmationStore 落盘不得使用固定 .tmp 名（并发互踩 / 改名失败滞留）。

同时覆盖 transfer_history 双写方（backend.handlers._transfer 与
services.react_executor）的临时名唯一化止血：两处必须写进同一个
transfer_history.json 而不互相抹掉临时文件。
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.confirmation_store import ConfirmationStore  # noqa: E402


def test_save_uses_unique_tmp_name_and_leaves_no_leftovers(tmp_path, monkeypatch):
    store = ConfirmationStore(data_dir=str(tmp_path))
    seen: list[str] = []
    real_replace = os.replace

    def _spy(src, dst, *args, **kwargs):
        seen.append(os.path.basename(str(src)))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", _spy)
    store._save({"a": {"status": "pending"}})
    store._save({"a": {"status": "pending"}, "b": {"status": "approved"}})

    assert len(seen) == 2
    assert seen[0] != seen[1], f"临时文件名重复：{seen}"
    assert all(name != "confirmations.tmp" for name in seen), seen
    assert list(tmp_path.glob("*.tmp")) == []
    assert json.loads(store.path.read_text(encoding="utf-8"))["b"]["status"] == "approved"


def test_concurrent_create_and_resolve_keeps_every_entry(tmp_path):
    store = ConfirmationStore(data_dir=str(tmp_path))
    created: list[str] = []
    lock = threading.Lock()

    def _worker(index: int) -> None:
        cid = store.create(f"action.{index}", {"i": index}, source="test")
        with lock:
            created.append(cid)
        store.resolve(cid, True)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    data = json.loads(store.path.read_text(encoding="utf-8"))
    assert len(created) == 8
    assert set(created) <= set(data), f"并发写入丢条目：{set(created) - set(data)}"
    assert all(data[c]["status"] == "approved" for c in created)
    assert list(tmp_path.glob("*.tmp")) == []


def test_corrupt_file_still_quarantined_then_writable(tmp_path):
    store = ConfirmationStore(data_dir=str(tmp_path))
    store.path.write_text("{not json", encoding="utf-8")

    cid = store.create("act", {}, source="test")
    assert list(tmp_path.glob("confirmations.json.corrupt-*"))
    assert store.get(cid)["status"] == "pending"


@pytest.mark.parametrize(
    "relative",
    [
        "backend/handlers/_transfer.py",
        "services/react_executor.py",
        "services/confirmation_store.py",
    ],
)
def test_no_fixed_tmp_write_pattern_remains(relative):
    """止血点守卫：这些生产写路径不得再出现「固定 .tmp 名」写法。"""
    src = (APP_ROOT / relative).read_text(encoding="utf-8")
    offenders = [
        line.strip()
        for line in src.splitlines()
        if ".tmp" in line and not line.strip().startswith("#") and "getpid" not in line
    ]
    assert offenders == [], f"{relative} 仍存在固定临时名写入：{offenders}"
    assert 'with_suffix(".tmp")' not in src
