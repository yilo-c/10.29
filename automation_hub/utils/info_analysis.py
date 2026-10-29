from __future__ import annotations

import html
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable
from urllib.parse import urlparse


_CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)
_CNVD_RE = re.compile(r"\bCNVD-\d{4}-\d{4,7}\b", re.IGNORECASE)
_CNNVD_RE = re.compile(r"\bCNNVD-\d{4}-\d{4,7}\b", re.IGNORECASE)
_CWE_RE = re.compile(r"\bCWE-\d{1,5}\b", re.IGNORECASE)
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# 轻量 stopwords：只放高频无意义词，避免误杀中文关键词
_DEFAULT_STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "to",
    "of",
    "in",
    "on",
    "for",
    "with",
    "is",
    "are",
    "as",
    "by",
    "from",
    "at",
    "this",
    "that",
    "these",
    "those",
    "it",
    "its",
    "we",
    "you",
    "your",
    "他们",
    "我们",
    "你们",
    "关于",
    "以及",
    "但是",
    "因为",
    "所以",
    "一个",
    "一种",
    "如何",
    "什么",
    "为什么",
    "通过",
    "进行",
    "相关",
    "最新",
    "今日",
    "今天",
    "报告",
    "发布",
    "摘要",
    "全文",
    "转载",
    "作者",
    "来源",
    "评论",
    "阅读",
    "点击",
    "更多",
    "查看",
    "下载",
    "上线",
    "更新",
    "公告",
    "通知",
    "通告",
    # 常见 HTML/CSS 噪声
    "style",
    "text",
    "indent",
    "align",
    "nbsp",
    "img",
    "src",
    "href",
    "http",
    "https",
    "www",
    "com",
    "net",
    "org",
    "rss",
    "feed",
    "xml",
    "atom",
    "pdf",
    "html",
    "json",
    "new",
    "announce",
    "announcement",
    "abstract",
    "type",
    "internet",
    "newsletter",
    "blog",
    "post",
    "posts",
    "update",
    "release",
    "released",
    "issue",
    "issues",
    "has",
    "have",
    "how",
    "what",
    "why",
    "guide",
    "using",
    "use",
    "used",
    "team",
    "company",
    "companies",
    "model",
    "models",
    "language",
    "large",
    "explainable",
    "click",
    "original",
    "read",
    "全文",
    "点击查看原文",
    "arxiv",
    "preprint",
    "doi",
    "cc",
}

_NOISE_TERMS = {
    "kubernetes",
    "cncf",
    "ietf",
    "cloudflare",
    "arxiv",
    "abstract",
    "announce",
    "announcement",
    "newsletter",
}


# 就业/民生方向：关键词打分（轻量、可解释）
_EMPLOYMENT_WEIGHTS: list[tuple[str, int]] = [
    ("稳就业", 5),
    ("就业", 4),
    ("失业", 5),
    ("裁员", 5),
    ("招聘", 4),
    ("岗位", 4),
    ("求职", 4),
    ("用工", 4),
    ("农民工", 4),
    ("高校毕业生", 4),
    ("毕业生", 3),
    ("灵活就业", 4),
    ("就业补贴", 5),
    ("社保补贴", 5),
    ("补贴", 3),
    ("社保", 3),
    ("工资", 3),
    ("薪酬", 3),
    ("劳动", 3),
    ("劳动合同", 4),
    ("见习", 3),
    ("实习", 2),
    ("技能", 1),
    ("培训", 1),
    ("返岗", 3),
]


_EMPLOYMENT_CORE: set[str] = {
    # 严格核心：必须出现至少一个，避免“补贴/消费补贴”等泛民生误判为就业
    "稳就业",
    "就业",
    "失业",
    "裁员",
    "招聘",
    "岗位",
    "求职",
    "用工",
    "农民工",
    "高校毕业生",
    "毕业生",
    "灵活就业",
    "薪酬",
    "工资",
    "劳动",
    "劳动合同",
    "社保",
}


_POLICY_ACTIONS: list[tuple[str, list[str]]] = [
    ("征求意见", ["征求意见", "公开征求意见"]),
    ("发布通知", ["通知", "通告", "公告", "发布"]),
    ("实施落地", ["实施", "施行", "执行", "落地", "正式施行"]),
    ("申报/报名", ["申报", "报名", "申请", "填报", "申领"]),
    ("补贴/扶持", ["补贴", "补助", "扶持", "减免", "优惠", "奖补"]),
    ("招聘/招录", ["招聘", "招录", "招考", "校招", "社招", "岗位"]),
    ("培训/见习", ["培训", "见习", "实习", "技能提升", "职业技能"]),
]


_DEADLINE_RE = re.compile(
    r"(?:截至|截止|至)\s*(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{4}年\d{1,2}月\d{1,2}日|\d{1,2}月\d{1,2}日)"
)


def extract_policy_actions(text: str, max_actions: int = 3) -> list[str]:
    blob = _normalize_text(text)
    if not blob:
        return []
    out: list[str] = []
    for action, kws in _POLICY_ACTIONS:
        if any(k in blob for k in kws):
            out.append(action)
        if len(out) >= max_actions:
            break
    return out


def extract_deadline(text: str) -> str | None:
    blob = _normalize_text(text)
    if not blob:
        return None
    m = _DEADLINE_RE.search(blob)
    if not m:
        return None
    return (m.group(1) or "").strip() or None


def score_employment_item(it: dict) -> dict:
    """对单条资讯做“就业相关度”打分，并给出可解释命中与政策动作。"""
    title = _normalize_text(it.get("title") or "")
    summary = _normalize_text(it.get("summary") or "")
    blob = f"{title}\n{summary}"

    score = 0
    hits: list[str] = []
    for kw, w in _EMPLOYMENT_WEIGHTS:
        if kw and kw in blob:
            score += w
            hits.append(kw)

    core_hit = any(h in _EMPLOYMENT_CORE for h in hits)

    # 如果包含明显政策语义，稍微加权，鼓励“可执行”信息
    if any(x in blob for x in ["政策", "通知", "征求意见", "实施", "申报", "补贴"]):
        score += 1

    actions = extract_policy_actions(blob)
    deadline = extract_deadline(blob)

    return {
        "title": title,
        "link": it.get("link"),
        "source": it.get("source_name") or it.get("source"),
        "score": score,
        "hits": hits[:8],
        "core_hit": core_hit,
        "actions": actions,
        "deadline": deadline,
    }


def score_employment_items(items: list[dict], top_n: int = 10, min_score: int = 2) -> list[dict]:
    scored = [score_employment_item(it) for it in (items or [])]
    scored = [
        x
        for x in scored
        if int(x.get("score") or 0) >= min_score and bool(x.get("core_hit"))
    ]
    scored.sort(key=lambda x: (-int(x.get("score") or 0), (x.get("title") or "")))
    return scored[:top_n]


@dataclass(frozen=True)
class AnalysisConfig:
    top_terms: int = 15
    trend_terms: int = 8
    max_risk_items: int = 10
    stopwords: frozenset[str] = frozenset(_DEFAULT_STOPWORDS)


def _normalize_text(s: str) -> str:
    return (s or "").strip()


def strip_html(text: str) -> str:
    t = _normalize_text(text)
    if not t:
        return ""

    t = html.unescape(t)
    # 去掉 script/style 块
    t = re.sub(r"<\s*(script|style)[^>]*>.*?<\s*/\s*\1\s*>", " ", t, flags=re.I | re.S)
    # 去掉所有标签
    t = re.sub(r"<[^>]+>", " ", t)
    # 压缩空白
    t = re.sub(r"\s+", " ", t).strip()
    return t


def tokenize(text: str) -> list[str]:
    """中英文轻量分词：

    - 英文/数字：按连续字母数字下划线
    - 中文：按连续 CJK 字符段

    不引入外部依赖（jieba 等），保证部署简单。
    """
    t = strip_html(text)
    if not t:
        return []

    # 过滤掉常见 CSS 量词/单位、颜色等噪声
    t = re.sub(r"\b\d+(?:\.\d+)?(px|em|rem|%)\b", " ", t, flags=re.I)
    t = re.sub(r"#[0-9a-f]{3,8}\b", " ", t, flags=re.I)

    parts = re.findall(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", t)
    out: list[str] = []
    for p in parts:
        tok = p.lower()
        # 过滤纯数字
        if tok.isdigit():
            continue
        # 英文/数字 token：至少 3 位，避免 al/it/of 等噪声
        if tok.isascii() and re.fullmatch(r"[a-z0-9_]+", tok):
            if len(tok) < 3:
                continue
        if tok in _NOISE_TERMS:
            continue
        out.append(tok)
    return out


def extract_domain(url: str) -> str | None:
    u = _normalize_text(url)
    if not u:
        return None
    try:
        host = urlparse(u).netloc
        host = host.split(":")[0].lower()
        return host or None
    except Exception:
        return None


def compute_top_terms(texts: Iterable[str], cfg: AnalysisConfig) -> list[dict]:
    counter: Counter[str] = Counter()
    for text in texts:
        for tok in tokenize(text):
            if tok in cfg.stopwords:
                continue
            if len(tok) < 2:
                continue
            counter[tok] += 1

    items = [{"term": t, "count": c} for t, c in counter.most_common(cfg.top_terms * 2)]
    items = [x for x in items if x["count"] >= 2]
    return items[: cfg.top_terms]


def trend_terms(today_texts: Iterable[str], prev_texts: Iterable[str], cfg: AnalysisConfig) -> list[dict]:
    def to_counter(texts: Iterable[str]) -> Counter[str]:
        c: Counter[str] = Counter()
        for text in texts:
            for tok in tokenize(text):
                if tok in cfg.stopwords:
                    continue
                if len(tok) < 2:
                    continue
                c[tok] += 1
        return c

    today = to_counter(today_texts)
    prev = to_counter(prev_texts)

    # 简单趋势：delta = today - prev
    deltas = []
    for term, c in today.items():
        delta = c - prev.get(term, 0)
        if delta <= 0:
            continue
        if c < 2 or delta < 2:
            continue
        deltas.append((term, c, delta))

    deltas.sort(key=lambda x: (-x[2], -x[1], x[0]))
    return [
        {"term": t, "count": c, "delta": d}
        for t, c, d in deltas[: cfg.trend_terms]
    ]


def trend_terms_baseline(today_texts: Iterable[str], baseline_texts: Iterable[str], cfg: AnalysisConfig) -> list[dict]:
    """基于近 N 日基线的趋势词，减少偶然波动。"""
    return trend_terms(today_texts, baseline_texts, cfg)


def detect_risk_signals(items: list[dict], cfg: AnalysisConfig) -> dict:
    """从标题/摘要中抓取风险信号（CVE/漏洞/攻击/泄露等）。"""
    keywords = [
        "漏洞",
        "cve",
        "cnvd",
        "cnnvd",
        "cwe",
        "0day",
        "0-day",
        "rce",
        "远程代码执行",
        "供应链",
        "攻击",
        "入侵",
        "勒索",
        "泄露",
        "数据泄露",
        "后门",
        "木马",
        "钓鱼",
        "影响版本",
        "poc",
        "在野",
        "被利用",
    ]

    flagged = []
    cve_hits: Counter[str] = Counter()
    cnvd_hits: Counter[str] = Counter()
    cnnvd_hits: Counter[str] = Counter()
    cwe_hits: Counter[str] = Counter()
    for it in items:
        title = _normalize_text(it.get("title") or "")
        summary = _normalize_text(it.get("summary") or "")
        blob = f"{title}\n{summary}".lower()
        tokset = set(tokenize(blob))

        def hit(k: str) -> bool:
            kk = (k or "").lower()
            if not kk:
                return False
            # 短英文/数字关键词容易误触发（如 reinforcement 含 rce），用 token 边界匹配
            if kk.isascii() and kk in {"cve", "cnvd", "cnnvd", "cwe", "rce", "poc"}:
                return kk in tokset
            # 0-day / 0day 用原始包含判断
            if kk in {"0day", "0-day"}:
                return kk in blob
            # 中文/长词：允许包含
            return kk in blob

        matched = [k for k in keywords if hit(k)]
        cves = [m.upper() for m in _CVE_RE.findall(blob)]
        cnvds = [m.upper() for m in _CNVD_RE.findall(blob)]
        cnnvds = [m.upper() for m in _CNNVD_RE.findall(blob)]
        cwes = [m.upper() for m in _CWE_RE.findall(blob)]
        for c in cves:
            cve_hits[c] += 1
        for c in cnvds:
            cnvd_hits[c] += 1
        for c in cnnvds:
            cnnvd_hits[c] += 1
        for c in cwes:
            cwe_hits[c] += 1

        ips = _IP_RE.findall(blob)

        if matched or cves or cnvds or cnnvds or cwes:
            flagged.append(
                {
                    "title": title,
                    "link": it.get("link"),
                    "source": it.get("source_name"),
                    "matched": matched,
                    "cves": cves,
                    "cnvds": cnvds,
                    "cnnvds": cnnvds,
                    "cwes": cwes,
                    "ips": ips[:5],
                }
            )

    return {
        "flagged_count": len(flagged),
        "top_cves": [{"cve": c, "count": n} for c, n in cve_hits.most_common(10)],
        "top_cnvd": [{"cnvd": c, "count": n} for c, n in cnvd_hits.most_common(10)],
        "top_cnnvd": [{"cnnvd": c, "count": n} for c, n in cnnvd_hits.most_common(10)],
        "top_cwe": [{"cwe": c, "count": n} for c, n in cwe_hits.most_common(10)],
        "items": flagged[: cfg.max_risk_items],
    }


def parse_date_yyyy_mm_dd(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d")


def date_range(today: str, days_back: int) -> tuple[str, str]:
    d = parse_date_yyyy_mm_dd(today)
    prev = d - timedelta(days=days_back)
    return prev.strftime("%Y-%m-%d"), today


def classify_items(items: list[dict]) -> dict:
    """规则化分栏：风险/运维/政策/行业/通用。"""
    buckets = {
        "risk": [],
        "ops": [],
        "employment": [],
        "policy": [],
        "industry": [],
        "general": [],
    }

    employment_kw = [
        "就业",
        "稳就业",
        "招聘",
        "岗位",
        "求职",
        "失业",
        "裁员",
        "人才",
        "薪酬",
        "工资",
        "劳动",
        "劳务",
        "社保",
        "社保缴费",
        "社保补贴",
        "见习",
        "实习",
        "创业",
        "补贴",
        "返岗",
        "用工",
    ]
    policy_kw = [
        "政策",
        "法规",
        "条例",
        "通知",
        "通告",
        "公告",
        "征求意见",
        "意见",
        "办法",
        "方案",
        "印发",
        "举措",
        "若干措施",
        "国务院",
        "国办",
        "人民政府",
        "发改委",
        "工信部",
        "网信办",
        "财政部",
        "公安部",
        "教育部",
    ]
    # 仅靠“公告/意见/办法/方案”容易把技术文章误判为政策：需要额外政策语境兜底
    weak_policy_only = ["公告", "意见", "办法", "方案"]
    policy_context_kw = [
        "国务院",
        "国办",
        "人民政府",
        "政府",
        "办公厅",
        "发改委",
        "工信部",
        "网信办",
        "财政部",
        "公安部",
        "教育部",
        "印发",
        "通知",
        "通告",
        "征求意见",
        "条例",
        "法规",
        "规定",
        "若干措施",
        "实施",
    ]
    # 技术噪声：仅保留“明显教程/解决方案”信号，避免误伤政策文本中的“决策部署”等常用表述
    tech_noise_kw = [
        "解决方案",
        "教程",
        "最佳实践",
        "入门",
        "howto",
        "手把手",
        "排查",
        "配置",
    ]
    industry_kw = ["行业", "产业", "市场", "公司", "企业", "财报", "融资", "并购", "上市", "竞争", "合作"]
    ops_kw = ["运维", "故障", "宕机", "SLA", "停机", "维护", "升级", "发布", "变更", "中断", "告警"]
    risk_kw = ["漏洞", "cve", "cnvd", "cnnvd", "cwe", "攻击", "入侵", "勒索", "后门", "木马", "泄露", "在野", "poc", "rce"]

    for it in items:
        title = _normalize_text(it.get("title") or "")
        summary = _normalize_text(it.get("summary") or "")
        blob = f"{title}\n{summary}".lower()

        def hit(kws: list[str]) -> bool:
            return any(k in blob for k in kws)

        if hit(risk_kw):
            buckets["risk"].append(it)
        elif hit(ops_kw):
            buckets["ops"].append(it)
        elif hit(employment_kw):
            buckets["employment"].append(it)
        elif hit(policy_kw):
            # 若仅命中弱词且缺乏政策语境，通常不是“政策类”而是技术/产品/商业内容
            only_weak = hit(weak_policy_only) and not any(k in blob for k in policy_context_kw)
            tech_noise = any(k in blob for k in tech_noise_kw)
            if only_weak or tech_noise:
                if hit(industry_kw):
                    buckets["industry"].append(it)
                else:
                    buckets["general"].append(it)
            else:
                buckets["policy"].append(it)
        elif hit(industry_kw):
            buckets["industry"].append(it)
        else:
            buckets["general"].append(it)

    return buckets


def classify_policy_level(items: list[dict]) -> dict:
    """将政策类条目按“国家/部委/地方/行业协会”归一分桶。"""
    levels = {
        "national": [],
        "ministry": [],
        "local": [],
        "association": [],
        "other": [],
    }

    national_kw = ["国务院", "全国", "国家", "中央", "全国人大", "国办", "中共中央", "中央办公厅", "国务院办公厅"]
    ministry_kw = [
        "工信部",
        "发改委",
        "网信办",
        "财政部",
        "公安部",
        "市场监管总局",
        "住建部",
        "生态环境部",
        "交通运输部",
        "国家能源局",
        "人民银行",
        "银保监",
        "证监会",
        "教育部",
        "科技部",
        "商务部",
        "农业农村部",
        "卫健委",
        "应急管理部",
        "药监局",
        "知识产权局",
    ]
    local_kw = ["省", "市", "自治区", "直辖市", "区政府", "市政府", "省政府", "人民政府", "厅", "局", "委", "办"]
    association_kw = ["协会", "学会", "商会", "联盟", "基金会", "研究会", "促进会"]

    for it in items:
        title = _normalize_text(it.get("title") or "")
        summary = _normalize_text(it.get("summary") or "")
        blob = f"{title}\n{summary}"

        def hit(kws: list[str]) -> bool:
            return any(k in blob for k in kws)

        if hit(national_kw):
            levels["national"].append(it)
        elif hit(ministry_kw):
            levels["ministry"].append(it)
        elif hit(association_kw):
            levels["association"].append(it)
        elif hit(local_kw):
            levels["local"].append(it)
        else:
            levels["other"].append(it)

    return levels


def summarize_policy_agencies(items: list[dict], top_n: int = 10) -> list[dict]:
    """统计政策条目中的机构/部门出现频次，输出 TopN。"""
    agencies = [
        "工信部",
        "发改委",
        "网信办",
        "财政部",
        "公安部",
        "市场监管总局",
        "住建部",
        "生态环境部",
        "交通运输部",
        "国家能源局",
        "人民银行",
        "银保监",
        "证监会",
        "教育部",
        "科技部",
        "商务部",
        "农业农村部",
        "卫健委",
        "应急管理部",
        "药监局",
        "知识产权局",
        "国务院",
        "全国人大",
        "中共中央",
    ]

    counts: Counter[str] = Counter()
    for it in items:
        title = _normalize_text(it.get("title") or "")
        summary = _normalize_text(it.get("summary") or "")
        blob = f"{title}\n{summary}"
        for a in agencies:
            if a in blob:
                counts[a] += 1

    return [{"agency": a, "count": c} for a, c in counts.most_common(top_n)]


def classify_industry_track(items: list[dict]) -> dict:
    """行业赛道归类：云/AI/安全/能源/制造/金融/电信/医疗/教育/政务。"""
    tracks = {
        "cloud": [],
        "ai": [],
        "security": [],
        "energy": [],
        "manufacturing": [],
        "finance": [],
        "telecom": [],
        "healthcare": [],
        "education": [],
        "gov": [],
        "other": [],
    }

    kw = {
        "cloud": ["云", "云计算", "云原生", "云服务", "多云", "混合云", "边缘云"],
        "ai": ["人工智能", "ai", "大模型", "llm", "智能", "算法", "机器学习", "深度学习"],
        "security": ["安全", "网络安全", "攻防", "漏洞", "cve", "cnvd", "cnnvd", "cwe", "零信任"],
        "energy": ["能源", "电力", "电网", "储能", "风电", "光伏", "新能源", "油气"],
        "manufacturing": ["制造", "工业", "工厂", "工业互联网", "工业软件", "装备", "智能制造"],
        "finance": ["金融", "银行", "保险", "证券", "基金", "支付", "交易所"],
        "telecom": ["通信", "电信", "运营商", "5g", "6g", "基站", "宽带"],
        "healthcare": ["医疗", "医药", "医保", "医院", "健康", "药品", "医疗器械"],
        "education": ["教育", "学校", "高校", "培训", "教学", "课程"],
        "gov": ["政务", "政府", "公共服务", "政务云", "智慧城市"],
    }

    for it in items:
        title = _normalize_text(it.get("title") or "")
        summary = _normalize_text(it.get("summary") or "")
        blob = f"{title}\n{summary}".lower()

        matched = []
        for k, kws in kw.items():
            if any(w in blob for w in kws):
                matched.append(k)

        if not matched:
            tracks["other"].append(it)
            continue

        for k in matched:
            tracks[k].append(it)

    return tracks
