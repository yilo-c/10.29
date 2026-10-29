import argparse
from datetime import datetime


def _resolve_version(channel: str, build: int) -> str:
    now = datetime.now()
    if channel == "stable":
        year, week, _ = now.isocalendar()
        return f"{year}.{week}.0"
    return f"{now.year}.{now.month:02d}{now.day:02d}-dev.{max(1, build)}"


def _load_text(path: str, fallback: str) -> str:
    if not path:
        return fallback
    try:
        with open(path, "r", encoding="utf-8") as file_handle:
            content = file_handle.read().strip()
            return content or fallback
    except OSError:
        return fallback


def main() -> int:
    parser = argparse.ArgumentParser(description="生成发布报告（功能变化 + 已知问题 + 回滚步骤）")
    parser.add_argument("--channel", choices=["stable", "dev"], default="dev")
    parser.add_argument("--build", type=int, default=1)
    parser.add_argument("--feature-changes-file", default="")
    parser.add_argument("--known-issues-file", default="")
    parser.add_argument("--rollback-steps-file", default="")
    parser.add_argument("--rollback-version", default="上一个稳定版本")
    parser.add_argument("--output", default="release_report.md")
    args = parser.parse_args()

    release_version = _resolve_version(args.channel, args.build)
    release_date = datetime.now().strftime("%Y-%m-%d")
    feature_changes = _load_text(args.feature_changes_file, "- 本次未记录功能变化。")
    known_issues = _load_text(args.known_issues_file, "- 当前未登记新的已知问题。")
    rollback_steps = _load_text(
        args.rollback_steps_file,
        "1. 暂停灰度扩容。\n2. 回退到上一个可用版本。\n3. 恢复最近快照并重启。\n4. 验证关键链路。",
    )

    output = [
        f"# 发布报告 {release_version} ({args.channel})",
        "",
        f"发布日期: {release_date}",
        "",
        "## 功能变化",
        feature_changes,
        "",
        "## 已知问题",
        known_issues,
        "",
        "## 回滚步骤",
        rollback_steps.replace("{rollback_version}", args.rollback_version),
    ]

    with open(args.output, "w", encoding="utf-8") as file_handle:
        file_handle.write("\n".join(output).strip() + "\n")

    print(f"RELEASE_REPORT_OK {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
