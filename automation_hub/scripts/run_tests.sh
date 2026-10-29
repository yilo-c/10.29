#!/usr/bin/env bash
# 模块说明：容器内测试执行脚本。
set -euo pipefail

TARGET="${1:-${WORKSPACE:-/workspace}}"

echo "[run_tests] target: ${TARGET}"
if [ ! -d "${TARGET}" ]; then
  echo "[run_tests] directory not found: ${TARGET}"
  exit 2
fi

cd "${TARGET}"

if [ -f "pyproject.toml" ] || [ -f "pytest.ini" ] || [ -d "tests" ]; then
  python -m pytest -q
else
  echo "[run_tests] no obvious pytest project found (pyproject.toml/pytest.ini/tests)."
  echo "[run_tests] create a tests/ folder or mount a repo into ./workspace."
  exit 3
fi
