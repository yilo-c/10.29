# -*- coding: utf-8 -*-
"""run_eval.py — D15 25 题评估骨架（M4a-4）。

四维评分口径（方案 §三 D15 终签）：正确性 / 有用性 / 时延 / 语气。
* 时延：逐题真实调用时机器记录（``perf_counter`` → ``latency_ms``）；
* 正确性/有用性/语气：留人工输入接口（``--grade qid:correct:useful:tone``）。

作答端 ``--model shell|deep``：
* ``shell``：经本机后端 WS ``chat.send``（壳内管家路径，需后端在 18765 运行）；
* ``deep`` ：经 ``services.agent_gateway`` 内核网关直接提交（settings 读生产，只读）。
缺省为骨架预览（不真调外部模型）；真调用必须显式 ``--live``。

存储：``eval_results`` 表（DDL 见 ``--ddl``）。**生产库 ``data/data/ai_usage.db``
建表 = 🔒 人工批准点**（本批出口断言以临时库演示跑通；生产写入需 ``--yes`）。
其余库读数一律 ``mode=ro``（沿用 route_report.py 只读连接模式）。

用法::

    python scripts/run_eval.py --list
    python scripts/run_eval.py --ddl
    python scripts/run_eval.py --init-db --db <tmp>/ai_usage.db
    python scripts/run_eval.py --model shell --live --db <tmp>/ai_usage.db   # 真跑（人工）
    python scripts/run_eval.py --grade f01:4:4:5 --db <tmp>/ai_usage.db
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from typing import Any

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUESTIONS_PATH = os.path.join(APP_ROOT, "scripts", "eval_questions.json")
PRODUCTION_DB = os.path.join(APP_ROOT, "data", "data", "ai_usage.db")
DEFAULT_TYPES = ("fact", "tool", "memory", "writing", "chat")

EVAL_DDL = """CREATE TABLE IF NOT EXISTS eval_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id TEXT NOT NULL,
    model TEXT NOT NULL,
    answer TEXT,
    score_correct REAL,
    score_useful REAL,
    latency_ms REAL,
    score_tone REAL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);"""


def load_questions() -> list[dict[str, Any]]:
    with open(QUESTIONS_PATH, encoding="utf-8") as f:
        qs = json.load(f)
    if not isinstance(qs, list):
        raise RuntimeError("eval_questions.json 必须是题目数组")
    return qs


def cmd_list(qs: list[dict[str, Any]]) -> int:
    for q in qs:
        print(f"{q['id']}\t{q['type']}\t{q['question']}")
    return 0


def _is_production(db: str) -> bool:
    return os.path.abspath(db) == os.path.abspath(PRODUCTION_DB)


def open_db(db: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.executescript(EVAL_DDL)
    conn.commit()


def cmd_ddl() -> int:
    print(EVAL_DDL)
    return 0


def cmd_init_db(db: str, yes: bool) -> int:
    if _is_production(db) and not yes:
        print("🔒 拒绝：生产库 data/data/ai_usage.db 建表 = 人工批准点（M4a-4 登记项）。")
        print("   批准后再执行：python scripts/run_eval.py --init-db --db data/data/ai_usage.db --yes")
        return 1
    conn = open_db(db)
    try:
        ensure_table(conn)
        present = conn.execute(
            "SELECT COUNT(1) FROM sqlite_master WHERE name='eval_results'"
        ).fetchone()[0]
        print(f"eval_results 就位：{db}（present={present}）")
        return 0
    finally:
        conn.close()


def answer_question(question: str, model: str, live: bool) -> tuple[str, float | None]:
    """返回 (answer_text, latency_ms)。骨架模式（live=False）不实调，时延为 None。"""
    if not live:
        return f"[骨架预览] 未真调用（--live 开启作答端：{model}）", None
    t0 = time.perf_counter()
    if model == "deep":
        answer = _answer_deep(question)
    elif model == "shell":
        answer = _answer_shell(question)
    else:
        raise RuntimeError(f"未知 model：{model}（shell|deep）")
    latency_ms = round((time.perf_counter() - t0) * 1000, 1)
    return answer, latency_ms


def _answer_deep(question: str) -> str:
    sys.path.insert(0, APP_ROOT)
    from services.agent_gateway import build_gateway_from_repository
    from data.local_storage import LocalDataStore

    class _Repo:
        def __init__(self) -> None:
            self.data_store = LocalDataStore(base_dir=os.path.join(APP_ROOT, "data", "data"))
            self._settings = self.data_store.load_json("settings", {}) or {}

        def get_setting(self, key: str, default: Any = "") -> Any:
            return self._settings.get(key, default)

    gw = build_gateway_from_repository(_Repo())
    parts: list[str] = []
    for ev in gw.submit_stream(question):
        kind = getattr(ev, "kind", "")
        payload = getattr(ev, "payload", {}) or {}
        if kind == "result":
            parts.append(str(payload.get("text") or payload.get("result") or ""))
        elif kind == "error":
            parts.append(f"[kernel-error] {payload.get('message', '')}")
    return "".join(parts).strip() or "[deep] 空响应"


def _answer_shell(question: str) -> str:
    import asyncio

    async def _ws() -> str:
        import aiohttp

        url = os.environ.get("SIYI_WS_URL", "ws://127.0.0.1:18765/ws")
        chunks: list[str] = []
        async with aiohttp.ClientSession() as sess:
            async with sess.ws_connect(url, timeout=aiohttp.ClientWSTimeout(ws_close=5.0)) as ws:
                await ws.send_json({
                    "jsonrpc": "2.0", "id": 1, "method": "chat.send",
                    "params": {"message": question}, "stream": True,
                })
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        continue
                    res = data.get("result") or {}
                    if res.get("type") == "chunk":
                        chunks.append(str(res.get("data", "")))
                    elif res.get("type") == "done":
                        break
        return "".join(chunks).strip() or "[shell] 空响应"

    return asyncio.run(_ws())


def cmd_run(model: str, live: bool, only: str, db: str | None, qs: list[dict[str, Any]]) -> int:
    targets = [q for q in qs if not only or q["id"] == only]
    if not targets:
        print(f"未找到题目 id：{only}")
        return 1
    conn = None
    if db:
        if _is_production(db):
            print("🔒 生产库写入需先人工批准 --init-db --yes；run 模式拒绝写生产。")
            return 1
        conn = open_db(db)
        ensure_table(conn)
    try:
        for q in targets:
            answer, latency_ms = answer_question(q["question"], model, live)
            print(f"{q['id']}\t{model}\tlatency_ms={latency_ms}\t{answer[:120]}")
            if conn is not None:
                conn.execute(
                    "INSERT INTO eval_results(question_id, model, answer, latency_ms) VALUES (?,?,?,?)",
                    (q["id"], model, answer, latency_ms),
                )
                conn.commit()
        return 0
    finally:
        if conn is not None:
            conn.close()


def cmd_grade(specs: list[str], db: str) -> int:
    """人工评分补录：qid:correct:useful:tone（1-5 分），回填最近一条作答行。"""
    if _is_production(db):
        print("🔒 生产库评分写入需人工批准（本批登记项），拒绝。")
        return 1
    conn = open_db(db)
    try:
        ensure_table(conn)
        for spec in specs:
            parts = spec.split(":")
            if len(parts) != 4:
                print(f"评分格式错（应为 qid:correct:useful:tone）：{spec}")
                return 1
            qid = parts[0]
            correct, useful, tone = (float(p) for p in parts[1:])
            cur = conn.execute(
                "UPDATE eval_results SET score_correct=?, score_useful=?, score_tone=?"
                " WHERE id=(SELECT id FROM eval_results WHERE question_id=? ORDER BY id DESC LIMIT 1)",
                (correct, useful, tone, qid),
            )
            print(f"{qid}: 回填 {cur.rowcount} 行")
        conn.commit()
        return 0
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="D15 25 题评估骨架（四维：正确/有用/时延/语气）")
    ap.add_argument("--list", action="store_true", help="打印题目录（一行一题）")
    ap.add_argument("--ddl", action="store_true", help="打印 eval_results 建表 DDL")
    ap.add_argument("--init-db", action="store_true", help="在 --db 建 eval_results 表")
    ap.add_argument("--model", choices=["shell", "deep"], help="作答端（配合 --run）")
    ap.add_argument("--run", action="store_true", help="逐题调用作答端")
    ap.add_argument("--live", action="store_true", help="真实外呼（缺省骨架预览不外呼）")
    ap.add_argument("--only", default="", help="只跑指定题 id")
    ap.add_argument("--grade", action="append", default=[], metavar="QID:C:U:T",
                    help="人工评分补录（可多次）：qid:正确性:有用性:语气")
    ap.add_argument("--db", default=None, help="eval_results 目标库（init-db/grade 缺省=生产路径；run 缺省=只打印不写库）")
    ap.add_argument("--yes", action="store_true", help="生产库建表人工批准旗标")
    args = ap.parse_args(argv)

    qs = load_questions()
    if args.list:
        return cmd_list(qs)
    if args.ddl:
        return cmd_ddl()
    if args.init_db:
        return cmd_init_db(args.db or PRODUCTION_DB, args.yes)
    if args.grade:
        return cmd_grade(args.grade, args.db or PRODUCTION_DB)
    if args.run:
        if not args.model:
            print("--run 需要 --model shell|deep")
            return 1
        return cmd_run(args.model, args.live, args.only, args.db, qs)
    ap.print_help()
    dist = {t: sum(1 for q in qs if q["type"] == t) for t in DEFAULT_TYPES}
    print(f"题库：{len(qs)} 题（类型分布：{dist}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
