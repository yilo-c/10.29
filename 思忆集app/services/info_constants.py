# -*- coding: utf-8 -*-
"""资讯采集相关常量与配置。

将板块、来源、关键词等配置从 InfoCollectorService 中剥离，
便于前端/后端共享和独立调整。
"""
from __future__ import annotations

import re
from typing import Any

#: 默认关注主题（供资讯采集兜底）
DEFAULT_TOPICS = ["AI", "前端开发", "效率工具", "开源项目"]

#: 各板块默认关键词
_DEFAULT_SECTION_KEYWORDS: dict[str, list[str]] = {
    "ai": [
        "大模型", "LLM", "Agent", "RAG", "多模态", "推理", "Transformer", "DeepSeek",
        "GPT", "Claude", "AI", "人工智能", "机器学习", "Diffusion", "模型", "OpenAI",
        "Anthropic", "Gemini", "文心", "通义", "Llama", "Mistral", "Qwen", "MoE",
        "RLHF", "对齐", "训练", "推理芯片", "ChatGPT", "Copilot", "Cursor",
        "Midjourney", "Sora", "Stable Diffusion", "神经网络", "token", "embedding",
    ],
    "dev": [
        "前端", "React", "TypeScript", "Rust", "Python", "Go", "架构", "数据库",
        "后端", "框架", "编译", "Web", "API", "开源", "编程", "代码", "开发",
        "Java", "C++", "Kubernetes", "Docker", "Linux", "AWS", "Next.js", "Vue",
        "Svelte", "Swift", "Kotlin", "微服务", "GraphQL", "WebAssembly", "WASI",
    ],
    "tool": [
        "GitHub", "开源", "CLI", "Chrome 插件", "VS Code", "效率工具", "AI 工具",
        "工具", "插件", "Product Hunt", "IDE", "编辑器", "终端", "库", "SDK",
        "DevOps", "CI/CD", "监控", "日志", "Neovim", "Vim",
    ],
    "internet": [
        "产品", "社区", "创业", "独立开发", "DevTools", "互联网", "平台", "创投",
        "融资", "App", "应用", "SaaS", "商业", "公司", "发布", "上线", "收购",
        "IPO", "增长", "用户",
    ],
    "science": [
        "Nature", "Science", "论文", "研究", "突破", "试验", "发现", "科学",
        "量子", "物理", "生物", "医学", "航天", "太空", "能源", "气候", "基因",
        "CRISPR", "核聚变", "脑机",
    ],
    "policy": [
        "国务院", "发改委", "政策", "法规", "监管", "立法", "工信部", "网信办",
        "数据安全", "合规", "禁令", "制裁", "审查", "反垄断", "隐私", "GDPR",
    ],
    "domestic": ["国内", "中国", "本土", "国产", "政务", "民生", "社会", "北京", "上海", "深圳", "杭州"],
    "international": ["国际", "全球", "海外", "美国", "欧洲", "日本", "硅谷", "欧盟", "东南亚"],
    "entertainment": [
        "游戏", "动漫", "影视", "音乐", "综艺", "明星", "电竞", "直播", "UP主",
        "番剧", "Steam", "原神", "王者", "LOL", "动画", "电影", "剧集", "二次元",
        "电竞", "Cosplay",
    ],
}

#: 英文来源
_ENGLISH_SOURCES = {
    "arstechnica", "devto", "github", "hackernews", "lobsters",
    "mittr", "producthunt", "smashing", "techcrunch", "theverge",
    # 2026-08-18 新增英文源
    "arxiv_ai", "simonwillison",
}

#: 实验性来源（默认不启用）
_EXPERIMENTAL_SOURCES = frozenset({
    "baidu_hot", "bilibili", "coolapk", "douban", "jike", "nga",
    "smzdm", "thepaper", "tieba", "toutiao", "weibo_hot", "xueqiu",
})

#: 板块元数据（名称 + emoji）
_SECTION_META: dict[str, dict[str, str]] = {
    "ai": {"name": "AI 与大模型", "emoji": "🤖"},
    "dev": {"name": "技术动态", "emoji": "💻"},
    "tool": {"name": "工具与开源", "emoji": "🔧"},
    "internet": {"name": "互联网与产品", "emoji": "🌐"},
    "science": {"name": "科技突破", "emoji": "🧪"},
    "policy": {"name": "政策与监管", "emoji": "📋"},
    "entertainment": {"name": "游戏与娱乐", "emoji": "🎮"},
    "domestic": {"name": "国内动态", "emoji": "🇨🇳"},
    "international": {"name": "国际动态", "emoji": "🌍"},
    "local": {"name": "同城", "emoji": "📍"},
    "general": {"name": "其他资讯", "emoji": "📎"},
}

#: 板块展示顺序
_SECTION_ORDER = [
    "ai", "dev", "tool", "internet", "science", "policy",
    "entertainment", "domestic", "international", "local", "general",
]

#: 各板块默认关联信息源（可被用户逐板块覆盖；来源为空的板块不限源）
_DEFAULT_SECTION_SOURCES: dict[str, list[str]] = {
    "ai": ["hackernews", "arxiv_ai", "simonwillison", "jiqizhixin", "qbitai", "36kr", "mittr"],
    "dev": ["devto", "github", "lobsters", "juejin", "oschina", "infoq", "ruanyifeng"],
    "tool": ["github", "producthunt", "smashing", "sspai"],
    "internet": ["techcrunch", "theverge", "36kr", "ifanr", "solidot"],
    "science": ["mittr", "arxiv_ai"],
    "policy": ["govpolicy", "statsgov", "moe", "36kr", "cnbeta", "solidot"],
    "entertainment": ["gcores", "chuapp"],
    "domestic": ["juejin", "oschina", "cnbeta", "ithome", "36kr"],
    "international": ["techcrunch", "theverge", "arstechnica"],
    "local": ["ynzs"],
    "general": [],
}

#: 来源展示名
_SOURCE_DISPLAY: dict[str, str] = {
    "hackernews": "HackerNews",
    "devto": "Dev.to",
    "github": "GitHub Trending",
    "lobsters": "Lobsters",
    "producthunt": "Product Hunt",
    "techcrunch": "TechCrunch",
    "arstechnica": "Ars Technica",
    "theverge": "The Verge",
    "smashing": "Smashing Magazine",
    "mittr": "MIT科技评论",
    "solidot": "Solidot",
    "oschina": "开源中国",
    "juejin": "掘金",
    "36kr": "36氪",
    "infoq": "InfoQ中文",
    "sspai": "少数派",
    "jiqizhixin": "机器之心",
    "ruanyifeng": "阮一峰周刊",
    "ifanr": "爱范儿",
    "cnbeta": "cnBeta",
    # 2026-08-18 新增源
    "qbitai": "量子位",
    "ithome": "IT之家",
    "gcores": "机核",
    "chuapp": "触乐",
    "arxiv_ai": "arXiv AI",
    "simonwillison": "Simon Willison",
    # 2026-09 新增源（16 号文档信源军火库）
    "govpolicy": "国务院文件",
    "statsgov": "国家统计局",
    "moe": "教育部",
    "ynzs": "云南招考院",
}

#: 来源主页 URL
_SOURCE_URLS: dict[str, str] = {
    "hackernews": "https://news.ycombinator.com",
    "devto": "https://dev.to",
    "github": "https://github.com/trending",
    "lobsters": "https://lobste.rs",
    "producthunt": "https://www.producthunt.com",
    "techcrunch": "https://techcrunch.com",
    "arstechnica": "https://arstechnica.com",
    "theverge": "https://www.theverge.com",
    "smashing": "https://www.smashingmagazine.com",
    "mittr": "https://www.technologyreview.com",
    "solidot": "https://www.solidot.org",
    "oschina": "https://www.oschina.net",
    "juejin": "https://juejin.cn",
    "36kr": "https://36kr.com",
    "infoq": "https://www.infoq.cn",
    "sspai": "https://sspai.com",
    "jiqizhixin": "https://www.jiqizhixin.com",
    "ruanyifeng": "https://www.ruanyifeng.com/blog",
    "ifanr": "https://www.ifanr.com",
    "cnbeta": "https://www.cnbeta.com.tw",
    # 2026-08-18 新增源
    "qbitai": "https://www.qbitai.com",
    "ithome": "https://www.ithome.com",
    "gcores": "https://www.gcores.com",
    "chuapp": "https://www.chuapp.com",
    "arxiv_ai": "https://arxiv.org/list/cs.AI/recent",
    "simonwillison": "https://simonwillison.net",
    # 2026-09 新增源（16 号文档信源军火库）
    "govpolicy": "https://www.gov.cn/zhengce/zhengceku/gwywj/home.htm",
    "statsgov": "https://www.stats.gov.cn/sj/zxfb/",
    "moe": "https://www.moe.gov.cn/jyb_xwfb/",
    "ynzs": "https://www.ynzs.cn",
}

#: CJK 字符检测（中文/日文假名/韩文）
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")

# ── 运行时可变状态（由 set_* 设置）──
_user_sections: list[dict[str, Any]] | None = None
_section_keywords: dict[str, list[str]] | None = None


def set_section_keywords(keywords: dict[str, list[str]] | None) -> None:
    """运行时覆盖板块关键词。

    为兼容旧前端，该函数只覆盖关键词；自定义板块元数据请通过 set_section_config。
    """
    global _section_keywords
    if keywords:
        merged: dict[str, list[str]] = {}
        for k, default_v in _DEFAULT_SECTION_KEYWORDS.items():
            merged[k] = list(keywords.get(k, default_v))
        for k, v in keywords.items():
            if k not in merged:
                merged[k] = list(v)
        _section_keywords = merged
    else:
        _section_keywords = None


def set_user_sections(user_sections: list[dict[str, Any]] | None) -> None:
    """运行时设置用户自定义板块配置。"""
    global _user_sections, _section_keywords
    _user_sections = list(user_sections) if user_sections else None
    # 板块配置（含关键词）是权威来源：一旦有板块配置被写回，
    # 立即让旧的 info_section_keywords 缓存让位，避免遮蔽板块编辑的关键词。
    _section_keywords = None


def _default_sections() -> list[dict[str, Any]]:
    """把默认板块转换成统一结构。"""
    sections: list[dict[str, Any]] = []
    for sid in _SECTION_ORDER:
        if sid == "general":
            continue
        meta = _SECTION_META.get(sid, {"name": sid, "emoji": "📎"})
        sections.append({
            "id": sid,
            "name": meta["name"],
            "emoji": meta["emoji"],
            "keywords": list(_DEFAULT_SECTION_KEYWORDS.get(sid, [])),
            "sources": list(_DEFAULT_SECTION_SOURCES.get(sid, [])),
            "enabled": True,
            "is_default": True,
        })
    return sections


def get_effective_sections(
    user_sections: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """合并默认板块与用户自定义板块，返回 effective 配置列表。

    用户可以用同 id 覆盖默认板块的 name/emoji/keywords/enabled。
    自定义板块（id 以 user_ 开头或不在默认中）追加到列表。
    返回列表保持默认顺序，自定义板块按用户给定顺序追加。
    """
    defaults = {s["id"]: s for s in _default_sections()}
    user_map: dict[str, dict[str, Any]] = {}
    for s in (user_sections or _user_sections or []):
        sid = s.get("id", "")
        if not sid:
            continue
        user_map[sid] = dict(s)

    result: list[dict[str, Any]] = []
    # 默认板块按顺序输出（应用用户覆盖）
    for sid in _SECTION_ORDER:
        if sid == "general":
            continue
        base = dict(defaults.get(sid, {"id": sid, "name": sid, "emoji": "📎", "keywords": [], "sources": [], "enabled": True, "is_default": True}))
        override = user_map.pop(sid, None)
        if override:
            base.update(override)
            base["is_default"] = True
        if base.get("enabled", True):
            result.append(base)
    # 追加用户自定义板块（不在默认/内置板块中的）
    for s in (user_sections or _user_sections or []):
        sid = s.get("id", "")
        if sid in defaults or not sid or sid in _SECTION_ORDER:
            continue
        if s.get("enabled", True):
            result.append(dict(s))
    return result


def get_all_sections(
    user_sections: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """返回所有板块（包括 disabled 和 general），供配置弹窗使用。

    与 get_effective_sections 的区别：
    - 不过滤 enabled
    - 包含 general 板块
    """
    defaults = {s["id"]: s for s in _default_sections()}
    user_map: dict[str, dict[str, Any]] = {}
    for s in (user_sections or _user_sections or []):
        sid = s.get("id", "")
        if not sid:
            continue
        user_map[sid] = dict(s)

    result: list[dict[str, Any]] = []
    for sid in _SECTION_ORDER:
        base = dict(defaults.get(sid, {"id": sid, "name": sid, "emoji": "📎", "keywords": [], "sources": [], "enabled": True, "is_default": True}))
        override = user_map.pop(sid, None)
        if override:
            if override.get("removed"):
                # 已被彻底删除（如 general）：不再出现在配置列表
                continue
            base.update(override)
            base["is_default"] = True
        result.append(base)
    # 追加用户自定义板块（不在默认/内置板块中的）
    for s in (user_sections or _user_sections or []):
        sid = s.get("id", "")
        if sid in defaults or not sid or sid in _SECTION_ORDER:
            continue
        result.append(dict(s))
    return result


def get_effective_section_order() -> list[str]:
    return [s["id"] for s in get_effective_sections()] + ["general"]


def get_effective_section_meta(category: str) -> dict[str, str]:
    for s in get_effective_sections():
        if s["id"] == category:
            return {"name": s.get("name", category), "emoji": s.get("emoji", "📎")}
    return {"name": category, "emoji": "📎"}


def get_effective_section_keywords() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for s in get_effective_sections():
        sid = s["id"]
        kws = s.get("keywords")
        result[sid] = list(kws) if isinstance(kws, list) else []
    return result


def get_section_keywords() -> dict[str, list[str]]:
    """返回 effective 关键词（默认 + 用户覆盖 + 用户自定义板块）。"""
    if _section_keywords is not None:
        return _section_keywords
    base = dict(_DEFAULT_SECTION_KEYWORDS)
    for s in (_user_sections or []):
        sid = s.get("id", "")
        if not sid:
            continue
        kws = s.get("keywords")
        if isinstance(kws, list):
            base[sid] = list(kws)
    return base


def has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def section_meta(category: str) -> dict[str, str]:
    return _SECTION_META.get(category, _SECTION_META["general"])


def all_source_names() -> list[str]:
    return [
        "hackernews", "devto", "lobsters", "github", "producthunt",
        "techcrunch", "arstechnica", "theverge", "smashing", "mittr",
        "solidot", "oschina", "juejin", "36kr", "infoq", "sspai",
        "jiqizhixin", "ruanyifeng", "ifanr", "cnbeta",
        # 2026-08-18 新增源
        "qbitai", "ithome", "gcores", "chuapp", "arxiv_ai", "simonwillison",
        # 2026-09 新增源（16 号文档信源军火库）
        "govpolicy", "statsgov", "moe", "ynzs",
    ]


def source_display(source: str) -> str:
    return _SOURCE_DISPLAY.get(source, source)


def source_url(source: str) -> str:
    return _SOURCE_URLS.get(source, "")


def get_active_source_names() -> list[str]:
    """返回启用板块所关联信息源的并集（用于抓取时限源）。

    仅当所有启用板块都未配置来源（sources 全空）时返回空列表，
    此时调用方应退回"不限源、抓全源"。
    """
    names: list[str] = []
    seen: set[str] = set()
    for s in get_effective_sections():
        for src in (s.get("sources") or []):
            key = str(src).strip()
            if key and key not in seen:
                seen.add(key)
                names.append(key)
    return names


__all__ = [
    "DEFAULT_TOPICS",
    "all_source_names",
    "get_effective_sections",
    "get_effective_section_order",
    "get_effective_section_meta",
    "get_effective_section_keywords",
    "get_section_keywords",
    "set_section_keywords",
    "set_user_sections",
    "get_active_source_names",
    "has_cjk",
    "section_meta",
    "source_display",
    "source_url",
]
