"""File operations capability domain.

Extracted from capability_router.py — FileCapabilityDomain (was capability_router_files.py).
"""
from __future__ import annotations

import glob as glob_mod
import logging
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from core.path_safety import is_sensitive_path

logger = logging.getLogger("siyi.assistant.capability.file_ops")

# M2.3 QUA-4：文件快照内容上限（与写前快照 [:65536] 截断一致），
# 超过即 truncated 标记，回滚侧据此拒绝「半截写回」。
FILE_SNAPSHOT_CHARS = 65536

# ── C-1 执行器收敛 步骤 2：从 B 套（services/react_executor.py）迁入的三样安全策略 ──
# 与 B 套逐字一致的拒绝语义（拒读/拒拷/拒写）。
_READ_SENSITIVE_REASON = "禁止读取敏感文件（密钥/凭据/系统配置）"
_COPY_SENSITIVE_REASON = "禁止复制敏感文件（密钥/凭据/系统配置）"
_WRITE_SENSITIVE_REASON = "禁止写入敏感文件（密钥/凭据/系统配置）"
# env 门控 + agent_outputs 兜底夹取。
_ALLOW_FILE_WRITE_ENV = "SIYI_AGENT_ALLOW_FILE_WRITE"
_AGENT_OUTPUTS_DIRNAME = "agent_outputs"
_NO_DATA_STORE_REASON = "data_store 不可用，拒绝写入"
_TRAVERSAL_REASON = "禁止路径穿越"


class FileCapabilityDomain:
    """File read/write/download capability domain."""

    def __init__(self, router) -> None:
        self.router = router

    def _snapshot_file_content(self, target: Path) -> dict[str, Any] | None:
        """构建文件写前快照：内容截 FILE_SNAPSHOT_CHARS 并携带 size/truncated 元数据。

        QUA-4：file 型回滚靠 truncated 标记判定「快照不可完整恢复」，拒绝半截写回。
        """
        if not target.exists() or not target.is_file():
            return None
        try:
            raw = target.read_text(encoding="utf-8", errors="replace")
        except Exception:
            logger.warning("failed to read before-snapshot for file %s", target)
            return None
        capped = raw[:FILE_SNAPSHOT_CHARS]
        return {
            "content": capped,
            "size": len(raw),
            "truncated": len(capped) < len(raw),
        }

    def file_read(self, payload: dict[str, Any]) -> dict[str, Any]:
        path = str(payload.get("path") or "").strip()
        if not path:
            return {"success": False, "reason": "no_path"}
        if "*" in path or "?" in path:
            try:
                matches = self._resolve_glob_matches(path)[:200]
            except ValueError as exc:
                return {"success": False, "reason": str(exc), "pattern": path}
            return {"success": True, "type": "glob", "pattern": path, "matches": [str(item) for item in matches], "count": len(matches)}
        try:
            target = self._resolve_read_path(path)
        except ValueError as exc:
            return {"success": False, "reason": str(exc), "path": path}
        # ① 敏感黑名单（C-1 迁入 B 套策略，先于存在性判定，与 B 套 file.read 逐字一致）
        if is_sensitive_path(target):
            logger.warning("file.read 拒绝读取敏感路径: %s", path)
            return {"success": False, "reason": _READ_SENSITIVE_REASON, "path": path}
        if not target.exists():
            return {"success": False, "reason": "path_not_found", "path": str(target)}
        recursive = str(payload.get("recursive") or "false").lower() in ("true", "1", "yes")
        max_depth = max(1, min(int(payload.get("depth") or 3), 5))
        preview = str(payload.get("preview") or "false").lower() in ("true", "1", "yes")
        if target.is_dir():
            try:
                tree = self._list_dir_recursive(target, max_depth if recursive else 1)
            except PermissionError:
                return {"success": False, "reason": "permission_denied", "path": str(target)}
            result: dict[str, Any] = {
                "success": True,
                "type": "directory",
                "path": str(target),
                "tree": tree,
                "count": sum(1 for _ in self._flatten_tree(tree)),
            }
            if preview:
                previews = {}
                for entry_path in self._flatten_tree(tree):
                    candidate = Path(entry_path)
                    # ① 目录预览同样跳过敏感子文件（防绕过 file.read 黑名单）
                    if is_sensitive_path(candidate):
                        continue
                    if candidate.is_file():
                        try:
                            size = candidate.stat().st_size
                            if size <= 4096:
                                previews[str(candidate)] = candidate.read_text(encoding="utf-8", errors="replace")
                        except Exception:
                            logger.warning("failed to read preview for %s", candidate)
                    if len(previews) >= 20:
                        break
                result["previews"] = previews
            return result
        try:
            size = target.stat().st_size
            if size > 256 * 1024:
                return {"success": False, "reason": "file_too_large", "size": size, "limit": 262144}
            encoding = str(payload.get("encoding") or "utf-8").strip()
            content = target.read_text(encoding=encoding, errors="replace")
            return {"success": True, "type": "file", "path": str(target), "size": size, "content": content}
        except PermissionError:
            return {"success": False, "reason": "permission_denied", "path": str(target)}
        except Exception as exc:
            return {"success": False, "reason": str(exc), "path": str(target)}

    def file_write(self, payload: dict[str, Any]) -> dict[str, Any]:
        path = str(payload.get("path") or "").strip()
        content = str(payload.get("content") or "")
        mode = str(payload.get("mode") or "overwrite").strip()
        if not path:
            return {"success": False, "reason": "no_path"}
        # ① 敏感黑名单（C-1 迁入 B 套策略，先于白名单/env 判定）
        if is_sensitive_path(Path(os.path.expanduser(path))):
            logger.warning("file.write 拒绝写入敏感路径: %s", path)
            return {"success": False, "reason": _WRITE_SENSITIVE_REASON, "path": path}
        # ②③ env 门控 + agent_outputs 兜底夹取：**叠加**在现有 write_roots 白名单之上，
        # 两套语义正交：先走白名单判定；白名单外且 env 未放开→夹 agent_outputs（而非直接拒），
        # env 放开→按 B 套逃生阀语义写到原始路径。缺 base_dir → 拒。
        allow_outside = os.environ.get(_ALLOW_FILE_WRITE_ENV) == "1"
        try:
            target = self._resolve_write_path(path)
        except ValueError as exc:
            reason = str(exc)
            if reason == "workspace_write_blocked":
                # 现有语义保持：workspace 根写禁与白名单同级，不经 env/夹取放行
                return {"success": False, "reason": reason, "path": path}
            # path_outside_allowed_root：
            if allow_outside:
                target = self._resolve_free_write_target(path)
            else:
                target, clamp_err = self._clamp_to_agent_outputs(path)
                if clamp_err is not None:
                    return {"success": False, "reason": clamp_err, "path": path}
        before_snapshot = self._snapshot_file_content(target)
        write_mode = "a" if mode == "append" else "w"
        target.parent.mkdir(parents=True, exist_ok=True)
        if self.router.write_guard is not None:
            with self.router.write_guard.protect("file", str(target), actor="assistant", action_id="file.write", operation=mode, before_snapshot=before_snapshot, snapshot_str_limit=FILE_SNAPSHOT_CHARS) as guard:
                target.write_text(content, encoding="utf-8") if write_mode == "w" else self._append_text(target, content)
                guard.set_after({"path": str(target), "size": len(content), "mode": mode})
        else:
            target.write_text(content, encoding="utf-8") if write_mode == "w" else self._append_text(target, content)
        return {"success": True, "path": str(target), "bytes_written": len(content), "mode": mode}

    def web_download(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = str(payload.get("url") or "").strip()
        if not url:
            return {"success": False, "reason": "no_url"}
        parsed = urlparse(url)
        if parsed.scheme not in {"https", "http"}:
            return {"success": False, "reason": "unsupported_url_scheme"}
        if parsed.scheme == "http" and (parsed.hostname or "").lower() not in {"127.0.0.1", "localhost"}:
            return {"success": False, "reason": "http_download_not_allowed"}
        save_path = str(payload.get("save_path") or "").strip()
        try:
            import urllib.request

            resolved_path = self.resolve_download_path(parsed, save_path)
            resolved_path.parent.mkdir(parents=True, exist_ok=True)
            # M2.3 QUA-4：web_download 纳入写前快照审计（action_id=file.download），
            # 新建文件回滚即删除；覆盖已存在文件则快照旧内容可恢复
            before_snapshot = self._snapshot_file_content(resolved_path)
            if self.router.write_guard is not None:
                with self.router.write_guard.protect(
                    "file", str(resolved_path), actor="assistant", action_id="file.download",
                    operation="download", before_snapshot=before_snapshot,
                    snapshot_str_limit=FILE_SNAPSHOT_CHARS,
                ) as guard:
                    urllib.request.urlretrieve(url, str(resolved_path))
                    guard.set_after({"path": str(resolved_path), "url": url})
            else:
                urllib.request.urlretrieve(url, str(resolved_path))
            return {"success": True, "saved_to": str(resolved_path), "url": url}
        except Exception as exc:
            return {"success": False, "reason": str(exc)}

    def download_root(self) -> Path:
        return (self._data_root() / self.router._DEFAULT_DOWNLOAD_DIRNAME).resolve()

    def file_find(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Search files by name/type pattern across readable roots.

        Payload:
          pattern: str — filename pattern (glob), e.g. "*.txt", "report*"
          root: str — optional root directory (default: workspace root)
          max_results: int — max results (default 50, max 200)
          type: str — optional filter: "file" / "dir"
        """
        pattern = str(payload.get("pattern") or "").strip()
        if not pattern:
            return {"success": False, "reason": "no_pattern"}

        root_path_str = str(payload.get("root") or "").strip()
        max_results = max(1, min(int(payload.get("max_results") or 50), 200))
        # 兼容 entry_type（新参数名，避免与 JSON Schema 关键字 "type" 冲突）
        # 与 type（历史参数名）。LLM 经 tool calling 时两种都可能传。
        type_filter = str(payload.get("entry_type") or payload.get("type") or "").strip().lower()

        try:
            if root_path_str:
                search_root = Path(os.path.expanduser(root_path_str)).resolve(strict=False)
            else:
                search_root = self._workspace_root()

            if not search_root.exists():
                return {"success": False, "reason": f"root not found: {search_root}"}

            matches = []
            try:
                for item in search_root.rglob(pattern):
                    try:
                        resolved = item.resolve(strict=False)
                        self._assert_within_roots(resolved, self._read_roots())
                    except ValueError:
                        continue
                    if type_filter == "file" and not item.is_file():
                        continue
                    if type_filter == "dir" and not item.is_dir():
                        continue
                    try:
                        stat = item.stat()
                        entry = {
                            "path": str(resolved),
                            "name": item.name,
                            "type": "dir" if item.is_dir() else "file",
                            "size": stat.st_size if item.is_file() else 0,
                            "modified": stat.st_mtime,
                        }
                        matches.append(entry)
                        if len(matches) >= max_results:
                            break
                    except (OSError, PermissionError):
                        continue
            except PermissionError:
                pass

            return {
                "success": True,
                "count": len(matches),
                "matches": matches,
                "pattern": pattern,
                "root": str(search_root),
            }
        except Exception as exc:
            logger.warning("file.find failed: %s", exc)
            return {"success": False, "reason": str(exc)}

    def file_grep(self, payload: dict[str, Any]) -> dict[str, Any]:
        """在文件内容中按正则搜索（对标 Codex 的 grep 能力）。

        Payload:
          pattern: str — 正则表达式（必填）
          path: str — 单个文件或搜索根目录（可选，默认 workspace 根）
          glob: str — 文件名过滤 glob，如 "*.py"（可选）
          max_results: int — 最多命中条数（默认 50，上限 100）
        """
        import fnmatch
        import re as _re

        pattern = str(payload.get("pattern") or "").strip()
        if not pattern:
            return {"success": False, "reason": "no_pattern"}
        try:
            regex = _re.compile(pattern)
        except _re.error as exc:
            return {"success": False, "reason": f"invalid_regex: {exc}"}

        max_results = max(1, min(int(payload.get("max_results") or 50), 100))
        glob_filter = str(payload.get("glob") or "").strip()
        path_str = str(payload.get("path") or "").strip()

        try:
            if path_str:
                base = Path(os.path.expanduser(path_str)).resolve(strict=False)
            else:
                base = self._workspace_root()
            if not base.exists():
                return {"success": False, "reason": f"path not found: {base}"}

            if base.is_file():
                candidates = [base]
            else:
                candidates = []
                try:
                    for item in base.rglob("*"):
                        if len(candidates) >= 2000:
                            break
                        if not item.is_file():
                            continue
                        if glob_filter and not fnmatch.fnmatch(item.name, glob_filter):
                            continue
                        candidates.append(item)
                except PermissionError:
                    pass

            matches: list[dict[str, Any]] = []
            for fp in candidates:
                try:
                    resolved = fp.resolve(strict=False)
                    self._assert_within_roots(resolved, self._read_roots())
                except ValueError:
                    continue
                try:
                    if fp.stat().st_size > 2 * 1024 * 1024:
                        continue  # 跳过大文件
                    raw = fp.read_bytes()
                    if b"\x00" in raw[:8192]:
                        continue  # 跳过二进制
                    text = raw.decode("utf-8", errors="replace")
                except (OSError, PermissionError):
                    continue
                for idx, line in enumerate(text.splitlines(), start=1):
                    if regex.search(line):
                        matches.append({
                            "path": str(fp),
                            "line": idx,
                            "text": line.strip()[:300],
                        })
                        if len(matches) >= max_results:
                            break
                if len(matches) >= max_results:
                    break

            return {
                "success": True,
                "count": len(matches),
                "matches": matches,
                "pattern": pattern,
                "truncated": len(matches) >= max_results,
            }
        except Exception as exc:
            logger.warning("file.grep failed: %s", exc)
            return {"success": False, "reason": str(exc)}

    def file_edit(self, payload: dict[str, Any]) -> dict[str, Any]:
        """局部替换编辑（对标 Codex 的 patch 能力）。

        Payload:
          path: str — 文件路径（必填）
          old_text: str — 要被替换的原文（必填，默认要求全文唯一）
          new_text: str — 替换后的新文本（必填，可为空串表示删除）
          replace_all: bool — 为 true 时替换全部匹配（默认 false）
        """
        path = str(payload.get("path") or "").strip()
        old_text = str(payload.get("old_text") or "")
        new_text = str(payload.get("new_text") or "")
        replace_all = bool(payload.get("replace_all", False))
        if not path:
            return {"success": False, "reason": "no_path"}
        if not old_text:
            return {"success": False, "reason": "no_old_text"}
        if old_text == new_text:
            return {"success": False, "reason": "old_text_equals_new_text"}
        try:
            target = self._resolve_write_path(path)
        except ValueError as exc:
            return {"success": False, "reason": str(exc), "path": path}
        if not target.exists() or not target.is_file():
            return {"success": False, "reason": "file_not_found", "path": str(target)}

        # 编码探测：utf-8 优先，失败回退 gbk（中文 Windows 常见）
        encoding = "utf-8"
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = target.read_text(encoding="gbk")
                encoding = "gbk"
            except UnicodeDecodeError:
                return {"success": False, "reason": "unsupported_encoding"}

        occurrences = content.count(old_text)
        if occurrences == 0:
            return {"success": False, "reason": "old_text_not_found", "hint": "请先用 file.read 查看文件实际内容后重试"}
        if occurrences > 1 and not replace_all:
            return {
                "success": False,
                "reason": f"old_text_ambiguous: {occurrences} 处匹配",
                "hint": "请提供更长的上下文使 old_text 唯一，或设 replace_all=true",
            }

        new_content = content.replace(old_text, new_text) if replace_all else content.replace(old_text, new_text, 1)
        # QUA-4：快照携带 size/truncated 元数据，回滚可判定「是否可完整恢复」
        before_snapshot = {
            "content": content[:FILE_SNAPSHOT_CHARS],
            "size": len(content),
            "truncated": len(content) > FILE_SNAPSHOT_CHARS,
        }
        replaced = occurrences if replace_all else 1
        if self.router.write_guard is not None:
            with self.router.write_guard.protect("file", str(target), actor="assistant", action_id="file.edit", operation="edit", before_snapshot=before_snapshot, snapshot_str_limit=FILE_SNAPSHOT_CHARS) as guard:
                target.write_text(new_content, encoding=encoding)
                guard.set_after({"path": str(target), "replaced": replaced})
        else:
            target.write_text(new_content, encoding=encoding)
        return {"success": True, "path": str(target), "replaced": replaced}

    def file_copy(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Copy or move a file.

        Payload:
          source: str — source path
          dest: str — destination path
          move: bool — if True, move instead of copy (default false)
          overwrite: bool — overwrite destination if exists (default false)
        """
        import shutil

        source = str(payload.get("source") or "").strip()
        dest = str(payload.get("dest") or "").strip()
        is_move = bool(payload.get("move", False))
        overwrite = bool(payload.get("overwrite", False))

        if not source or not dest:
            return {"success": False, "reason": "source and dest required"}

        try:
            src_path = self._resolve_read_path(source)
            # ① 敏感黑名单（C-1 迁入 B 套策略：防通过复制绕过 file.read 限制，逐字一致）
            if is_sensitive_path(src_path):
                logger.warning("file.copy 拒绝复制敏感源路径: %s", source)
                return {"success": False, "reason": _COPY_SENSITIVE_REASON, "source": source}
            if not src_path.exists():
                return {"success": False, "reason": f"source not found: {src_path}"}

            # M2.1 SEC-3：dest 与 file_write 同走写边界校验（越界拒绝，防复制外泄）
            dst_path = self._resolve_write_path(dest)

            if dst_path.exists() and not overwrite:
                return {"success": False, "reason": f"destination exists: {dst_path} (set overwrite=true)"}

            dst_path.parent.mkdir(parents=True, exist_ok=True)

            # M2.3 QUA-4：快照描述 dest 写前状态——overwrite 时含 dest 旧内容，
            # 回滚才能恢复 dest（修复前只快照了 source，回滚拿不到 dest 旧内容）
            before_snapshot = self._snapshot_file_content(dst_path)

            op_name = "move" if is_move else "copy"
            if self.router.write_guard is not None:
                with self.router.write_guard.protect("file", str(dst_path), actor="assistant", action_id=f"file.{op_name}", operation=op_name, before_snapshot=before_snapshot, snapshot_str_limit=FILE_SNAPSHOT_CHARS) as guard:
                    if is_move:
                        shutil.move(str(src_path), str(dst_path))
                    else:
                        shutil.copy2(str(src_path), str(dst_path))
                    guard.set_after({"source": str(src_path), "dest": str(dst_path)})
            else:
                if is_move:
                    shutil.move(str(src_path), str(dst_path))
                else:
                    shutil.copy2(str(src_path), str(dst_path))

            return {
                "success": True,
                "operation": op_name,
                "source": str(src_path),
                "dest": str(dst_path),
            }
        except Exception as exc:
            logger.warning("file.%s failed: %s", "move" if is_move else "copy", exc)
            return {"success": False, "reason": str(exc)}

    def file_delete(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Delete a file or empty directory within allowed roots.

        路径经 _resolve_read_path 白名单保护（workspace / data / download / Desktop），
        与 file_read 同级约束。非空目录拒绝删除；删除前写入审计快照（可回滚）。
        """
        path = str(payload.get("path") or "").strip()
        if not path:
            return {"success": False, "reason": "no_path"}
        try:
            target = self._resolve_read_path(path)
        except ValueError as exc:
            return {"success": False, "reason": str(exc), "path": path}
        if not target.exists():
            return {"success": False, "reason": "path_not_found", "path": str(target)}
        if target.is_dir():
            try:
                if any(target.iterdir()):
                    return {"success": False, "reason": "directory_not_empty", "path": str(target)}
            except OSError:
                return {"success": False, "reason": "permission_denied", "path": str(target)}
        try:
            before_snapshot = self._snapshot_file_content(target) if target.is_file() else None
            if self.router.write_guard is not None:
                with self.router.write_guard.protect(
                    "file", str(target), actor="assistant", action_id="file.delete",
                    operation="delete", before_snapshot=before_snapshot,
                    snapshot_str_limit=FILE_SNAPSHOT_CHARS,
                ) as guard:
                    if target.is_dir():
                        target.rmdir()
                    else:
                        target.unlink()
                    guard.set_after({"path": str(target), "deleted": True})
            else:
                if target.is_dir():
                    target.rmdir()
                else:
                    target.unlink()
            return {"success": True, "path": str(target), "deleted": True}
        except PermissionError:
            return {"success": False, "reason": "permission_denied", "path": str(target)}
        except OSError as exc:
            return {"success": False, "reason": str(exc), "path": str(target)}

    def resolve_download_path(self, parsed_url, save_path: str) -> Path:
        download_root = self.download_root()
        if save_path:
            candidate = Path(save_path)
            if any(part == ".." for part in candidate.parts):
                raise ValueError("path_traversal_blocked")
            resolved = candidate.resolve(strict=False) if candidate.is_absolute() else (download_root / candidate).resolve(strict=False)
        else:
            filename = self.router._safe_download_filename(Path(parsed_url.path).name or "download")
            resolved = (download_root / filename).resolve(strict=False)
        self._assert_within_roots(resolved, [download_root])
        return resolved

    def _workspace_root(self) -> Path:
        return Path(self.router.shell_execution.workspace_root).resolve()

    def _data_root(self) -> Path:
        base_dir = getattr(self.router.data_store, "base_dir", None)
        if base_dir:
            return Path(base_dir).resolve()
        return (self._workspace_root() / ".siyi_runtime").resolve()

    def _read_roots(self) -> list[Path]:
        roots = [self._workspace_root(), self._data_root(), self.download_root()]
        # 添加桌面目录，让 AI 可以读取用户桌面文件
        desktop = Path.home() / "Desktop"
        if desktop.exists():
            roots.append(desktop)
        deduped: list[Path] = []
        for root in roots:
            resolved = root.resolve(strict=False)
            if resolved not in deduped:
                deduped.append(resolved)
        return deduped

    def _write_roots(self) -> list[Path]:
        roots = [self._data_root(), self.download_root()]
        deduped: list[Path] = []
        for root in roots:
            resolved = root.resolve(strict=False)
            if resolved not in deduped:
                deduped.append(resolved)
        return deduped

    def _resolve_read_path(self, raw_path: str) -> Path:
        candidate = Path(os.path.expanduser(raw_path))
        if not candidate.is_absolute():
            candidate = self._workspace_root() / candidate
        resolved = candidate.resolve(strict=False)
        self._assert_within_roots(resolved, self._read_roots())
        return resolved

    def _resolve_write_path(self, raw_path: str) -> Path:
        candidate = Path(os.path.expanduser(raw_path))
        if not candidate.is_absolute():
            candidate = self._data_root() / candidate
        resolved = candidate.resolve(strict=False)
        self._assert_within_roots(resolved, self._write_roots())
        workspace_root = self._workspace_root()
        if resolved == workspace_root or resolved.is_relative_to(workspace_root):
            raise ValueError("workspace_write_blocked")
        return resolved

    def _clamp_to_agent_outputs(self, raw_path: str) -> tuple[Path | None, str | None]:
        """③ agent_outputs 兜底夹取（C-1 迁入 B 套）：取 basename 落到 data/agent_outputs。

        缺 base_dir → (None, 拒写原因)；basename 穿越（abspath 落在 safe_root 外）→ (None, 拒)。
        与 B 套 react_executor file.write 夹取语义一致（basename + abspath + 前缀校验）。
        """
        base_dir = getattr(self.router.data_store, "base_dir", None)
        if not base_dir:
            return None, _NO_DATA_STORE_REASON
        safe_root = os.path.abspath(os.path.join(str(base_dir), _AGENT_OUTPUTS_DIRNAME))
        os.makedirs(safe_root, exist_ok=True)
        name = raw_path.strip().replace("\\", "/").split("/")[-1] or "file"
        write_path = os.path.abspath(os.path.join(safe_root, name))
        if os.path.commonpath([write_path, safe_root]) != safe_root:
            return None, _TRAVERSAL_REASON
        return Path(write_path), None

    def _resolve_free_write_target(self, raw_path: str) -> Path:
        """② env 放开（SIYI_AGENT_ALLOW_FILE_WRITE=1）时的逃生阀：直接解析到原始请求路径。

        绝对路径直接 resolve；相对路径拼到 data_root。仅由 file_write 在
        白名单外且 env 已放开时调用（黑名单已在上游拦住）。
        """
        candidate = Path(os.path.expanduser(raw_path))
        if not candidate.is_absolute():
            candidate = self._data_root() / candidate
        return candidate.resolve(strict=False)

    def _resolve_glob_matches(self, pattern: str) -> list[Path]:
        candidate = Path(os.path.expanduser(pattern))
        if candidate.is_absolute():
            expanded_pattern = str(candidate)
        else:
            expanded_pattern = str(self._workspace_root() / candidate)
        matches = []
        for item in glob_mod.glob(expanded_pattern, recursive=True):
            try:
                resolved = Path(item).resolve(strict=False)
                self._assert_within_roots(resolved, self._read_roots())
            except ValueError:
                continue
            matches.append(resolved)
        return matches

    @staticmethod
    def _append_text(target: Path, content: str) -> None:
        with target.open("a", encoding="utf-8") as file_handle:
            file_handle.write(content)

    @staticmethod
    def _assert_within_roots(target: Path, roots: list[Path]) -> None:
        for root in roots:
            if target == root or target.is_relative_to(root):
                return
        raise ValueError("path_outside_allowed_root")

    def _list_dir_recursive(self, root: Path, max_depth: int, current_depth: int = 0) -> list[dict[str, Any]]:
        entries = []
        try:
            for child in sorted(root.iterdir(), key=lambda item: item.name)[:200]:
                try:
                    resolved = child.resolve(strict=False)
                    self._assert_within_roots(resolved, self._read_roots())
                except ValueError:
                    continue
                entry: dict[str, Any] = {"name": child.name, "path": str(resolved)}
                if child.is_dir():
                    entry["type"] = "dir"
                    if current_depth < max_depth - 1:
                        entry["children"] = self._list_dir_recursive(resolved, max_depth, current_depth + 1)
                    else:
                        entry["children"] = "..."
                else:
                    entry["type"] = "file"
                    try:
                        entry["size"] = child.stat().st_size
                    except OSError:
                        entry["size"] = -1
                entries.append(entry)
        except PermissionError:
            raise
        return entries

    @staticmethod
    def _flatten_tree(tree: list[dict[str, Any]]) -> Iterator[str]:
        for entry in tree:
            yield entry.get("path", "")
            children = entry.get("children")
            if isinstance(children, list):
                yield from FileCapabilityDomain._flatten_tree(children)
