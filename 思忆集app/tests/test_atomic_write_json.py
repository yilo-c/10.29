"""B5-修复7 回归：原子 JSON 写原语 + 7 仓储文件写入漏斗委托。

覆盖：
1. 原语写入内容正确、临时文件不留垃圾；
2. 序列化失败时既有文件不被撕裂；
3. Windows 偶发 PermissionError 经退避重试最终成功；
4. 多线程并发写同一路径不出现半截文件 / 残留 .tmp；
5. ``LocalDataStore._write_plain_json`` 与 ``ChatSessionRepository._save_json_file``
   都委托到原子原语（即 7 仓储的 JSON 落盘走同一原子路径）。
"""

from __future__ import annotations

import json
import os
import threading

import pytest

from data import local_storage
from data._atomic_json import atomic_write_json
from repositories._chat_session import LocalChatSessionRepository


def _read(path: str) -> object:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def test_writes_correct_content_and_cleans_tmp(tmp_path):
    target = tmp_path / "notes.json"
    payload = {"items": [1, 2, 3], "name": "测试"}
    atomic_write_json(str(target), payload)
    assert _read(str(target)) == payload
    # 不应残留任何 .tmp 文件
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == [], f"残留临时文件: {leftovers}"


def test_partial_write_does_not_corrupt_existing(tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({"a": 1}), encoding="utf-8")

    # 让 json.dump 抛错 → 原语必须保持既有文件不变且不留 tmp
    def _boom(*_args, **_kwargs):
        raise ValueError("serialize fail")

    monkeypatch.setattr("data._atomic_json.json.dump", _boom)
    with pytest.raises(ValueError):
        atomic_write_json(str(target), {"a": 2})

    assert _read(str(target)) == {"a": 1}
    assert list(tmp_path.glob("*.tmp")) == []


def test_retry_on_permissionerror_then_succeeds(tmp_path, monkeypatch):
    target = tmp_path / "words.json"
    calls = {"n": 0}

    real_replace = os.replace

    def _flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError("file locked by Defender")
        real_replace(src, dst)

    monkeypatch.setattr("data._atomic_json.os.replace", _flaky_replace)
    atomic_write_json(str(target), {"ok": True})
    assert _read(str(target)) == {"ok": True}
    assert calls["n"] == 3  # 前两次失败、第三次成功


def test_concurrent_writes_no_torn_file_or_tmp(tmp_path):
    target = tmp_path / "chat.json"
    n_threads = 8
    n_iters = 50

    def worker(tid: int):
        for i in range(n_iters):
            atomic_write_json(str(target), {"tid": tid, "i": i, "pad": "x" * 200})

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 最终文件必为某次完整写入（结构合法），且目录无 .tmp 残留
    data = _read(str(target))
    assert isinstance(data, dict) and "tid" in data and "i" in data
    assert list(tmp_path.glob("*.tmp")) == []


def test_local_datastore_mirror_uses_atomic_primitive(tmp_path, monkeypatch):
    seen = {}

    def _fake(path, data):
        seen["path"] = path
        seen["data"] = data

    monkeypatch.setattr("data.local_storage.atomic_write_json", _fake)
    local_storage.LocalDataStore._write_plain_json(str(tmp_path / "settings.json"), {"k": 1})
    assert seen == {"path": str(tmp_path / "settings.json"), "data": {"k": 1}}


def test_chat_session_save_delegates_to_atomic_primitive(tmp_path, monkeypatch):
    seen = {}

    def _fake(path, data):
        seen["path"] = path
        seen["data"] = data

    monkeypatch.setattr("repositories._chat_session.atomic_write_json", _fake)
    repo = LocalChatSessionRepository(object())  # _save_json_file 不依赖 data_store
    target = str(tmp_path / "session_1" / "latest.json")
    repo._save_json_file(target, [{"role": "user", "content": "hi"}])
    assert seen["path"] == target
    assert seen["data"] == [{"role": "user", "content": "hi"}]
