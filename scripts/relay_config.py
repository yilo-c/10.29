# -*- coding: utf-8 -*-
"""思忆集 免费 LLM 提供方 接入脚本（ProviderConfig + relay_model_map）。

用法（在 思忆集app 目录下，python 解释器须能 import core.*）:
    python scripts/relay_config.py show                      # 查看当前 provider / 中继 map
    python scripts/relay_config.py add-free                  # 预览：将默认不存在的免费 provider 合并进去
    python scripts/relay_config.py add-free --apply          # 真正写入 settings.json
    python scripts/relay_config.py add-free --data-dir <路径>  # 指定 data 目录

说明：
- settings.json 是扁平 key→value JSON。脚本只更新 provider_configs 与 relay_model_map 两个键，
  保留其余键；已存在的 provider/别名（按 name / key）不会覆盖。
- 默认 dry-run：只打印将发生的变更，加 --apply 才落盘。
- 免费 provider 的 api_key 为空占位——在桌面端「AI 管理」面板里填好各自平台 key 并启用即可路由。
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def _default_data_dir() -> str:
    env = os.environ.get("SIYI_DATA_DIR")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    # 脚本位于 <repo>/scripts 时，上一级 .\思忆集app；data 默认在 app 同层 data/
    for cand in (
        os.path.join(here, "..", "思忆集app", "data"),
        os.path.join(here, "data"),  # 直接放在 data 下时
        os.path.join(os.getcwd(), "data"),
    ):
        p = os.path.abspath(cand)
        if os.path.isdir(p):
            return p
    return os.path.abspath(os.path.join(here, "..", "思忆集app", "data"))


def _settings_path(data_dir: str) -> str:
    return os.path.join(data_dir, "settings.json")


def _load(data_dir: str) -> dict:
    path = _settings_path(data_dir)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data_dir: str, cfg: dict) -> None:
    path = _settings_path(data_dir)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ── 免费提供方定义（2026 已核验：永久免费档、OpenAI 兼容、无需信用卡）──
# api_key 留空占位，需用户在各平台控制台获取后填入。
FREE_PROVIDERS = [
    {
        "name": "groq",
        "api_base": "https://api.groq.com/openai/v1",
        "api_key": "",
        "model": "gpt-oss-120b",
        "models": ["gpt-oss-120b", "gpt-oss-20b", "llama-3.3-70b-versatile", "qwen/qwen3-32b", "groq/compound"],
        "timeout": 60,
        "enabled": True,
        "is_fallback": False,
        "is_local": False,
        "description": "Groq LPU 最快推理 · 永久免费档约 30 RPM / 1000 RPD，无信用卡",
        "purpose": "general",
    },
    {
        "name": "openrouter",
        "api_base": "https://openrouter.ai/api/v1",
        "api_key": "",
        "model": "meta-llama/llama-3.3-70b-instruct:free",
        "models": [
            "meta-llama/llama-3.3-70b-instruct:free",
            "meta-llama/llama-4-maverick:free",
            "qwen/qwen3-235b-a22b:free",
        ],
        "timeout": 120,
        "enabled": True,
        "is_fallback": False,
        "is_local": False,
        "description": "OpenRouter 聚合 70+ 提供方 · 20+ 免费模型，无信用卡",
        "purpose": "general",
    },
]

# 免费档的外部模型别名（供 relay 网关：外部 agent 用别名即可路由）
FREE_ALIASES = {
    "groq": "groq/gpt-oss-120b",
    "openrouter-free": "openrouter/meta-llama/llama-3.3-70b-instruct:free",
    "free": "",  # 快捷别名：走默认回退链
}


def _merge_providers(cfg: dict) -> tuple[list, list]:
    """合并免费 provider；返回 (新增的, 冲突跳过)。不改动已存在的。"""
    providers = cfg.get("provider_configs", [])
    existing = {p.get("name") for p in providers if isinstance(p, dict)}
    added, skipped = [], []
    for fp in FREE_PROVIDERS:
        if fp["name"] in existing:
            skipped.append(fp["name"])
            continue
        providers.append(fp)
        added.append(fp["name"])
    cfg["provider_configs"] = providers
    return added, skipped


def _merge_aliases(cfg: dict) -> tuple[list, list]:
    """合并免费别名；只补缺失键。"""
    m = cfg.get("relay_model_map", {})
    if not isinstance(m, dict):
        m = {}
    added, skipped = [], []
    for k, v in FREE_ALIASES.items():
        if k in m:
            skipped.append(k)
            continue
        m[k] = v
        added.append(k)
    cfg["relay_model_map"] = m
    return added, skipped


def _show(data_dir: str) -> None:
    cfg = _load(data_dir)
    print(f"data_dir = {os.path.abspath(data_dir)}")
    print(f"settings = {_settings_path(data_dir)}")
    providers = cfg.get("provider_configs", [])
    print(f"\nprovider_configs ({len(providers)}):")
    for p in providers:
        if not isinstance(p, dict):
            continue
        key = "已设" if p.get("api_key") else "空占位"
        flag = "本地" if p.get("is_local") else ""
        print(
            f"  - {p.get('name'):<14} {p.get('api_base', ''):<42} key[{key}] {flag} {('模型 ' + ','.join(p.get('models') or [p.get('model')]))[:60]}"
        )
    mm = cfg.get("relay_model_map", {})
    print(f"\nrelay_model_map ({len(mm)}):")
    for k, v in mm.items():
        print(f"  - {k:<16} -> {v or '(默认回退链)'}")
    key = cfg.get("relay_api_key", "")
    print(f"\nrelay_api_key = {key[:6] + '…' if key else '(未设置)'}")


def main() -> None:
    ap = argparse.ArgumentParser(description="思忆集 免费 LLM 接入工具")
    ap.add_argument("cmd", choices=["show", "add-free"])
    ap.add_argument("--apply", action="store_true", help="add-free：真正写盘（默认仅预览）")
    ap.add_argument("--data-dir", default=None, help="data 目录（默认 SIYI_DATA_DIR 或 思忆集app/data）")
    args = ap.parse_args()

    data_dir = args.data_dir or _default_data_dir()

    if args.cmd == "show":
        _show(data_dir)
        return

    cfg = _load(data_dir)
    prov_added, prov_skip = _merge_providers(cfg)
    alias_added, alias_skip = _merge_aliases(cfg)

    print(f"data_dir = {os.path.abspath(data_dir)}")
    print(f"将新增 provider : {prov_added or '(无)'}")
    print(f"已存在(跳过)   : {prov_skip or '(无)'}")
    print(f"将新增别名     : {alias_added or '(无)'}")
    print(f"已存在(跳过)   : {alias_skip or '(无)'}")

    if not (prov_added or alias_added):
        print("无变更。")
        return

    if not args.apply:
        print("\n[dry-run] 未写盘。确认无误后加 --apply 提交。")
        _show(data_dir)
        return

    _save(data_dir, cfg)
    print("\n已写入 settings.json。重启后端生效，并在 AI 管理面板填入各平台 api_key。")
    _show(data_dir)


if __name__ == "__main__":
    sys.exit(main())
