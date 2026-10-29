#!/usr/bin/env python3
"""思忆集桌面应用打包构建脚本。

基于 PyInstaller，使用 --onedir 模式打包。

用法:
    # 从项目根目录运行
    .venv3.11/Scripts/python.exe scripts/build_desktop.py

    # 调试模式 (保留控制台窗口，方便查看日志)
    .venv3.11/Scripts/python.exe scripts/build_desktop.py --debug

    # 清理之前的构建产物
    .venv3.11/Scripts/python.exe scripts/build_desktop.py --clean
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("build_desktop")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPEC_FILE = PROJECT_ROOT / "思忆集app" / "思忆集.spec"
BUILD_DIR = PROJECT_ROOT / "build"
DIST_DIR = PROJECT_ROOT / "dist"

# 虚拟环境中的 Python 和 PyInstaller
VENV_PYTHON = PROJECT_ROOT / ".venv3.11" / "Scripts" / "python.exe"
PYINSTALLER_MODULE = "-m"  # 使用 python -m PyInstaller


def _check_prerequisites() -> bool:
    """检查构建前置条件"""
    if not SPEC_FILE.exists():
        logger.error("spec 文件不存在: %s", SPEC_FILE)
        return False

    if not VENV_PYTHON.exists():
        logger.error("虚拟环境 Python 不存在: %s", VENV_PYTHON)
        logger.error("请先创建 .venv3.11 并安装依赖")
        return False

    # 验证 PyInstaller 可用
    result = subprocess.run(
        [str(VENV_PYTHON), "-c", "import PyInstaller; print(PyInstaller.__version__)"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    if result.returncode != 0:
        logger.error("PyInstaller 未安装: %s", result.stderr.strip())
        logger.error("请运行: pip install pyinstaller")
        return False

    logger.info("PyInstaller 版本: %s", result.stdout.strip())
    return True


def _clean_build():
    """清理之前的构建产物"""
    for d in [BUILD_DIR, DIST_DIR]:
        if d.exists():
            logger.info("清理: %s", d)
            shutil.rmtree(d)
    logger.info("构建产物已清理")


def _run_pyinstaller(debug: bool = False) -> bool:
    """运行 PyInstaller 打包"""
    cmd = [str(VENV_PYTHON), "-m", "PyInstaller", str(SPEC_FILE)]

    if debug:
        logger.info("调试模式: 将修改 spec 启用控制台窗口")
        cmd.extend(["--debug", "all"])

    logger.info("运行: %s", " ".join(cmd))
    logger.info("工作目录: %s", PROJECT_ROOT)

    start = time.perf_counter()
    result = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
    )
    elapsed = time.perf_counter() - start

    # 打印 PyInstaller 输出
    if result.stdout:
        for line in result.stdout.splitlines():
            logger.info("[PyInstaller] %s", line)

    if result.returncode != 0:
        logger.error("打包失败 (exit code %d)", result.returncode)
        if result.stderr:
            for line in result.stderr.splitlines():
                logger.error("[stderr] %s", line)
        return False

    logger.info("打包完成，耗时: %.1f 秒", elapsed)

    # 验证输出目录
    output_dir = DIST_DIR / "思忆集"
    if output_dir.exists():
        exe_path = output_dir / "思忆集.exe"
        if exe_path.exists():
            size_mb = exe_path.stat().st_size / (1024 * 1024)
            logger.info("可执行文件: %s (%.1f MB)", exe_path, size_mb)
        total_size = sum(f.stat().st_size for f in output_dir.rglob("*") if f.is_file()) / (1024 * 1024)
        logger.info("输出目录总大小: %.1f MB", total_size)
    else:
        logger.warning("输出目录未找到: %s", output_dir)

    return True


def verify_build() -> bool:
    """验证构建产物的基本完整性"""
    output_dir = DIST_DIR / "思忆集"
    if not output_dir.exists():
        logger.error("输出目录不存在")
        return False

    exe_path = output_dir / "思忆集.exe"
    if not exe_path.exists():
        logger.error("可执行文件不存在")
        return False

    # 检查关键数据文件
    checks = [
        ("web_dist/index.html", output_dir / "web_dist" / "index.html"),
        ("assets/xiaowen_avatar.png", output_dir / "assets" / "xiaowen_avatar.png"),
    ]

    all_ok = True
    for label, path in checks:
        if path.exists():
            logger.info("  [OK] %s", label)
        else:
            logger.warning("  [缺失] %s", label)
            all_ok = False

    # 检查关键的 DLL/依赖
    import re
    critical_dlls = [
        r"Qt6WebEngine",
        r"Qt6Core",
        r"Qt6Gui",
        r"Qt6Widgets",
        r"Qt6Network",
        r"python3\.\d+\.dll",
    ]
    found_files = [f.name for f in output_dir.rglob("*") if f.is_file()]
    for pattern in critical_dlls:
        if any(re.search(pattern, name) for name in found_files):
            logger.info("  [OK] 匹配: %s", pattern)
        else:
            logger.warning("  [缺失] 未找到匹配: %s", pattern)

    logger.info("验证完成 (总文件数: %d)", len(found_files))
    return all_ok


def main():
    parser = argparse.ArgumentParser(description="思忆集桌面应用打包脚本")
    parser.add_argument("--debug", action="store_true", help="调试模式 (保留控制台 + 详细日志)")
    parser.add_argument("--clean", action="store_true", help="先清理之前的构建产物")
    parser.add_argument("--no-verify", action="store_true", help="构建后跳过验证")
    args = parser.parse_args()

    if not _check_prerequisites():
        sys.exit(1)

    if args.clean:
        _clean_build()

    logger.info("=" * 50)
    logger.info("思忆集 桌面端打包开始")
    logger.info("Python: %s", VENV_PYTHON)
    logger.info("Spec:   %s", SPEC_FILE)
    logger.info("输出:   %s", DIST_DIR / "思忆集")
    logger.info("=" * 50)

    success = _run_pyinstaller(debug=args.debug)
    if not success:
        logger.error("打包失败")
        sys.exit(1)

    if not args.no_verify:
        logger.info("验证构建产物...")
        verify_build()

    logger.info("=" * 50)
    logger.info("打包完成! 输出目录: %s", DIST_DIR / "思忆集")
    logger.info("运行: %s", DIST_DIR / "思忆集" / "思忆集.exe")
    logger.info("=" * 50)


if __name__ == "__main__":
    main()
