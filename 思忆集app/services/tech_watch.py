"""TechWatch 技术瞭望 — 本地采集/摘要 + 云端裁判。

模块：
- TechWatchStore: SQLite 情报库
- TechWatchCollector: 每日采集 → 摘要 → 去重 → 存储
- TechWatchJudge: 云端 DeepSeek 评估 → 结果通知
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core.isolation import is_offline_mode

logger = logging.getLogger("siyi.tech_watch")

DB_FILENAME = "tech_watch.db"


# ── 数据类 ────────────────────────────────────────────────────


@dataclass
class TechInsight:
    """单条技术情报。"""

    id: int = 0
    topic: str = ""
    source_url: str = ""
    title: str = ""
    summary: str = ""
    key_points: str = ""
    collected_at: str = ""
    assessed: int = 0
    assessed_at: str = ""
    assessment_result: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "topic": self.topic,
            "source_url": self.source_url,
            "title": self.title,
            "summary": self.summary,
            "key_points": self.key_points,
            "collected_at": self.collected_at,
            "assessed": self.assessed,
            "assessed_at": self.assessed_at,
            "assessment_result": self.assessment_result,
        }


@dataclass
class TechAssessment:
    """一次云端评估记录。"""

    id: int = 0
    assessed_at: str = ""
    insight_count: int = 0
    summary: str = ""
    verdict: str = ""  # upgrade / wait / info
    direction: str = ""  # 方向建议
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "assessed_at": self.assessed_at,
            "insight_count": self.insight_count,
            "summary": self.summary,
            "verdict": self.verdict,
            "direction": self.direction,
            "detail": self.detail,
        }


# ── 情报库 ────────────────────────────────────────────────────


class TechWatchStore:
    """SQLite 情报库（线程安全，连接缓存）。"""

    def __init__(self, db_path: str = ""):
        self._db_path = db_path or DB_FILENAME
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                conn = sqlite3.connect(self._db_path, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                self._conn = conn
            return self._conn

    def close(self):
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    logger.debug("Non-critical error (see details)", exc_info=True)

                self._conn = None

    def _init_db(self):
        with self._lock:
            conn = self._get_conn()
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS tech_insights (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT NOT NULL DEFAULT '',
                    source_url TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    key_points TEXT NOT NULL DEFAULT '',
                    collected_at TEXT NOT NULL DEFAULT '',
                    assessed INTEGER NOT NULL DEFAULT 0,
                    assessed_at TEXT NOT NULL DEFAULT '',
                    assessment_result TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS tech_assessments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    assessed_at TEXT NOT NULL DEFAULT '',
                    insight_count INTEGER NOT NULL DEFAULT 0,
                    summary TEXT NOT NULL DEFAULT '',
                    verdict TEXT NOT NULL DEFAULT '',
                    direction TEXT NOT NULL DEFAULT '',
                    detail TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_insights_assessed
                    ON tech_insights(assessed);
                CREATE INDEX IF NOT EXISTS idx_insights_topic
                    ON tech_insights(topic);
                -- P3.4 性能优化：url_exists() 每次抓取都会查 source_url 去重，原无索引全表扫描。
                -- UNIQUE 还能防止并发采集重复入库。
                CREATE UNIQUE INDEX IF NOT EXISTS idx_insights_source_url
                    ON tech_insights(source_url);
                -- P3.4：get_insights_by_topic() 按 topic 过滤 + collected_at DESC 排序，
                -- 复合索引可直接覆盖 ORDER BY，避免回表排序。
                CREATE INDEX IF NOT EXISTS idx_insights_topic_collected
                    ON tech_insights(topic, collected_at DESC);
            """)
            conn.commit()

    # ── 情报 CRUD ──

    def add_insight(self, insight: TechInsight) -> int:
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                """INSERT INTO tech_insights
                   (topic, source_url, title, summary, key_points, collected_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (insight.topic, insight.source_url, insight.title, insight.summary, insight.key_points, insight.collected_at),
            )
            conn.commit()
            return cur.lastrowid or 0

    def url_exists(self, url: str) -> bool:
        with self._lock:
            row = (
                self._get_conn()
                .execute(
                    "SELECT 1 FROM tech_insights WHERE source_url = ? LIMIT 1",
                    (url,),
                )
                .fetchone()
            )
            return row is not None

    def get_unassessed(self, limit: int = 100) -> list[TechInsight]:
        with self._lock:
            rows = (
                self._get_conn()
                .execute(
                    "SELECT * FROM tech_insights WHERE assessed = 0 ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
                .fetchall()
            )
            return [self._row_to_insight(r) for r in rows]

    def get_insights_by_topic(self, topic: str, limit: int = 50) -> list[TechInsight]:
        with self._lock:
            rows = (
                self._get_conn()
                .execute(
                    "SELECT * FROM tech_insights WHERE topic = ? ORDER BY collected_at DESC LIMIT ?",
                    (topic, limit),
                )
                .fetchall()
            )
            return [self._row_to_insight(r) for r in rows]

    def mark_assessed(self, insight_ids: list[int], result: str = ""):
        if not insight_ids:
            return
        now = datetime.now().isoformat()
        placeholders = ",".join("?" for _ in insight_ids)
        with self._lock:
            self._get_conn().execute(
                f"UPDATE tech_insights SET assessed = 1, assessed_at = ?, assessment_result = ? WHERE id IN ({placeholders})",
                [now, result] + insight_ids,
            )
            self._get_conn().commit()

    def get_insight_count(self, assessed: int | None = None) -> int:
        with self._lock:
            conn = self._get_conn()
            if assessed is None:
                row = conn.execute("SELECT COUNT(*) AS c FROM tech_insights").fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) AS c FROM tech_insights WHERE assessed = ?", (assessed,)).fetchone()
            return row["c"] if row else 0

    def get_topics(self) -> list[str]:
        with self._lock:
            rows = self._get_conn().execute("SELECT DISTINCT topic FROM tech_insights ORDER BY topic").fetchall()
            return [r["topic"] for r in rows if r["topic"]]

    # ── 评估历史 CRUD ──

    def add_assessment(self, assessment: TechAssessment) -> int:
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                """INSERT INTO tech_assessments
                   (assessed_at, insight_count, summary, verdict, direction, detail)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (assessment.assessed_at, assessment.insight_count, assessment.summary, assessment.verdict, assessment.direction, assessment.detail),
            )
            conn.commit()
            return cur.lastrowid or 0

    def get_recent_assessments(self, limit: int = 10) -> list[TechAssessment]:
        with self._lock:
            rows = (
                self._get_conn()
                .execute(
                    "SELECT * FROM tech_assessments ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
                .fetchall()
            )
            return [self._row_to_assessment(r) for r in rows]

    def get_latest_verdict(self) -> str:
        with self._lock:
            row = self._get_conn().execute("SELECT verdict FROM tech_assessments ORDER BY id DESC LIMIT 1").fetchone()
            return row["verdict"] if row else ""

    @staticmethod
    def _row_to_insight(row: sqlite3.Row) -> TechInsight:
        return TechInsight(
            id=row["id"],
            topic=row["topic"],
            source_url=row["source_url"],
            title=row["title"],
            summary=row["summary"],
            key_points=row["key_points"],
            collected_at=row["collected_at"],
            assessed=row["assessed"],
            assessed_at=row["assessed_at"],
            assessment_result=row["assessment_result"],
        )

    @staticmethod
    def _row_to_assessment(row: sqlite3.Row) -> TechAssessment:
        return TechAssessment(
            id=row["id"],
            assessed_at=row["assessed_at"],
            insight_count=row["insight_count"],
            summary=row["summary"],
            verdict=row["verdict"],
            direction=row["direction"],
            detail=row["detail"],
        )


# ── 采集器 ────────────────────────────────────────────────────


class TechWatchCollector:
    """每日采集：搜索 → 抓取 → 摘要 → 去重 → 存入。"""

    def __init__(self, store: TechWatchStore, web_search_service=None, web_fetch_service=None):
        self._store = store
        self._search_svc = web_search_service
        self._fetch_svc = web_fetch_service

    def collect(self, topics: list[str], local_ai_client=None) -> dict[str, Any]:
        """执行一次采集流程。返回统计结果。

        N-A（2026-09-17）离线短路：SIYI_TEST_OFFLINE=1 时直接 skipped，
        不做任何真实搜索/抓取/LLM 摘要。既往该开关只约束 headless_main 的
        info 采集与 autonomy 循环，不约束 cron action —— 测试/隔离实例里
        tech_watch.collect 照样真跑（3 query 搜索 + 10 URL 抓取 + 逐条摘要），
        正是冷启动补跑风暴中霸占 cron 单 worker 队头的分钟级任务（J3 根因）。

        PD-ISO-b（2026-09-18）：判定收敛到 core.isolation 离线基元，不再自行
        解析环境变量（同一开关在多处各写一遍 = 下次新增通道必然再漏一处）。
        """
        if is_offline_mode():
            logger.info("tech_watch.collect skipped (offline mode)")
            return {"status": "skipped", "reason": "offline test mode"}
        if not topics:
            return {"status": "skipped", "reason": "no topics"}
        if self._search_svc is None:
            return {"status": "skipped", "reason": "no search service"}

        results: dict[str, Any] = {"status": "ok", "total": 0, "new": 0, "skipped": 0, "errors": 0, "by_topic": {}}

        for topic in topics:
            topic_result = self._collect_topic(topic, local_ai_client)
            results["total"] += topic_result.get("total", 0)
            results["new"] += topic_result.get("new", 0)
            results["skipped"] += topic_result.get("skipped", 0)
            results["errors"] += topic_result.get("errors", 0)
            results["by_topic"][topic] = topic_result

        return results

    def _collect_topic(self, topic: str, local_ai_client=None) -> dict[str, Any]:
        """采集单个关注主题。"""
        result: dict[str, Any] = {"total": 0, "new": 0, "skipped": 0, "errors": 0}

        queries = self._build_queries(topic)
        all_links: list[dict[str, str]] = []

        for query in queries[:3]:
            try:
                search_results = self._search_svc.search(query, num_results=5)
                for sr in search_results:
                    url = str(getattr(sr, "url", sr.get("url", "")))
                    title = str(getattr(sr, "title", sr.get("title", "")))
                    if url and not self._store.url_exists(url):
                        all_links.append({"url": url, "title": title})
                result["total"] += len(search_results)
            except Exception as exc:
                logger.warning("tech_watch search failed for '%s': %s", query, exc)
                result["errors"] += 1

        for link in all_links[:10]:
            try:
                if self._store.url_exists(link["url"]):
                    result["skipped"] += 1
                    continue
                content = self._fetch_content(link["url"])
                if not content:
                    result["skipped"] += 1
                    continue
                summary_data = self._summarize(content, link["title"], topic, local_ai_client)
                insight = TechInsight(
                    topic=topic,
                    source_url=link["url"],
                    title=summary_data.get("title", link["title"]),
                    summary=summary_data.get("summary", ""),
                    key_points=summary_data.get("key_points", ""),
                    collected_at=datetime.now().isoformat(),
                )
                self._store.add_insight(insight)
                result["new"] += 1
            except Exception as exc:
                logger.warning("tech_watch fetch/summarize failed for %s: %s", link.get("url", "?"), exc)
                result["errors"] += 1

        return result

    @staticmethod
    def _build_queries(topic: str) -> list[str]:
        base = topic.strip()
        return [base, f"{base} 最新进展", f"{base} 技术"]

    def _fetch_content(self, url: str) -> str:
        if self._fetch_svc is None:
            return ""
        try:
            page = self._fetch_svc.fetch_sync(url)
            if page is None:
                return ""
            text = str(getattr(page, "content", page.get("content", page.get("text", ""))))
            return text[:8000]
        except Exception as exc:
            logger.warning("fetch failed: %s", exc, exc_info=True)
            return ""

    def _summarize(self, content: str, title: str, topic: str, local_ai_client=None) -> dict[str, str]:
        """使用本地 AI 摘要。"""
        if local_ai_client is None or not content.strip():
            return {"title": title, "summary": content[:500], "key_points": ""}

        prompt = (
            f"你是一个技术情报分析助手。请阅读以下关于「{topic}」的文章，用中文输出：\n"
            f"1. 标题（精确反映核心内容，20 字以内）\n"
            f"2. 摘要（100-200 字，概括核心观点）\n"
            f"3. 关键点（3-5 条，每行一条）\n\n"
            f"原文标题：{title}\n原文内容：\n{content[:6000]}"
        )
        try:
            resp = local_ai_client.chat(prompt, max_tokens=1024)
            text = str(resp)
        except Exception:
            try:
                resp = local_ai_client.complete(prompt, max_tokens=1024)
                text = str(resp)
            except Exception as exc:
                logger.warning("local AI summarize failed: %s", exc)
                return {"title": title, "summary": content[:500], "key_points": ""}

        lines = text.strip().split("\n")
        summary_parts: list[str] = []
        key_points: list[str] = []
        in_key_points = False
        extracted_title = title

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("1.") or stripped.startswith("标题"):
                extracted_title = stripped.split(":", 1)[-1].strip().lstrip("1.").strip()
                in_key_points = False
            elif stripped.startswith("2.") or stripped.startswith("摘要"):
                in_key_points = False
                text_part = stripped.split(":", 1)[-1].strip().lstrip("2.").strip()
                if text_part:
                    summary_parts.append(text_part)
            elif stripped.startswith("3.") or stripped.startswith("关键"):
                in_key_points = True
            elif in_key_points:
                kp = stripped.lstrip("-*0123456789. ").strip()
                if kp:
                    key_points.append(kp)
            else:
                if not in_key_points:
                    summary_parts.append(stripped)

        return {
            "title": extracted_title,
            "summary": "\n".join(summary_parts) or content[:500],
            "key_points": "\n".join(key_points[:5]),
        }


# ── 裁判 ──────────────────────────────────────────────────────


_JUDGE_SYSTEM_PROMPT = """你是一个技术升级评估专家。你的任务是分析以下技术情报摘要，
判断是否有足够价值进行系统升级。

请输出 JSON 格式的评估结果：
{
  "verdict": "upgrade" | "wait" | "info",
  "summary": "整体评估摘要（50-100 字）",
  "direction": "方向建议（30-50 字）",
  "detail": "详细分析（100-200 字，列出关键论据）"
}

verdict 含义：
- upgrade: 有明显技术升级价值，建议执行升级
- wait: 有价值但不够成熟，建议等待更多情报
- info: 仅供参考，无需升级
"""


class TechWatchJudge:
    """云端评估：调用 DeepSeek 分析情报精华。"""

    def __init__(self, store: TechWatchStore):
        self._store = store

    def judge(self, deepseek_client=None, agent_gateway=None) -> dict[str, Any]:
        """执行一次评估。返回评估结果。

        M-cron 收敛：评估生成优先经 agent_gateway（成熟内核），DeepSeek 客户端兜底。
        """
        unassessed = self._store.get_unassessed(limit=50)
        if not unassessed:
            return {"status": "skipped", "reason": "no new insights"}

        context_lines = ["# 技术情报汇总\n"]
        for ins in unassessed:
            context_lines.append(f"## [{ins.topic}] {ins.title}")
            context_lines.append(f"来源: {ins.source_url}")
            context_lines.append(f"摘要: {ins.summary}")
            if ins.key_points:
                context_lines.append(f"要点:\n{ins.key_points}")
            context_lines.append("")

        context = "\n".join(context_lines)

        def _deepseek_fallback() -> str:
            if hasattr(deepseek_client, "chat"):
                return str(
                    deepseek_client.chat(
                        _JUDGE_SYSTEM_PROMPT + "\n\n" + context,
                        max_tokens=2048,
                        response_format="json",
                    )
                )
            return str(
                deepseek_client.complete(
                    _JUDGE_SYSTEM_PROMPT + "\n\n" + context,
                    max_tokens=2048,
                )
            )

        try:
            from services.kernel_ai import kernel_generate

            if agent_gateway is not None:
                text = kernel_generate(
                    agent_gateway,
                    context,
                    system=_JUDGE_SYSTEM_PROMPT,
                    timeout_s=240,
                    fallback=_deepseek_fallback if deepseek_client is not None else None,
                )
            elif deepseek_client is not None:
                text = _deepseek_fallback()
            else:
                return {"status": "skipped", "reason": "no kernel gateway and no DeepSeek client"}
            if not text.strip():
                return {"status": "error", "error": "empty generation (kernel and fallback)"}
            result = self._parse_response(text)
        except Exception as exc:
            logger.warning("DeepSeek judge failed: %s", exc)
            return {"status": "error", "error": str(exc)}

        now = datetime.now().isoformat()
        assessment = TechAssessment(
            assessed_at=now,
            insight_count=len(unassessed),
            summary=result.get("summary", ""),
            verdict=result.get("verdict", "info"),
            direction=result.get("direction", ""),
            detail=result.get("detail", ""),
        )
        self._store.add_assessment(assessment)
        self._store.mark_assessed(
            [ins.id for ins in unassessed],
            result=json.dumps(result, ensure_ascii=False),
        )

        return {
            "status": "ok",
            "verdict": assessment.verdict,
            "summary": assessment.summary,
            "direction": assessment.direction,
            "insight_count": assessment.insight_count,
        }

    @staticmethod
    def _parse_response(text: str) -> dict[str, str]:
        text = text.strip()
        if text.startswith("{"):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                logger.debug("Non-critical error (see details)", exc_info=True)

        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                logger.debug("Non-critical error (see details)", exc_info=True)

        return {"verdict": "info", "summary": "解析失败", "direction": "", "detail": text[:500]}
