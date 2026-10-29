from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = PROJECT_ROOT / "思忆集app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app_runtime.app_context import AppContext  # app_context 已迁入 app_runtime/ 子包


def _emit_stdout(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        stream = getattr(sys.stdout, "buffer", None)
        if stream is not None:
            stream.write((text + "\n").encode("utf-8", errors="replace"))
            return
        print(text.encode("ascii", errors="replace").decode("ascii"))


def main() -> int:
    parser = argparse.ArgumentParser(description="思忆集桌面 Doctor 诊断脚本")
    parser.add_argument("--deep", action="store_true", help="执行深度诊断，包括 LLM 与 automation-hub 连通性探测")
    parser.add_argument("--json", action="store_true", help="输出原始诊断 JSON")
    parser.add_argument("--debug-assets-json", action="store_true", help="输出共享 debug assets JSON")
    parser.add_argument("--output", default="", help="将结果写入指定文件")
    args = parser.parse_args()

    app_context = AppContext()
    report = app_context.run_desktop_doctor(deep=bool(args.deep))
    if args.debug_assets_json:
        rendered = json.dumps(app_context.build_desktop_doctor_debug_assets(report), ensure_ascii=False, indent=2)
    elif args.json:
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
    else:
        rendered = app_context.format_desktop_doctor_report(report)

    if args.output:
        Path(args.output).expanduser().resolve().write_text(rendered + "\n", encoding="utf-8")

    _emit_stdout(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
