"""七维-性C1：embedding_service 模块级 torch 导入回归测试。

背景：embedding_service.py 模块级 import sentence_transformers→torch 实测
冷启动 30.9s+数百 MB（python -B 实测，2026-08-29）。当前生产路径尚无人
import 该模块（埋雷），任何新接线（如 RAG 源接入）都会引爆。

本测试断言：import embedding_service 本身不得把 sentence_transformers
装进 sys.modules（模块级导入回归 = 红）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]

_PROBE = (
    "import sys; sys.path.insert(0, r'{root}'); "
    "import knowledge.retrieval.embedding_service as m; "
    "assert 'sentence_transformers' not in sys.modules, "
    "    'module-level sentence_transformers import regressed'; "
    "assert m.sentence_transformers_available() is True; "
    "assert set(m.MODEL_REGISTRY) >= {{'bge-small-zh-v1.5', 'all-MiniLM-L6-v2'}}; "
    "svc = m.EmbeddingService(offline_mode=True); "
    "assert svc.embedding_dim == 512; "
    "print('OK')"
).format(root=str(APP_ROOT))


def test_import_embedding_service_does_not_load_sentence_transformers():
    """import embedding_service 不得触发 torch 栈加载（30.9s 埋雷回归门）。"""
    r = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(APP_ROOT),
    )
    assert r.returncode == 0, f"stdout={r.stdout}\nstderr={r.stderr[-2000:]}"
    assert "OK" in r.stdout


def test_availability_helper_is_cheap():
    """可用性探测走 find_spec（不执行模块体），毫秒级返回。"""
    import time

    sys.path.insert(0, str(APP_ROOT))
    import knowledge.retrieval.embedding_service as m

    t0 = time.perf_counter()
    ok = m.sentence_transformers_available()
    dt = time.perf_counter() - t0
    assert isinstance(ok, bool)
    assert dt < 0.5, f"可用性探测耗时 {dt:.2f}s，疑似执行了模块体"
