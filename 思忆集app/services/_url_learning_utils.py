"""Utility functions and constants for URL learning service.

Extracted from url_learning_service.py to reduce its size from 1678 lines.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable
from urllib.parse import urlparse


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

STOP_WORDS = {
    "the", "and", "that", "this", "with", "from", "have", "will", "about", "into",
    "your", "http", "https", "www", "com", "html", "page", "video", "article",
    "for", "are", "but", "not", "you", "all", "can", "had", "her", "was", "one",
    "一个", "一些", "一下", "一种", "一样", "我们", "你们", "他们", "她们", "它们",
    "这个", "那个", "这些", "那些", "这里", "那里", "这样", "那样", "自己", "大家",
    "以及", "如果", "进行", "可以", "需要", "就是", "因为", "已经", "没有", "不是",
    "更多", "内容", "页面", "视频", "链接", "网址", "学习", "总结",
    "什么", "怎么", "为什么", "哪些", "哪个", "其实", "其中", "之后", "之前", "之间",
    "而且", "但是", "所以", "虽然", "然而", "不过", "或者", "还是", "还有",
    "应该", "可能", "一定", "必须", "当然", "肯定", "大概", "差不多",
    "这种", "那种", "各种", "所有", "一切", "任何", "每个", "很多", "非常",
    "比较", "特别", "相当", "十分", "尤其", "基本", "基本上", "一般",
    "就是说", "也就是", "也就是说", "简单来说",
    "然后", "那么", "所以说", "对不对", "对吧", "嗯", "啊", "哈", "呢", "吧",
    "呀", "哦", "嘛", "额", "唉", "哎", "是吧", "知道吗", "明白吗",
    "反正", "总之", "怎么说", "说实话", "老实说",
    "点击", "查看", "详情", "展开", "收起", "返回", "首页", "登录", "注册",
    "评论", "回复", "分享", "转发", "下载", "上传", "播放", "暂停",
}

_PACKAGING_LINE_PREFIXES = re.compile(
    r"^(字幕文本[：:]|补充重点[：:]|视频标签[：:]|UP主[：:]|播放量[：:]"
    r"|简介[：:]|标题[：:]|转录文本[：:]|页面附加文本[：:])",
)

_PACKAGING_FULL_STRIP_PREFIXES = re.compile(
    r"^(视频标签[：:]|UP主[：:]|播放量[：:]|补充重点[：:])",
)

_DOMAIN_CORRECTIONS: dict[str, str] = {
    "落云星土": "落陨星兔", "落云星图": "落陨星兔", "落雨星瀑": "落陨星兔",
    "黑兔子": "落陨星兔", "不枯枯": "不枯骨", "不咕咕": "不枯骨",
    "部枯骨": "不枯骨", "布枯菇": "不枯骨",
    "曾毅": "增益", "曾益": "增益",
    "卖场": "赖场",
    "顺丰": "顺风",
    "化进": "化劲", "化烬": "化劲", "化境": "化劲",
    "下推": "吓退", "下退": "吓退",
    "星练": "星链",
    "多被打击": "多维打击",
    "崔顶夫人": "翠顶夫人", "磁鼎夫人": "翠顶夫人",
    "康特": "counter",
}

_ORAL_BAN_PHRASES = re.compile(
    r"(孩子们|主包|那么以上就是|我们下[期次]再|可以看我上条视频"
    r"|不知道怎么养的|视频标签|UP主[：:]|播放量[：:]"
    r"|老规矩|为了方便理解|就是我们正式的"
    r"|置顶评论|置顶平论|配套资料|配套籽料|自取哦"
    r"|这对我真的很重要|这对我很重要"
    r"|评论.*666|留言.*666|666即可"
    r"|免费.*打包带走|全部免费|免费.*带走"
    r"|全网最齐全|零基础小白专用|小白专用课"
    r"|一键三连|请给up主|支持一下UP"
    r"|如果.*有用.*请|如果.*有帮助.*请"
    r"|无论是新手小白|无论.*还是.*皆可"
    r"|拜托.*重要|支持一下哦)"
)

_GAME_LINEUP_HINTS = (
    "pvp阵容", "阵容", "精灵", "技能", "竞技场", "队伍", "首发", "补位", "印记",
)

_MECHANISM_GUIDE_HINTS = (
    "获取方式", "获取机制", "属性用途", "概率", "保底", "触发", "异色", "图鉴", "赛季", "限定",
    "如何区别", "怎么获得", "怎么获取", "触发条件", "几率", "判定",
    "噩梦", "稀有", "超进化", "进化条件", "解锁条件",
    "深渊形态", "清除药剂", "去除污染", "净化", "排位", "pvp", "生蛋",
    "国王球", "魔光球", "炫彩球", "球的不同属性", "来回抓", "交替抓",
)

_MECHANISM_SECTION_MARKERS: dict[str, tuple[str, ...]] = {
    "获取方式": ("获取方式", "获取机制", "怎么获得", "怎么获取", "可以通过", "唯一途径", "只能通过"),
    "触发条件": ("触发条件", "触发方式", "什么时候触发", "当你", "需要满足", "触发深渊"),
    "概率/保底": ("概率", "几率", "保底", "百分之", "%"),
    "类型区分": ("如何区别", "怎么区分", "分为", "一共有", "种类"),
    "判定方法": ("判定", "判断", "看它的", "观察", "辨别"),
    "赛季/时效": ("赛季", "限定", "过期", "下架", "仅在", "活动期间"),
    "进化/升级": ("进化", "超进化", "升级", "解锁", "进化条件"),
}

_MECHANISM_RULE_PATTERNS: dict[str, tuple[str, ...]] = {
    "获取方式": (
        r"(?:捕捉虫系精灵|捕捉.*精灵|抓虫系精灵).{0,28}(?:触发|深渊|概率|星辰虫)",
        r"(?:触发|深渊).{0,24}(?:概率|几率|星辰虫|出现)",
    ),
    "概率优化": (
        r"(?:两种|两只|两类).{0,16}(?:来回抓|交替抓|换着抓)",
        r"(?:抓[一二两三四五六七八九十\d]+个).{0,16}(?:抓[一二两三四五六七八九十\d]+个)",
        r"这边[一二两三四五六七八九十\d]+个.{0,12}那边[一二两三四五六七八九十\d]+个",
        r"(?:概率|几率).{0,8}(?:更高|提高)",
    ),
    "使用限制": (
        r"(?:深渊形态|清除药剂|去除污染|净化).{0,28}(?:排位|PVP|可以用|能用)",
        r"清除药剂.{0,28}(?:污染|排位|PVP)",
    ),
    "繁殖用途": (
        r"(?:母的|母体|窝里|生蛋).{0,28}(?:生蛋|窝里|别的虫子)",
    ),
    "球属性建议": (
        r"(?:国王球|魔光球|炫彩球|球的不同属性|随机触发|属性来区分)",
    ),
}

_ORAL_FILLER_INLINE_RE = re.compile(
    r"(哈喽兄弟们|兄弟们|呃|额|啊|呢|吧|哦|呀|哈哈|怎么说呢|说一下这个|今天说一下这个"
    r"|再一个就是|再一个说是|再一个|你看一下|看一下|然后给.*?再说一下|然后再说一下"
    r"|然后就是|就是说|所以说|这个他是|这个星辰虫额|确实|挺那个什么的)",
    re.IGNORECASE,
)

_GAME_ENTITY_CANDIDATES = (
    "不枯骨", "小皮球", "落陨星兔", "翠顶夫人", "罗隐", "白金独角兽", "彩虹独角兽",
    "狼王队", "翼王队", "毒队", "星云队", "粉星仔", "粉粉星",
)

_GAME_SECTION_MARKERS = {
    "获取方式": ("获取方式", "没法直接抓到", "代币商店", "精灵蛋", "图鉴里面搜"),
    "技能配置": ("技能推荐带", "技能需要携带", "自带技能", "升级即可解锁"),
    "职责定位": ("玩法逻辑", "辅助精灵", "伤害清洁手", "首发位置", "终结位"),
    "顺风打法": ("实战逻辑", "首发", "叠印记", "多维打击输出"),
    "逆风补救": ("逆风情况下", "兜底", "收尾", "切罗隐", "切翠顶夫人"),
    "补位精灵": ("空缺着三个位置", "带一只", "另外两只带", "兜底"),
    "克制对象": ("康特", "毒队", "狼王队", "翼王队", "竞技场环境"),
}

_PROMO_NOISE_PHRASES = re.compile(
    r"(一键三连|点赞|投币|收藏|分享|关注|弹幕|评论留言|打包带走|拜托"
    r"|支持一下|免费.*带走|如果.*有用.*请|长按点赞|全网最"
    r"|置顶评论|置顶平论|配套资料|配套籽料|自取哦"
    r"|评论.*666|留言.*666|666即可|免费.*打包|全部免费"
    r"|零基础小白专用|小白专用课|这对我真的很重要"
    r"|无论是新手|无论.*还是.*皆可|无论.*皆可学习"
    r"|为了制作这套|翻遍了.*所有的|鱼龙混杂|滥鱼充数"
    r"|花里胡哨|最原始最直观|疯狂输出知识点"
    r"|请给up主|让更多.*小伙伴.*看到|拜托.*重要)"
)

_SUBSTANCE_MARKERS = re.compile(
    r"(第[一二三四五六七八九十\d]+步|步骤[一二三\d]|代码[是如]"
    r"|命令[是如]|参数[是为]|返回值|输出结果|报错"
    r"|具体来说|举个例子|比如说.*[，,].*具体"
    r"|\d+\.\d+%|\d+倍|公式[是为]|原理[是在]"
    r"|首先.*然后.*最后|实现方式|底层原理"
    r"|源码|源代码|函数|接口|类名|变量)"
)

_COURSE_OUTLINE_HINTS = (
    "基础篇", "进阶篇", "实战篇", "入门篇", "高级篇", "项目篇",
    "第一部分", "第二部分", "第三部分",
    "第一章", "第二章", "第三章",
    "模块一", "模块二", "模块三",
)

_COURSE_SECTION_RE = re.compile(
    r"(基础篇|进阶篇|实战篇|入门篇|高级篇|项目篇"
    r"|第[一二三四五六七八九十\d]+(?:部分|章|节|模块))"
)


# ---------------------------------------------------------------------------
# 文本清理基础函数
# ---------------------------------------------------------------------------


def _clean_text(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"聽", " ", text)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _strip_tags(value: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|li|h1|h2|h3|blockquote|pre)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return _clean_text(text)


def _text_overlap_ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    set_a = set(a)
    set_b = set(b)
    intersection = len(set_a & set_b)
    return intersection / min(len(set_a), len(set_b)) if min(len(set_a), len(set_b)) > 0 else 0.0


def _dedupe_lines(lines: Iterable[str]) -> str:
    seen = set()
    kept = []
    for line in lines:
        cleaned = _clean_text(line)
        if len(cleaned) < 12:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        kept.append(cleaned)
        if len("\n".join(kept)) >= 12000:
            break
    return "\n\n".join(kept)


def _normalize_url(url: str) -> str:
    text = (url or "").strip()
    if not text:
        raise ValueError("请输入网址。")
    if not re.match(r"^https?://", text, flags=re.IGNORECASE):
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("当前只支持 http 或 https 网址。")
    return text


def _normalize_compare_text(text: str) -> str:
    lowered = (text or "").lower()
    lowered = re.sub(r"\s+", "", lowered)
    lowered = re.sub(r"[^一-鿿A-Za-z0-9]", "", lowered)
    return lowered


def _similar_text_ratio(a: str, b: str) -> float:
    normalized_a = _normalize_compare_text(a)
    normalized_b = _normalize_compare_text(b)
    if not normalized_a or not normalized_b:
        return 0.0
    if normalized_a in normalized_b or normalized_b in normalized_a:
        return min(len(normalized_a), len(normalized_b)) / max(len(normalized_a), len(normalized_b))
    return _text_overlap_ratio(normalized_a, normalized_b)


def _dedupe_sentences(sentences: list[str], threshold: float = 0.72) -> list[str]:
    kept: list[str] = []
    for sentence in sentences:
        cleaned = _clean_text(sentence)
        if len(cleaned) < 8:
            continue
        if any(_similar_text_ratio(cleaned, existing) >= threshold for existing in kept):
            continue
        kept.append(cleaned)
    return kept


def _split_sentences(text: str) -> list[str]:
    normalized = re.sub(r"\n+", " ", text)
    parts = re.split(r"(?<=[。！？!?；;\.])\s*|\s{2,}", normalized)
    if len(parts) <= 3 and len(normalized) > 200:
        parts = re.split(r"\s+", normalized)
        merged = []
        buf = ""
        for p in parts:
            buf = (buf + " " + p).strip() if buf else p
            if len(buf) >= 30:
                merged.append(buf)
                buf = ""
        if buf:
            merged.append(buf)
        parts = merged if len(merged) > 3 else [normalized]
    sentences = []
    for part in parts:
        cleaned = _clean_text(part)
        if len(cleaned) >= 8:
            sentences.append(cleaned)
    return sentences


# ---------------------------------------------------------------------------
# 包装文本剥离
# ---------------------------------------------------------------------------


def _strip_packaging_text(text: str) -> str:
    lines = text.split("\n")
    cleaned: list[str] = []
    skip_supplement_block = False
    for line in lines:
        stripped = line.strip()
        if skip_supplement_block:
            if re.match(r"^\d+[\.\、]", stripped) or not stripped:
                continue
            if _PACKAGING_LINE_PREFIXES.match(stripped) or _PACKAGING_FULL_STRIP_PREFIXES.match(stripped):
                skip_supplement_block = False
            else:
                continue
        if _PACKAGING_FULL_STRIP_PREFIXES.match(stripped):
            if stripped.startswith("补充重点"):
                skip_supplement_block = True
            continue
        m = _PACKAGING_LINE_PREFIXES.match(stripped)
        if m:
            remainder = stripped[m.end():].strip()
            if len(remainder) < 30:
                continue
            cleaned.append(remainder)
        else:
            cleaned.append(line)
    return "\n".join(cleaned)


def _dedupe_text_blocks(text: str, threshold: float = 0.75) -> str:
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    if len(paragraphs) <= 1:
        return text
    kept: list[str] = []
    for para in paragraphs:
        is_dup = False
        for existing in kept:
            if _text_overlap_ratio(para, existing) > threshold:
                is_dup = True
                if len(para) > len(existing):
                    kept[kept.index(existing)] = para
                break
        if not is_dup:
            kept.append(para)
    return "\n\n".join(kept)


# ---------------------------------------------------------------------------
# 领域词纠错
# ---------------------------------------------------------------------------


def _build_title_corrections(title: str, body: str) -> dict[str, str]:
    title_entities = set(re.findall(r"[一-鿿]{2,8}", title))
    if not title_entities:
        return {}
    corrections: dict[str, str] = {}
    for entity in title_entities:
        if entity in body:
            continue
        entity_len = len(entity)
        for width in (entity_len, entity_len - 1, entity_len + 1):
            if width < 2:
                continue
            candidates = re.findall(r"[一-鿿]{" + str(width) + r"}", body)
            for candidate in set(candidates):
                if candidate == entity or candidate in corrections:
                    continue
                overlap = len(set(entity) & set(candidate))
                ratio = overlap / max(len(set(entity)), len(set(candidate)))
                if ratio >= 0.5 and candidate not in _DOMAIN_CORRECTIONS:
                    corrections[candidate] = entity
                    break
            if entity in [v for v in corrections.values()]:
                break
    return corrections


def _correct_domain_terms(text: str, *, title: str = "") -> str:
    result = text
    for wrong, right in _DOMAIN_CORRECTIONS.items():
        result = result.replace(wrong, right)
    if title:
        dynamic_corrections = _build_title_corrections(title, result)
        for wrong, right in dynamic_corrections.items():
            result = result.replace(wrong, right)
    return result


# ---------------------------------------------------------------------------
# 口播噪声清理
# ---------------------------------------------------------------------------


def _clean_oral_noise(text: str) -> str:
    patterns = [
        r"大家好[，,]?\s*我是.*?[，,。\s]",
        r"(hello|hi)\s*(everyone|guys|there)?[,，。\s]*",
        r"欢迎(大家)?来到.*?(频道|直播间|视频)[，,。\s]*",
        r"今天(我们|给大家|跟大家).*?(聊一聊|讲一下|说一说|分享)[，,。\s]*",
        r"(别忘了|记得)?(一键三连|点赞|投币|收藏|分享|关注|订阅|转发)[一下]?[，,。！!]*",
        r"喜欢.*?(点赞|关注|转发|收藏)[，,。！!]*",
        r"(如果|觉得).*?(有用|有帮助|不错).*?(点赞|关注|收藏|三连)[，,。！!]*",
        r"拜托.*?(重要|三连)[，,。！!]*",
        r"(长按|双击)点赞[，,。！!]*",
        r"免费.*?带走[，,。！!]*",
        r"支持一下[，,。！!]*",
        r"(?<!\w)(那么|然后|所以说|就是说|对不对|对吧|嗯|啊|哈|呢|吧|呀|哦|嘛|额)(?=\s|$)",
        r"兄弟们[，,。！!\s]*",
        r"怎么说呢[，,。！!\s]*",
        r"看一下[，,。！!\s]*",
        r"再一个(就是|说是)?[，,。！!\s]*",
        r"我们下[期次]再见[，,。！!]*",
        r"(感谢|谢谢)(大家)?(的?.?观看|收看|支持)[，,。！!]*",
        r"(拜拜|bye\s*bye)[，,。！!]*",
    ]
    result = text
    for p in patterns:
        result = re.sub(p, " ", result, flags=re.IGNORECASE)
    result = re.sub(r"[ \t]+", " ", result)
    return result.strip()


def _compress_oral_clause(text: str, max_len: int = 96) -> str:
    cleaned = _clean_text(text)
    cleaned = _ORAL_FILLER_INLINE_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\bP\s*V\s*P\b", "PVP", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"([一-鿿A-Za-z0-9])\s+([一-鿿A-Za-z0-9])", r"\1\2", cleaned)
    cleaned = re.sub(r"(生的){2,}", "生的", cleaned)
    cleaned = re.sub(r"(去除它的污染|去除他的污染)", "去除污染", cleaned)
    cleaned = re.sub(r"获取机制其实挺简单的就是", "", cleaned)
    cleaned = re.sub(r"^(这个|就是|然后|那么|如果说|如果|你像我这个|你抓这个|再说一下)+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，,；;。")
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 3].rstrip() + "..."
    return cleaned


def _split_action_clauses(text: str) -> list[str]:
    normalized = _clean_text(text)
    normalized = re.sub(
        r"(然后再一个就是|再一个就是|再一个说是|然后再说一下|然后给.*?再说一下|"
        r"还有就是|另外|其次|最后|具体怎么区分的话|但是|不过|你抓这个|尽量不要用)",
        "。",
        normalized,
        flags=re.IGNORECASE,
    )
    clauses = _split_sentences(normalized)
    compressed: list[str] = []
    for clause in clauses:
        shrunk = _compress_oral_clause(clause)
        if len(shrunk) >= 8:
            compressed.append(shrunk)
    return _dedupe_sentences(compressed, threshold=0.78)


def _normalize_title_topic(title: str) -> str:
    cleaned = _clean_text(title)
    cleaned = re.sub(r"[_\-|｜].*$", "", cleaned)
    cleaned = re.sub(r"(哔哩哔哩.*|bilibili.*)$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _strip_point_prefix(text: str) -> str:
    cleaned = _clean_text(text)
    cleaned = re.sub(
        r"^(阵容名称|核心精灵|获取方式|技能配置|职责定位|顺风打法|逆风补救|补位精灵|克制对象|重点摘要|关键点)[：:]",
        "",
        cleaned,
    )
    return cleaned.strip(" ：:")


def _shorten_phrase(text: str, max_len: int = 28) -> str:
    cleaned = _strip_point_prefix(text).rstrip("。！？!?")
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 3].rstrip() + "..."


# ---------------------------------------------------------------------------
# 机制攻略提取
# ---------------------------------------------------------------------------


def _is_mechanism_guide_video(title: str, text: str) -> bool:
    combined = f"{title} {text}".lower()
    hit_count = sum(1 for hint in _MECHANISM_GUIDE_HINTS if hint in combined)
    return hit_count >= 3


def _collect_mechanism_rule_text(text: str, patterns: tuple[str, ...], max_len: int = 96) -> str:
    clauses = _split_action_clauses(text)
    picked: list[str] = []
    for clause in clauses:
        if any(re.search(pattern, clause, flags=re.IGNORECASE) for pattern in patterns):
            if clause.startswith("今天说一下") or clause.startswith("目前在游戏里边"):
                continue
            picked.append(clause)
    merged = _dedupe_sentences(picked, threshold=0.76)
    if not merged:
        return ""
    result = "；".join(merged[:3]).strip("；")
    if len(result) > max_len:
        result = result[: max_len - 3].rstrip() + "..."
    return result


def _refine_mechanism_rule_text(label: str, text: str) -> str:
    cleaned = _compress_oral_clause(text, max_len=120)
    if label == "获取方式":
        if re.search(r"捕捉.*(?:虫系|精灵)", cleaned) and "深渊" in cleaned:
            return "捕捉虫系精灵到一定数量后触发深渊，有概率出现目标精灵"
    elif label == "概率优化":
        parts: list[str] = []
        if re.search(r"两种.*(?:来回抓|交替抓)", cleaned):
            parts.append("不要只抓一种，最好两种虫子交替抓")
        if re.search(r"抓五个", cleaned) or re.search(r"这边[三3]个", cleaned) or re.search(r"那边[三3]个", cleaned):
            parts.append("可按五五或三三节奏切换来提高概率")
        if parts:
            return "；".join(parts)
    elif label == "使用限制":
        if "清除药剂" in cleaned and ("PVP" in cleaned or "排位" in cleaned):
            return "深渊形态需先用清除药剂去除污染，之后才能用于 PVP"
    elif label == "繁殖用途":
        if "生蛋" in cleaned and ("母" in cleaned or "窝里" in cleaned):
            return "母体可放进窝里和别的虫子生蛋"
    elif label == "球属性建议":
        parts = []
        if "国王球" in cleaned or "魔光球" in cleaned:
            parts.append("更推荐国王球或魔光球")
        if "炫彩球" in cleaned:
            parts.append("炫彩球会随机触发效果，不适合 PVP")
        if parts:
            return "；".join(parts)
    return cleaned


def _extract_mechanism_guide_key_points(text: str) -> list[str]:
    points: list[str] = []
    for label, patterns in _MECHANISM_RULE_PATTERNS.items():
        section_text = _collect_mechanism_rule_text(text, patterns)
        if section_text:
            points.append(f"{label}：{_refine_mechanism_rule_text(label, section_text)}。")
    for label, markers in _MECHANISM_SECTION_MARKERS.items():
        if any(point.startswith(f"{label}：") for point in points):
            continue
        section_text = _extract_game_section_text(text, markers)
        if section_text:
            points.append(f"{label}：{_refine_mechanism_rule_text(label, section_text)}。")
    numeric_sentences = _extract_numeric_rules(text)
    for ns in numeric_sentences:
        if not any(_text_overlap_ratio(ns, p) > 0.6 for p in points):
            points.append(ns)
    return _dedupe_sentences(points, threshold=0.68)[:5]


def _build_mechanism_guide_summary(title: str, text: str) -> str:
    topic = _normalize_title_topic(title)
    point_map: dict[str, str] = {}
    for point in _extract_mechanism_guide_key_points(text):
        match = re.match(r"^([^：:]+)[：:](.+?)[。]?$", point)
        if not match:
            continue
        point_map.setdefault(match.group(1), _compress_oral_clause(match.group(2), max_len=48))

    summary_parts: list[str] = []
    for label in ("获取方式", "概率优化", "使用限制", "繁殖用途", "球属性建议"):
        content = point_map.get(label)
        if content:
            summary_parts.append(content)

    if summary_parts:
        intro = f"这期内容讲的是《{topic}》的获取与用途" if topic else "这期内容讲的是该机制的获取与用途"
        return f"{intro}：{'；'.join(summary_parts[:4])}。"

    mechanism_kw = []
    for hint in ("获取机制", "属性用途", "概率", "触发", "获取", "生蛋", "PVP"):
        if hint.lower() in text.lower():
            mechanism_kw.append(hint)
    kw_text = "、".join(mechanism_kw[:3]) if mechanism_kw else "相关机制"
    if topic:
        return f"这期内容主要讲解《{topic}》中{kw_text}的详细规则与实操要点。"
    return f"这期内容主要讲解{kw_text}的详细规则与实操要点。"


# ---------------------------------------------------------------------------
# 内容密度门控
# ---------------------------------------------------------------------------


def _compute_substance_ratio(text: str) -> float:
    sentences = _split_sentences(text)
    if not sentences:
        return 0.0
    substance_count = 0
    promo_count = 0
    for sent in sentences:
        if _PROMO_NOISE_PHRASES.search(sent) or _ORAL_BAN_PHRASES.search(sent):
            promo_count += 1
        elif _SUBSTANCE_MARKERS.search(sent) or len(sent) > 40:
            substance_count += 1
    total = max(len(sentences), 1)
    return substance_count / total


def _is_promotional_video(title: str, text: str) -> bool:
    ratio = _compute_substance_ratio(text)
    promo_hits = len(_PROMO_NOISE_PHRASES.findall(text))
    return ratio < 0.20 and promo_hits >= 3


# ---------------------------------------------------------------------------
# 课程大纲
# ---------------------------------------------------------------------------


def _is_course_outline_video(title: str, text: str) -> bool:
    combined = f"{title} {text}".lower()
    hit_count = sum(1 for hint in _COURSE_OUTLINE_HINTS if hint in combined)
    return hit_count >= 2


def _extract_tech_terms_from_sentence(sent: str) -> list[str]:
    abbrevs = re.findall(
        r"\b(?:SFT|RLHF|RAG|Agent|Langchain|LLM|API|GPU|NLP|Transformer|Python)\b", sent, re.IGNORECASE
    )
    cn_patterns = re.findall(
        r"(提示词工程|私有化部署|预训练|模型微调|大模型微调|环境搭建"
        r"|知识库|问答系统|数字人|核心原理|企业级落地"
        r"|大模型训练|模型训练|大模型部署|开发环境"
        r"|超进化|进化条件|解锁条件)",
        sent,
    )
    all_terms = abbrevs + cn_patterns
    seen: set[str] = set()
    unique: list[str] = []
    for t in all_terms:
        t_clean = t.strip()
        t_lower = t_clean.lower()
        if len(t_lower) < 2 or t_lower in seen:
            continue
        seen.add(t_lower)
        unique.append(t_clean)
    return unique


def _extract_course_outline_key_points(title: str, text: str) -> list[str]:
    full_text = " ".join(_split_sentences(text))
    markers = list(_COURSE_SECTION_RE.finditer(full_text))
    if not markers:
        return []

    sections: dict[str, list[str]] = {}
    for i, m in enumerate(markers):
        section_name = m.group(1)
        start = m.end()
        end = markers[i + 1].start() if i + 1 < len(markers) else len(full_text)
        segment = full_text[start:end]
        if section_name not in sections:
            sections[section_name] = []
        tech_terms = _extract_tech_terms_from_sentence(segment)
        for term in tech_terms:
            if term not in sections[section_name]:
                sections[section_name].append(term)

    all_section_terms: set[str] = set()
    for terms_list in sections.values():
        all_section_terms.update(t.lower() for t in terms_list)

    global_terms: list[str] = []
    for sent in _split_sentences(text):
        for t in _extract_tech_terms_from_sentence(sent):
            if t.lower() not in all_section_terms and t.lower() not in {g.lower() for g in global_terms}:
                global_terms.append(t)

    points: list[str] = []
    for section_name, terms in sections.items():
        if terms:
            terms_text = "、".join(terms[:6])
            points.append(f"{section_name}：{terms_text}")
        else:
            points.append(f"{section_name}（未展开具体内容）")

    if global_terms:
        points.append(f"涉及技术：{'、'.join(global_terms[:8])}")

    return _dedupe_sentences(points, threshold=0.68)[:6]


# ---------------------------------------------------------------------------
# 摘要生成辅助
# ---------------------------------------------------------------------------

def _build_promotional_summary(title: str, text: str) -> str:
    topic = _normalize_title_topic(title)
    all_terms: list[str] = []
    for sent in _split_sentences(text):
        all_terms.extend(_extract_tech_terms_from_sentence(sent))
    seen: set[str] = set()
    unique_terms: list[str] = []
    for t in all_terms:
        if t.lower() not in seen:
            seen.add(t.lower())
            unique_terms.append(t)
    terms_text = "、".join(unique_terms[:8]) if unique_terms else "相关技术"
    if topic:
        return f"这是一期关于《{topic}》的课程导学/宣传视频，提及的主题包括{terms_text}，本集暂无实质性教学内容展开。"
    return f"这是一期课程导学/宣传视频，提及的主题包括{terms_text}，本集暂无实质性教学内容展开。"


def _build_course_outline_summary(title: str, text: str) -> str:
    topic = _normalize_title_topic(title)
    outline_points = _extract_course_outline_key_points(title, text)
    if outline_points:
        section_parts = [p for p in outline_points if not p.startswith("涉及技术")]
        tech_parts = [p for p in outline_points if p.startswith("涉及技术")]
        sections = "；".join(section_parts[:4])
        tech_suffix = f"。{tech_parts[0]}" if tech_parts else ""
        base = f"这期视频介绍了《{topic}》课程的整体大纲，包括{sections}" if topic else "这期视频介绍了课程的整体大纲，包括{sections}"
        return f"{base}{tech_suffix}。"
    return _build_promotional_summary(title, text)


def _preprocess_summary_text(text: str, *, title: str = "") -> str:
    cleaned_text = _strip_packaging_text(text)
    cleaned_text = _correct_domain_terms(cleaned_text, title=title)
    cleaned_text = _dedupe_text_blocks(cleaned_text)
    cleaned_text = _clean_oral_noise(cleaned_text)
    sentences = _split_sentences(cleaned_text)
    sentences = _dedupe_sentences(sentences)
    return "\n".join(sentences) if sentences else cleaned_text


# ---------------------------------------------------------------------------
# 游戏阵容相关
# ---------------------------------------------------------------------------


def _is_game_lineup_video(title: str, text: str) -> bool:
    combined = f"{title} {text}".lower()
    hit_count = sum(1 for item in _GAME_LINEUP_HINTS if item in combined)
    return hit_count >= 3


def _extract_game_entities(text: str, limit: int = 5) -> list[str]:
    found = [name for name in _GAME_ENTITY_CANDIDATES if name in text]
    return found[:limit]


def _extract_game_section_text(text: str, markers: tuple[str, ...], max_len: int = 88) -> str:
    sentences = _dedupe_sentences(_split_sentences(text))
    for sentence in sentences:
        if any(marker in sentence for marker in markers):
            trimmed = sentence.rstrip("。！？!?")
            return trimmed if len(trimmed) <= max_len else trimmed[: max_len - 3].rstrip() + "..."
    return ""


def _extract_numeric_rules(text: str, limit: int = 3) -> list[str]:
    _NUMERIC_RULE_PATTERN = re.compile(
        r"(\d+(\.\d+)?%"
        r"|\d+(\.\d+)?倍"
        r"|最多\d+|最少\d+|一共\d+|总共\d+"
        r"|[一二两三四五六七八九十百\d]+种"
        r"|[一二两三四五六七八九十百\d]+个"
        r"|抓[一二两三四五六七八九十百\d]+个"
        r"|这边[一二两三四五六七八九十百\d]+个"
        r"|那边[一二两三四五六七八九十百\d]+个"
        r"|两种.*来回抓|交替抓|换着抓"
        r"|只能通过|唯一途径"
        r"|赛季结束|活动结束|限时"
        r"|保底\d+|每\d+次"
        r"|不超过\d+|至少\d+)"
    )
    sentences = _split_action_clauses(text)
    hits: list[str] = []
    for sent in sentences:
        if _NUMERIC_RULE_PATTERN.search(sent):
            trimmed = _compress_oral_clause(sent, max_len=88).rstrip("。！？!?")
            if len(trimmed) > 88:
                trimmed = trimmed[:85].rstrip() + "..."
            hits.append(trimmed)
            if len(hits) >= limit:
                break
    return hits


def _build_game_lineup_summary(title: str, text: str) -> str:
    lineup_name = ""
    match = re.search(r"(?:阵容名称叫|叫)([一-鿿A-Za-z0-9]{2,12}队)", text)
    if match:
        lineup_name = match.group(1)
    elif "星云队" in text:
        lineup_name = "星云队"
    core_entities = _extract_game_entities(text, limit=3)
    counter_entities = [name for name in ("毒队", "狼王队", "翼王队") if name in text]
    lineup_part = lineup_name or (title[:16] if title else "这套阵容")
    core_part = "、".join(core_entities) if core_entities else "多只核心精灵"
    counter_part = "、".join(counter_entities[:3]) if counter_entities else "当前常见竞技场阵容"
    return f"{lineup_part}是一套围绕印记叠层与收割展开的PVP阵容，核心成员包括{core_part}，主要针对{counter_part}。"


def _extract_game_lineup_key_points(text: str) -> list[str]:
    points: list[str] = []
    lineup_name = ""
    match = re.search(r"(?:阵容名称叫|叫)([一-鿿A-Za-z0-9]{2,12}队)", text)
    if match:
        lineup_name = match.group(1)
    core_entities = _extract_game_entities(text, limit=5)
    if lineup_name or core_entities:
        summary = f"阵容名称：{lineup_name or '未明确提及'}；核心精灵：{'、'.join(core_entities[:3]) or '未明确提及'}。"
        points.append(summary)
    for label, markers in _GAME_SECTION_MARKERS.items():
        section_text = _extract_game_section_text(text, markers)
        if section_text:
            points.append(f"{label}：{section_text}。")
    return _dedupe_sentences(points, threshold=0.68)[:5]


# ---------------------------------------------------------------------------
# HTML 提取
# ---------------------------------------------------------------------------


def _remove_noise_blocks(html_text: str) -> str:
    cleaned = re.sub(r"<script\b.*?</script>", " ", html_text, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<style\b.*?</style>", " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<noscript\b.*?</noscript>", " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<!--.*?-->", " ", cleaned, flags=re.DOTALL)
    return cleaned


def _extract_title(html_text: str) -> str:
    patterns = [
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+name=["\']title["\'][^>]+content=["\']([^"\']+)',
        r"<title[^>]*>(.*?)</title>",
        r"<h1[^>]*>(.*?)</h1>",
    ]
    for pattern in patterns:
        match = re.search(pattern, html_text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            title = _strip_tags(match.group(1))
            if title:
                return title
    return ""


def _extract_meta_description(html_text: str) -> str:
    patterns = [
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, html_text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            description = _clean_text(match.group(1))
            if description:
                return description
    return ""


def _extract_html_segments(html_text: str) -> list[str]:
    main_match = re.search(r"<(article|main)[^>]*>(.*?)</\1>", html_text, flags=re.IGNORECASE | re.DOTALL)
    source = main_match.group(2) if main_match else html_text
    segments = []
    for match in re.finditer(
        r"<(h1|h2|h3|p|li|blockquote|pre)[^>]*>(.*?)</\1>", source, flags=re.IGNORECASE | re.DOTALL
    ):
        segment = _strip_tags(match.group(2))
        if len(segment) >= 12:
            segments.append(segment)
    if segments:
        return segments
    body_match = re.search(r"<body[^>]*>(.*?)</body>", html_text, flags=re.IGNORECASE | re.DOTALL)
    fallback_source = body_match.group(1) if body_match else html_text
    fallback_text = _strip_tags(fallback_source)
    return [line for line in re.split(r"[\r\n]+", fallback_text) if len(line.strip()) >= 12]


# ---------------------------------------------------------------------------
# URL/内容类型辅助
# ---------------------------------------------------------------------------


def _derive_title_from_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.strip("/") or parsed.netloc
    return path.split("/")[-1] or parsed.netloc or "未命名网页"


def _classify_content_kind(url: str, title: str, text: str, content_type: str) -> str:
    host = urlparse(url).netloc.lower()
    combined = f"{host} {title} {text[:400]}".lower()
    if any(keyword in host for keyword in ["pan.baidu", "quark", "aliyundrive", "drive.google"]):
        return "资源页"
    if any(keyword in host for keyword in ["bilibili", "youtube", "youku", "iqiyi", "v.qq", "ted.com"]):
        return "视频页"
    if any(keyword in combined for keyword in ["doc", "文档", "教程", "readthedocs", "api", "手册"]):
        return "文档页"
    if "text/plain" in content_type:
        return "文本页"
    return "网页文章"
