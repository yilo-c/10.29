# -*- coding: utf-8 -*-
"""handlers 重建用 UTF-8 提取器：模块/函数 docstring + 全量反汇编。

用法: python handler_extract_utf8.py <模块名> <输出前缀>
产出: <输出前缀>_docs.json 与 <输出前缀>_dis.txt（均 UTF-8）
"""
import dis
import importlib
import inspect
import io
import json
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "思忆集app"
sys.path.insert(0, str(APP_ROOT))


def main() -> None:
    mod_name = sys.argv[1]
    prefix = sys.argv[2]
    mod = importlib.import_module(mod_name)

    docs: dict = {
        "module": mod.__doc__,
        "logger": getattr(getattr(mod, "logger", None), "name", None),
    }
    buf = io.StringIO()
    for name, obj in vars(mod).items():
        if name.startswith("__"):
            continue
        if inspect.isfunction(obj) and getattr(obj, "__module__", None) == mod_name:
            docs[name] = {"doc": obj.__doc__, "sig": str(inspect.signature(obj))}
            print(f"===== {name}", file=buf)
            dis.dis(obj, file=buf)
            # 嵌套闭包（handlers 的 RPC 实现都在 register 的嵌套函数里）
            for const in obj.__code__.co_consts:
                if hasattr(const, "co_consts"):
                    docs[f"{name}::{const.co_name}"] = {
                        "doc": const.co_consts[0] if const.co_consts and isinstance(const.co_consts[0], str) else None
                    }
                    print(f"===== {name} :: nested {const.co_name}", file=buf)
                    dis.dis(const, file=buf)
                    for inner in const.co_consts:
                        if hasattr(inner, "co_consts"):
                            print(f"===== {name} :: nested {const.co_name} :: {inner.co_name}", file=buf)
                            dis.dis(inner, file=buf)

    out_dir = Path(__file__).resolve().parent
    (out_dir / f"{prefix}_docs.json").write_text(
        json.dumps(docs, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / f"{prefix}_dis.txt").write_text(buf.getvalue(), encoding="utf-8")
    print(f"saved docs={prefix}_docs.json dis_lines={len(buf.getvalue().splitlines())}")


if __name__ == "__main__":
    main()
