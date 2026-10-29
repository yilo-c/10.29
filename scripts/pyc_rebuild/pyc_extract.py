# -*- coding: utf-8 -*-
"""pyc 模块 API 提取工具：dir/签名/docstring/常量/外部名。

用法: python pyc_extract.py <模块名> [函数过滤词...]
"""
import importlib
import inspect
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "思忆集app"
sys.path.insert(0, str(APP_ROOT))


def dump_code(code, indent="  "):
    print(f"{indent}consts: {[c for c in code.co_consts if not hasattr(c, 'co_consts')]}")
    print(f"{indent}names : {list(code.co_names)}")
    print(f"{indent}varnames: {list(code.co_varnames)}")
    for c in code.co_consts:
        if hasattr(c, "co_consts"):
            print(f"{indent}-- nested code: {c.co_name} (line {c.co_firstlineno})")
            dump_code(c, indent + "    ")


def main():
    mod_name = sys.argv[1]
    filters = sys.argv[2:]
    mod = importlib.import_module(mod_name)
    print(f"### MODULE {mod_name}  file={getattr(mod, '__file__', '?')}")
    print(f"doc: {(mod.__doc__ or '').strip()[:400]}")
    print(f"names: {[n for n in dir(mod) if not n.startswith('__')]}")
    print()
    for name in sorted(dir(mod)):
        if name.startswith("__"):
            continue
        obj = getattr(mod, name)
        if inspect.isclass(obj) and obj.__module__ == mod_name:
            print(f"=== CLASS {name}  doc={(obj.__doc__ or '').strip()[:200]}")
            for mname, mval in vars(obj).items():
                if mname.startswith("__") and mname not in ("__init__",):
                    continue
                if callable(mval) and getattr(mval, "__code__", None):
                    sig = ""
                    try:
                        sig = str(inspect.signature(mval))
                    except Exception:
                        pass
                    doc = (mval.__doc__ or "").strip().replace("\n", " ")[:150]
                    print(f"  def {mname}{sig}  # line {mval.__code__.co_firstlineno} | {doc}")
                    if not filters or any(f in mname for f in filters):
                        dump_code(mval.__code__, "    ")
                else:
                    print(f"  attr {mname} = {repr(mval)[:120]}")
            print()
        elif inspect.isfunction(obj) and obj.__module__ == mod_name:
            sig = ""
            try:
                sig = str(inspect.signature(obj))
            except Exception:
                pass
            doc = (obj.__doc__ or "").strip().replace("\n", " ")[:150]
            print(f"=== FUNC {name}{sig}  # line {obj.__code__.co_firstlineno} | {doc}")
            if not filters or any(f in name for f in filters):
                dump_code(obj.__code__)
            print()
        else:
            if isinstance(obj, (str, int, float, bool, tuple, frozenset, dict, list)):
                print(f"=== CONST {name} = {repr(obj)[:200]}")
            else:
                print(f"=== OBJ   {name} : {type(obj).__name__}")


if __name__ == "__main__":
    main()
