"""技术雷达：自动检测依赖更新和技术变化。

根据 complete-architecture-design.md 第 6 章设计：
- 每日扫描 GitHub Releases、PyPI、模型厂商公告等
- 发现新版本、安全补丁、breaking changes
- 结果汇入每日早报「技术动态」板块
- 只汇报不执行，用户确认后才升级
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any
from urllib.error import URLError

logger = logging.getLogger("siyi.evolution.tech_radar")

# 变更等级
LEVEL_KNOWLEDGE = 0   # 知识更新，低风险
LEVEL_CONFIG = 1      # 配置/依赖升级，中风险
LEVEL_FEATURE = 2     # 功能/代码变更，高风险


@dataclass
class RadarFinding:
    """雷达单次发现。"""
    name: str
    current_version: str = ""
    latest_version: str = ""
    url: str = ""
    description: str = ""
    level: int = LEVEL_CONFIG          # 变更等级
    has_breaking_changes: bool = False
    source: str = ""                   # github_release | pypi | model_provider | changelog
    category: str = ""                 # dependency | model | tool | security
    published_at: str = ""
    found_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "url": self.url,
            "description": self.description,
            "level": self.level,
            "has_breaking_changes": self.has_breaking_changes,
            "source": self.source,
            "category": self.category,
            "published_at": self.published_at,
            "found_at": self.found_at or datetime.now(timezone.utc).isoformat(),
        }


# 预定义的扫描目标
DEFAULT_SCAN_TARGETS: list[dict[str, Any]] = [
    # 依赖包
    {"name": "chromadb", "source": "pypi", "category": "dependency", "url": "https://pypi.org/project/chromadb/"},
    {"name": "fastapi", "source": "pypi", "category": "dependency", "url": "https://pypi.org/project/fastapi/"},
    {"name": "langchain", "source": "pypi", "category": "dependency", "url": "https://pypi.org/project/langchain/"},
    # 工具
    {"name": "Ollama", "source": "github_release", "category": "tool",
     "url": "https://github.com/ollama/ollama/releases"},
    # AI 模型
    {"name": "Qwen2.5", "source": "model_provider", "category": "model",
     "url": "https://github.com/QwenLM/Qwen2.5"},
]

# 扫描目标配置文件路径（相对于应用数据目录）
_SCAN_TARGETS_CONFIG_FILENAME = "scan_targets.json"


def load_scan_targets_from_config(config_path: str | None = None) -> list[dict[str, Any]] | None:
    """从 JSON 配置文件加载用户自定义扫描目标。

    配置文件格式：
    [
        {"name": "aiohttp", "source": "pypi", "category": "dependency", "enabled": true},
        {"name": "ollama", "source": "github_release", "url": "...", "enabled": false}
    ]

    Args:
        config_path: 配置文件路径。None 时使用默认路径（data/scan_targets.json）

    Returns:
        扫描目标列表，配置文件不存在时返回 None（调用方回退到 DEFAULT_SCAN_TARGETS）
    """
    import os

    if config_path is None:
        # 默认路径：思忆集app/data/scan_targets.json
        app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        config_path = os.path.join(app_root, "data", _SCAN_TARGETS_CONFIG_FILENAME)

    if not os.path.isfile(config_path):
        return None

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            targets = json.load(f)
        if not isinstance(targets, list):
            logger.warning("扫描目标配置文件格式错误（应为列表）: %s", config_path)
            return None
        # 过滤掉 disabled 的目标
        return [t for t in targets if t.get("enabled", True) and t.get("name")]
    except Exception as exc:
        logger.warning("加载扫描目标配置失败: %s", exc)
        return None


def extract_dependencies_from_pyproject(pyproject_path: str | None = None) -> list[dict[str, Any]]:
    """从 pyproject.toml 提取依赖列表，生成扫描目标。

    解析 [project.dependencies] 和 [project.optional-dependencies] 中的包名，
    生成 source="pypi" 的扫描目标。

    Args:
        pyproject_path: pyproject.toml 路径。None 时使用默认路径。

    Returns:
        扫描目标列表（仅包含包名，URL 由 TechRadar 内部构造）
    """
    import os
    import re

    if pyproject_path is None:
        app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pyproject_path = os.path.join(app_root, "pyproject.toml")

    if not os.path.isfile(pyproject_path):
        return []

    try:
        with open(pyproject_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as exc:
        logger.debug("读取 pyproject.toml 失败: %s", exc)
        return []

    # 简单解析：提取 dependencies 段中的包名
    # 格式: "package-name>=1.0" 或 "package-name" 或 'package-name[extra]>=1.0'
    targets: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    # 匹配 [project] 段下的 dependencies 和 optional-dependencies
    dep_pattern = re.compile(r'^[\'"]?([a-zA-Z0-9_-]+)(?:\[[^\]]*\])?[\'"]?\s*[><=!~]', re.MULTILINE)
    for match in dep_pattern.finditer(content):
        name = match.group(1).lower()
        if name in seen_names:
            continue
        seen_names.add(name)
        targets.append({
            "name": name,
            "source": "pypi",
            "category": "dependency",
            "url": f"https://pypi.org/project/{name}/",
        })

    return targets


class TechRadar:
    """技术雷达扫描。

    职责：
    1. 按预定义目标定时扫描（通常每日）
    2. 检测版本变更、安全补丁、新发布
    3. 返回发现列表供汇报
    """

    def __init__(
        self,
        scan_targets: list[dict[str, Any]] | None = None,
        *,
        auto_load_config: bool = True,
        auto_load_pyproject: bool = False,
    ):
        """初始化技术雷达。

        扫描目标加载优先级（从高到低）：
        1. 显式传入的 scan_targets 参数
        2. data/scan_targets.json 配置文件（auto_load_config=True 时）
        3. DEFAULT_SCAN_TARGETS 硬编码默认值

        当 auto_load_pyproject=True 时，额外从 pyproject.toml 提取依赖
        并合并到扫描目标中（去重，按 name 字段）。

        Args:
            scan_targets: 显式指定扫描目标，优先级最高
            auto_load_config: 是否从 data/scan_targets.json 加载用户配置
            auto_load_pyproject: 是否从 pyproject.toml 提取依赖
        """
        if scan_targets is not None:
            self._scan_targets = scan_targets
        elif auto_load_config:
            config_targets = load_scan_targets_from_config()
            self._scan_targets = config_targets if config_targets else DEFAULT_SCAN_TARGETS
        else:
            self._scan_targets = DEFAULT_SCAN_TARGETS

        # 可选：从 pyproject.toml 提取依赖并合并
        if auto_load_pyproject:
            pyproject_targets = extract_dependencies_from_pyproject()
            if pyproject_targets:
                existing_names = {t.get("name", "").lower() for t in self._scan_targets}
                for t in pyproject_targets:
                    if t["name"].lower() not in existing_names:
                        self._scan_targets.append(t)

        self._findings: list[RadarFinding] = []
        self._version_cache: dict[str, str] = {}  # name → last_seen_version
        self._last_scan: str | None = None

    def scan(self, target: str | None = None) -> list[RadarFinding]:
        """执行技术雷达扫描。

        Args:
            target: 可选，只扫描指定目标（按 name），不传则扫描全部。

        Returns:
            发现列表（新增变化才返回，无变化的跳过）。
        """
        now = datetime.now(timezone.utc).isoformat()
        new_findings: list[RadarFinding] = []
        targets = [t for t in self._scan_targets if target is None or t["name"] == target]

        for t in targets:
            try:
                finding = self._scan_target(t)
                if finding is not None:
                    self._findings.append(finding)
                    new_findings.append(finding)
            except Exception as exc:
                logger.warning("扫描目标 %s 失败", t["name"], exc_info=True)

        self._last_scan = now
        return new_findings

    def _scan_target(self, target: dict[str, Any]) -> RadarFinding | None:
        """扫描单个目标，返回发现或 None（无变化）。"""
        name = target["name"]
        source = target["source"]
        category = target["category"]
        url = target.get("url", "")

        latest = self._fetch_latest_version(name, source, url)
        if latest is None:
            return None

        current = self._version_cache.get(name, "")
        is_new = not current or latest != current
        if not is_new:
            return None

        # 更新缓存
        self._version_cache[name] = latest

        # 简单判断变更等级
        level = LEVEL_CONFIG
        has_breaking = False
        if "breaking" in latest.lower():
            has_breaking = True
            level = LEVEL_FEATURE
        if category == "dependency" and latest:
            # 主版本号变更 → 可能有 breaking changes
            if current and self._is_major_version_change(current, latest):
                has_breaking = True
                level = LEVEL_FEATURE

        return RadarFinding(
            name=name,
            current_version=current,
            latest_version=latest,
            url=url,
            description=f"发现 {name} 更新: {current} → {latest}",
            level=level,
            has_breaking_changes=has_breaking,
            source=source,
            category=category,
            found_at=datetime.now(timezone.utc).isoformat(),
        )

    def _fetch_latest_version(self, name: str, source: str, url: str = "") -> str | None:
        """获取指定目标的最新版本。

        优先通过 PyPI JSON API / GitHub API 获取真实版本号。
        失败时返回 None（而非模拟版本号），避免误报"发现新版本"。
        调用方应处理 None 返回值，在报告中标注"无法获取最新版本"。
        """
        try:
            if source == "pypi":
                resp = urllib.request.urlopen(
                    f"https://pypi.org/pypi/{name}/json", timeout=5
                )
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("info", {}).get("version")

            elif source == "github_release":
                # 从 URL 中提取 owner/repo
                m = re.search(r"github\.com/([^/]+)/([^/]+)", url or name)
                if m:
                    owner, repo = m.group(1), m.group(2)
                    api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
                    req = urllib.request.Request(
                        api_url,
                        headers={"Accept": "application/vnd.github.v3+json", "User-Agent": "siyiji/1.0"},
                    )
                    resp = urllib.request.urlopen(req, timeout=5)
                    data = json.loads(resp.read().decode("utf-8"))
                    return data.get("tag_name", "").lstrip("v")

        except Exception as exc:
            logger.debug("实时版本获取失败 (%s): %s", name, exc)

        # 失败时返回 None，不再返回模拟版本号（避免误报）
        return None

    def _is_major_version_change(self, old_ver: str, new_ver: str) -> bool:
        """判断是否为主版本号变更。"""
        def _major(v: str) -> int:
            digits = re.findall(r"\d+", v.lstrip("vV"))
            return int(digits[0]) if digits else 0
        return _major(new_ver) > _major(old_ver)

    def get_all_findings(self, since: str | None = None) -> list[RadarFinding]:
        """获取所有发现，可选按时间过滤。"""
        if since is None:
            return list(self._findings)
        return [f for f in self._findings if f.found_at >= since]

    def set_version(self, name: str, version: str) -> None:
        """手动设置基线版本。"""
        self._version_cache[name] = version

    def get_status_snapshot(self) -> dict[str, Any]:
        return {
            "targets_count": len(self._scan_targets),
            "last_scan": self._last_scan,
            "findings_count": len(self._findings),
            "latest_findings": [
                {"name": f.name, "current": f.current_version, "latest": f.latest_version,
                 "level": f.level, "has_breaking": f.has_breaking_changes}
                for f in self._findings[-10:]
            ],
        }
