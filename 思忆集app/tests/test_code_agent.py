"""Tests for CodeExecutor sandbox and CodeActionEngine."""

from __future__ import annotations

from assistant.code_executor import CodeExecutor, CodeResult, ALLOWED_MODULES


# ── CodeExecutor 沙箱测试 ────────────────────────────────


class TestCodeExecutorBasic:
    def test_print_hello(self):
        exe = CodeExecutor(timeout=5)
        result = exe.execute('print("hello world")')
        assert result.success
        assert "hello world" in result.output

    def test_math_expression(self):
        exe = CodeExecutor(timeout=5)
        result = exe.execute('print(1 + 2 * 3)')
        assert result.success
        assert "7" in result.output.strip()

    def test_multiple_lines(self):
        exe = CodeExecutor(timeout=5)
        code = """
for i in range(3):
    print(f"line {i}")
"""
        result = exe.execute(code)
        assert result.success
        assert "line 0" in result.output
        assert "line 1" in result.output
        assert "line 2" in result.output

    def test_empty_code(self):
        exe = CodeExecutor(timeout=5)
        result = exe.execute("")
        assert result.success


class TestCodeExecutorSafety:
    def test_blocked_eval(self):
        exe = CodeExecutor(timeout=5)
        result = exe.execute('eval("1+1")')
        assert not result.success
        # P0/C3: eval 现在被 _is_dangerous_code 预检拦截
        assert "禁止" in (result.error or "") or "eval" in (result.error or "")

    def test_blocked_exec(self):
        exe = CodeExecutor(timeout=5)
        result = exe.execute('exec("print(1)")')
        assert not result.success

    def test_blocked_import_ctypes(self):
        exe = CodeExecutor(timeout=5)
        result = exe.execute("import ctypes")
        assert not result.success

    def test_blocked_import_socket(self):
        exe = CodeExecutor(timeout=5)
        result = exe.execute("import socket")
        assert not result.success

    def test_allowed_imports(self):
        exe = CodeExecutor(timeout=5)
        for mod in ("json", "re", "pathlib", "datetime", "collections", "math", "random"):
            result = exe.execute(f"import {mod}; print({mod}.__name__)")
            assert result.success, f"import {mod} 失败: {result.error}"

    def test_timeout(self):
        """无限循环应超时。"""
        exe = CodeExecutor(timeout=1)
        result = exe.execute("while True: pass")
        assert result.timed_out
        assert not result.success


class TestCodeExecutorSubprocess:
    def test_blocked_shell(self):
        """shell=True 应该被禁止（subprocess.run 被包装）。"""
        exe = CodeExecutor(timeout=5)
        result = exe.execute("import subprocess; subprocess.run('rm -rf /', shell=True)")
        # 预期要么 safe_subprocess 拒绝 shell=True，要么报错
        assert not result.success


class TestCodeExecutorAudit:
    def test_audit_log(self):
        exe = CodeExecutor(timeout=5)
        exe.execute('print("hello")')
        exe.execute('print("world")')
        log = exe.audit_log
        assert len(log) == 2
        assert log[0]["success"] is True
        assert log[1]["success"] is True

    def test_audit_log_failure(self):
        exe = CodeExecutor(timeout=5)
        exe.execute('print(1/0)')
        log = exe.audit_log
        assert len(log) == 1
        assert log[0]["success"] is False


# ── CodeExecutor 上下文注入 ─────────────────────────────


class TestCodeExecutorContext:
    def test_extra_context_variables(self):
        exe = CodeExecutor(timeout=5)
        result = exe.execute('print(mydata)', context={"mydata": "hello_context"})
        assert result.success
        assert "hello_context" in result.output

    def test_globals_isolation(self):
        """每次 exec 调用不应互相污染命名空间。"""
        exe = CodeExecutor(timeout=5)
        exe.execute('x = 42')
        result = exe.execute('print(x)')
        # x should not exist in this new execution
        assert not result.success
        assert "x" in (result.error or "") or "NameError" in (result.error or "")


# ── CodeResult 测试 ─────────────────────────────────────


class TestCodeResult:
    def test_format_output(self):
        r = CodeResult(output="hello", duration_ms=150)
        text = r.format_for_llm()
        assert "hello" in text
        assert "150ms" in text

    def test_format_error(self):
        r = CodeResult(error="division by zero", duration_ms=10)
        text = r.format_for_llm()
        assert "division by zero" in text

    def test_format_truncation(self):
        r = CodeResult(output="x" * 5000, duration_ms=50)
        text = r.format_for_llm()
        assert len(text) < 4500

    def test_format_timeout(self):
        r = CodeResult(timed_out=True, duration_ms=1000)
        text = r.format_for_llm()
        assert "超时" in text

    def test_success_property(self):
        assert CodeResult(output="ok").success is True
        assert CodeResult(error="err").success is False
        assert CodeResult(timed_out=True).success is False
