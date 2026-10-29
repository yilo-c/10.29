"""单实例锁按数据目录派生（并行测试矩阵前提）。

验证：
- 同一数据目录：二次 acquire 冲突（单实例语义保持）；
- 不同数据目录：各自 acquire 成功（并行隔离后端可行）；
- 未设 SIYI_DATA_DIR：回落 default 锁名。
仅 Windows（msvcrt）有意义，其他平台 skip。
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import instance_lock  # noqa: E402

pytestmark = pytest.mark.skipif(not instance_lock.HAS_MSVCRT, reason="msvcrt 仅 Windows")


def _release(fd):
    instance_lock.release(fd)


def test_same_data_dir_conflicts(tmp_path, monkeypatch):
    d = str(tmp_path / "data")
    monkeypatch.setenv("SIYI_DATA_DIR", d)
    # 关键：同进程二次 acquire 会走僵死接管——持有者就是自己（PID 存活+端口
    # 不可达），takeover 开着会 terminate 自杀。测试关闭 takeover，只验「被拒」。
    monkeypatch.setenv("SIYI_LOCK_TAKEOVER", "0")
    fd1 = instance_lock.acquire()
    assert fd1 is not None
    try:
        assert instance_lock.acquire() is None  # 同目录第二实例被拒
    finally:
        _release(fd1)


def test_different_data_dirs_parallel(tmp_path, monkeypatch):
    d1 = str(tmp_path / "data_a")
    d2 = str(tmp_path / "data_b")
    monkeypatch.setenv("SIYI_DATA_DIR", d1)
    fd1 = instance_lock.acquire()
    assert fd1 is not None
    try:
        monkeypatch.setenv("SIYI_DATA_DIR", d2)
        fd2 = instance_lock.acquire()
        assert fd2 is not None  # 不同目录互不阻塞
        _release(fd2)
    finally:
        _release(fd1)


def test_default_lock_name_fallback(monkeypatch):
    monkeypatch.delenv("SIYI_DATA_DIR", raising=False)
    assert instance_lock.lock_path().endswith("siyi_headless.lock")


def test_lock_path_for_data_dir_matches_env_derivation(tmp_path):
    d = str(tmp_path / "x")
    monkeypatch_local = os.environ
    old = monkeypatch_local.get("SIYI_DATA_DIR")
    try:
        os.environ["SIYI_DATA_DIR"] = d
        via_env = instance_lock.lock_path()
    finally:
        if old is None:
            os.environ.pop("SIYI_DATA_DIR", None)
        else:
            os.environ["SIYI_DATA_DIR"] = old
    assert instance_lock.lock_path_for_data_dir(d) == via_env
