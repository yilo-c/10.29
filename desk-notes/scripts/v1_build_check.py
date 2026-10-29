# -*- coding: utf-8 -*-
"""V1 打包链自动化（对应 V1 ①「双击安装当天构建的包可用」）。

用法（desk-notes 仓库内）：
    python scripts/v1_build_check.py                # 构建 + 产物校验
    python scripts/v1_build_check.py --launch       # 追加：便携版启动 + 健康探针（见下）

做什么：
  1. pnpm run electron:build（generate-icons + vite build + electron-builder）
  2. 校验 NSIS 安装包与便携版产物存在、非零、记录 sha256/大小/构建耗时
  3. --launch：启动便携版（免安装路径），等待其 WS 后端 /health 就绪，
     记录冷启动秒数后终止**仅本脚本拉起的进程**（绝不碰已有实例）。

安全边界：--launch 仅在 18765 端口空闲时执行（已有实例在跑则跳过并标注
SKIP——避免文件锁接管误杀用户正在使用的后端）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1] / "packages" / "desktop"
# electron-builder 产物目录 = packages/desktop/package.json directories.output
# = "../../release"（即 desk-notes/release）；DIST 是 vite 前端产物，不是安装包。
DIST = DESKTOP.parent.parent / "release"  # desk-notes/release


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def find_artifacts() -> list[Path]:
    pats = ["desk-notes-setup-*.exe", "*便携版*.exe", "*.exe", "*.yml"]
    seen: list[Path] = []
    for pat in pats:
        seen.extend(DIST.glob(pat))
    return sorted(set(seen), key=lambda p: p.stat().st_mtime, reverse=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--launch", action="store_true", help="构建后启动便携版做健康探针")
    parser.add_argument("--skip-build", action="store_true", help="跳过构建，只校验既有产物")
    args = parser.parse_args()

    report: dict = {"steps": []}

    if not args.skip_build:
        t0 = time.time()
        print("[1/3] electron-builder 构建中（可能 5-15 分钟）...")
        # Windows: pnpm 是 pnpm.cmd，CreateProcess 找不到裸名——用 shutil.which 解析全路径
        pnpm = shutil.which("pnpm")
        if not pnpm:
            print("[1/3] FAILED: pnpm 不在 PATH（需 Node 工具链）")
            return 1
        r = subprocess.run([pnpm, "run", "electron:build"], cwd=str(DESKTOP.parent.parent),
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        build_s = time.time() - t0
        ok = r.returncode == 0
        report["steps"].append({"step": "build", "ok": ok, "seconds": round(build_s, 1)})
        print(f"      构建 {'OK' if ok else 'FAILED'} 用时 {build_s:.0f}s")
        if not ok:
            print(r.stdout[-3000:])
            print(r.stderr[-3000:])
            return 1
    else:
        report["steps"].append({"step": "build", "ok": None, "note": "skipped"})

    print("[2/3] 产物校验...")
    artifacts = find_artifacts()
    installers = [a for a in artifacts if a.name.startswith("desk-notes-setup-")]
    portable = [a for a in artifacts if "便携版" in a.name]
    entry = {"step": "artifacts",
             "installer": [{"name": a.name, "mb": round(a.stat().st_size / 1e6, 1),
                            "sha256": sha256(a)} for a in installers[:1]],
             "portable": [{"name": a.name, "mb": round(a.stat().st_size / 1e6, 1),
                           "sha256": sha256(a)} for a in portable[:1]]}
    report["steps"].append(entry)
    gate = bool(installers) and bool(portable)
    print(f"      安装包: {entry['installer'] or '缺失'}")
    print(f"      便携版: {entry['portable'] or '缺失'}")

    if args.launch and gate:
        port = 18765
        busy = False
        with socket.socket() as s:
            busy = s.connect_ex(("127.0.0.1", port)) == 0
        if busy:
            print("[3/3] 启动探针: SKIP（18765 已被占用——避免接管正在使用的实例）")
            report["steps"].append({"step": "launch", "ok": None, "note": "port busy, skipped"})
        else:
            print("[3/3] 便携版启动 + 健康探针...")
            exe = portable[0]
            proc = subprocess.Popen([str(exe)], cwd=str(exe.parent))
            t0 = time.time()
            ready = False
            while time.time() - t0 < 90:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as resp:
                        if resp.status == 200:
                            ready = True
                            break
                except Exception:
                    time.sleep(1)
            launch_s = time.time() - t0
            print(f"      /health 就绪: {'OK' if ready else '超时'}（{launch_s:.1f}s）")
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
            report["steps"].append({"step": "launch", "ok": ready, "seconds": round(launch_s, 1)})

    out = DIST / "v1-build-check-report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告: {out}")
    return 0 if gate else 1


if __name__ == "__main__":
    sys.exit(main())
