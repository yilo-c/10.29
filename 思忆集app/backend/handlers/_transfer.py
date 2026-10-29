"""跨端文本传递 — transfer RPCs。

鉴权模型（PH-A1, 2026-09-17）：单一凭证 —— 传输 RPC 复用 WS 握手 auth_token，
不另设配对凭证（原 settings.transfer_token 生成/持久化子系统全仓零消费，已删除）。
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
import time
from typing import Any

from backend.handlers._list_guard import cap_list, clamp_limit

logger = logging.getLogger("siyi.backend.handlers.transfer")

MAX_HISTORY = 50
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
# B-06: transfer.send 内嵌 file 的独立小上限（1MB base64 ≈ 750KB 原始）。
# 若沿用 50MB：50 条历史最坏 ~2.5GB，每次 send 全量 load→dump→fsync 卡顿。
# 大文件走 transfer.upload（transfer_files/ 目录 + 引用）；send 内嵌只收小附件。
# 超限明确拒绝（不静默截断 —— P2-13 已证实截断导致文件损坏）。
MAX_EMBEDDED_FILE = 1 * 1024 * 1024

# PH-F2（2026-09-17）：盘符形态（C:\foo / C:/foo）。
_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:")


def _is_drive_or_absolute(relative_path: str) -> bool:
    """判断路径是否为盘符 / 绝对路径形态（PH-F2）。

    upload/download/delete 的历史实现会对入参做 basename 收敛：``C:\\foo\\win.ini``
    被收敛成 ``win.ini`` 后当普通文件收下 —— 用户以为上传/读到了那个绝对位置，
    实际落在 transfer_files 根且读不回。此处在收敛**之前**显式识别以便拒绝。
    """
    text = str(relative_path or "").replace("\\", "/").strip()
    if not text:
        return False
    if _DRIVE_PATH_RE.match(text):
        return True
    return text.startswith("/")  # POSIX 绝对路径 / UNC 前缀


def _resolve_file_store_path(base_dir: str, relative_path: str = "") -> str | None:
    """安全拼接文件存储路径，返回绝对路径；检测到路径穿越时返回 None。

    PH-F2（2026-09-17）：盘符绝对路径一律显式拒绝（返回 None），不再依赖平台
    差异（Windows 上 os.path.join 的 drive 语义会逃逸 base；POSIX 上会被误当
    成 base 下的 "C:" 目录）。
    """
    base = os.path.abspath(base_dir)
    rel = str(relative_path or "")
    # 处理 Windows 反斜杠与空字节
    rel = rel.replace("\\", "/").replace("\x00", "")
    # 去除开头多余的 /
    rel = rel.lstrip("/")
    if _DRIVE_PATH_RE.match(rel):
        return None
    target = os.path.abspath(os.path.join(base, rel))
    # 阻止 .. 穿越：target 必须在 base 内部（允许 target == base）
    base_abs = os.path.abspath(base)
    if target != base_abs and not target.startswith(base_abs + os.sep):
        return None
    return target


def _classify_by_rules(text: str) -> dict[str, Any]:
    """根据关键词/内容对跨端传送文本做简单分类。"""
    if not text:
        return {"category": None, "action": None}

    # URL -> 稍后读
    if "http://" in text or "https://" in text:
        return {"category": "link", "action": "save_reading"}

    # base64 图片 -> 笔记
    if "data:image/" in text:
        return {"category": "image", "action": "create_note"}

    # 任务关键词
    if any(kw in text for kw in ("明天", "别忘了")):
        return {"category": "task", "action": "create_task"}

    # 偏好/习惯关键词
    if any(kw in text for kw in ("喜欢", "习惯")):
        return {"category": "preference", "action": "save_memory"}

    # 长文本 -> 笔记
    if len(text) > 100:
        return {"category": "note", "action": "create_note"}

    return {"category": None, "action": None}


def _check_auth(**kw: Any) -> str | None:
    """Check authorization via kwargs. Returns error message or None if OK.

    单一凭证模型（PH-A1, 2026-09-17）：传输 RPC 复用 WS 握手 auth_token，不另设
    配对凭证 —— 原「生成 token → 持久化 settings.transfer_token → 文件回退」子系统
    全仓零消费（前后端均无调用点，settings 里的值也无人校验），已整段删除。
    仅当运维显式注入 SIYI_TRANSFER_KEY（VPS/公网部署）时，额外要求 ``_token``
    与之恒时匹配；未配置（默认 LAN，走已鉴权的 WS 通道）不要求额外凭证。
    """
    token = kw.get("_token", "") or ""
    # Not configured → allow all (LAN mode)
    env_key = os.environ.get("SIYI_TRANSFER_KEY", "")
    if not env_key:
        return None
    if not token:
        return "auth required (missing _token param)"
    # 七维-安C7（2026-08-29）：恒时比较替代 != 明文比较（计时侧信道可逐字节
    # 探测 token）。两侧先 UTF-8 编码：compare_digest 的 str/str 形式遇非 ASCII
    # 会抛 TypeError，bytes 形式对任意 unicode 输入安全且语义不变（精确匹配）。
    if not hmac.compare_digest(token.encode("utf-8"), env_key.encode("utf-8")):
        return "auth failed (invalid _token)"
    return None


def _get_transfer_path(context) -> str:
    ds = getattr(context, "local_data_store", None)
    if ds and hasattr(ds, "base_dir"):
        return os.path.join(ds.base_dir, "transfer_history.json")
    fallback = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data")
    return os.path.join(os.path.abspath(fallback), "transfer_history.json")


def register_transfer(registry, context) -> None:
    """Register transfer-related JSON-RPC methods.

    PH-A1（2026-09-17）：此处原先会 ``_get_or_create_token(context)`` 生成并持久化
    一个 settings.transfer_token 配对凭证。该 token 全仓零消费且无人校验（真鉴权
    只看 env SIYI_TRANSFER_KEY），属死孤岛 —— 已删除生成/持久化路径。
    """

    # ── transfer.token_info (unauthenticated — returns auth model) ──
    # 七维-安C7（2026-08-29）：移除 token_prefix（原返回 env_key 前 4 字符，
    # 向未鉴权调用方泄露 token 前缀）。有意 RPC 契约变更：全仓生产消费方为零
    # （desk-notes methods.ts:44 仅映射方法名，手机端无 token_prefix 引用；
    # test_integration_transfer.py 为 OR 断言不受影响）。
    # PH-A1（2026-09-17）：如实说明单一凭证模型，不再暗示存在独立配对凭证。
    async def transfer_token_info(**kwargs: Any) -> dict[str, Any]:
        env_key = os.environ.get("SIYI_TRANSFER_KEY", "")
        return {
            "auth_model": "ws_auth_token",
            "token_required": bool(env_key),
            "mode": "lan" if not env_key else "secured",
            "note": "传输鉴权复用 WS 握手 auth_token，不另设配对凭证；仅当 SIYI_TRANSFER_KEY 显式注入时额外校验 _token。",
        }

    registry.register("transfer.token_info", transfer_token_info)

    # ── transfer.send (authenticated) ──
    async def transfer_send(
        text: str = "",
        target: str = "desktop",
        file: str = "",
        fileName: str = "",
        file_path: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        auth_err = _check_auth(**kwargs)
        if auth_err:
            return {"error": auth_err}
        # 七维-兼C4：file_path 为手机端大文件场景的显式参数
        # （TransferView.tsx:43 >1MB 时走 file_path 引用而非 base64 内联）。
        # 此前被 kwargs 静默丢弃：带文本时附件引用无痕丢失，仅发路径时直接报错。
        if (not text or not text.strip()) and not file and not file_path:
            return {"error": "text or file required"}

        entry: dict[str, Any] = {
            "text": text.strip()[:5000] if text else "",
            "target": target,
            "from_device": "mobile" if target == "desktop" else "desktop",
            "timestamp": time.time(),
        }
        device_local_ref = ""
        if file_path:
            # file_path 是发送端设备本地引用，桌面端无法读取其文件系统——
            # 只记录为历史条目元数据供展示，实际字节仍需走 transfer.upload 通道。
            device_local_ref = str(file_path).strip()[:512]
            entry["file_path"] = device_local_ref
        if file:
            # B-06: history 内嵌 file 独立小上限（1MB base64）——超限拒绝，
            # 引导走 transfer.upload 大文件通道；不静默截断（P2-13 教训）。
            if len(file) > MAX_EMBEDDED_FILE:
                return {
                    "error": (
                        f"file too large for transfer.send (max {MAX_EMBEDDED_FILE // (1024 * 1024)}MB base64); use transfer.upload for large files"
                    )
                }
            # Keep full base64 for viewing; truncate if unreasonably large
            # P2-13: 与 transfer.upload 的 MAX_FILE_SIZE (50MB) 保持一致，
            # 此前硬编码 10MB 导致上传的文件在 send 时被静默截断。
            # （B-06 后此处为纯防御：1MB 上限检查先行，实际到不了 50MB）
            entry["file"] = file[:MAX_FILE_SIZE]
            entry["fileName"] = fileName or "unnamed"

        try:
            # P1 修复：文件 I/O 包裹 to_thread，避免 os.fsync 阻塞 event loop
            def _persist_transfer():
                path = _get_transfer_path(context)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                history: list[dict] = []
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        try:
                            history = json.load(f)
                        except (json.JSONDecodeError, ValueError):
                            history = []
                history.insert(0, entry)
                if len(history) > MAX_HISTORY:
                    history = history[:MAX_HISTORY]
                tmp = f"{path}.{os.getpid()}.{os.urandom(4).hex()}.tmp"
                try:
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(history, f, ensure_ascii=False, indent=2)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp, path)  # atomic on same filesystem
                finally:
                    if os.path.exists(tmp):
                        try:
                            os.remove(tmp)
                        except OSError:
                            pass

            await asyncio.to_thread(_persist_transfer)
        except Exception as exc:
            logger.warning("Failed to persist transfer: %s", exc)

        try:
            from core.events import AppEvent, event_bus

            event_bus.emit(AppEvent("transfer.received", payload=entry))
        except Exception:
            logger.exception("transfer.send 推送 transfer.received 事件失败，数据已落盘")
            # event_bus 推送失败不影响主流程 — 数据已落盘
            pass

        resp: dict[str, Any] = {"success": True}
        if device_local_ref:
            resp["warning"] = (
                "file_path recorded as device-local reference only; "
                "desktop cannot read sender filesystem - use transfer.upload "
                "with base64 data to transfer actual file bytes"
            )
            logger.warning(
                "transfer.send file_path is device-local reference (not fetchable): %s",
                device_local_ref[:120],
            )
        return resp

    registry.register("transfer.send", transfer_send)

    # ── transfer.upload ── 上传文件（写到 transfer 目录）──
    async def transfer_upload(
        path: str = "",
        data: str = "",
        fileName: str = "",
        name: str = "",
        folder: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """上传二进制文件（base64）到本地 transfer 目录。

        - path/name: 可选 "上传到的相对路径"（默认按 fileName + 时间戳）
        - data: base64 编码内容（必填）
        - fileName: 原始文件名（用于回显）
        - folder: 七维-兼C4（2026-08-29）：可选目标子目录（如 "notes/<note_id>"），
          手机端 noteAttachment.ts:346 依赖此参数把附件归档到笔记目录；
          此前该参数被 kwargs 静默丢弃，附件一律落 transfer_files 根目录。
          穿越防护：folder 与 name 组合后经 _resolve_file_store_path 校验。
        """
        auth_err = _check_auth(**kwargs)
        if auth_err:
            return {"success": False, "error": auth_err}
        file_name = (path or name or fileName or "").strip()
        if not file_name:
            return {"success": False, "error": "name and data required"}
        if not data:
            return {"success": False, "error": "data required"}

        try:
            import base64

            ds = getattr(context, "local_data_store", None)
            base_dir = getattr(ds, "base_dir", None) if ds else None
            if not base_dir:
                return {"success": False, "error": "data_store unavailable"}

            # 路径穿越保护：禁止 .. 和绝对路径（纯计算，留在事件循环）
            rel_name = file_name.replace("\\", "/")
            if ".." in rel_name:
                return {"success": False, "error": "invalid path (traversal)"}
            # PH-F2（2026-09-17）：盘符/绝对路径必须显式拒绝 —— 否则会被
            # os.path.basename 收敛成裸文件名静默收下（用户以为传到了 C:\foo\bar）。
            if _is_drive_or_absolute(rel_name):
                return {"success": False, "error": "invalid path (absolute path not allowed)"}

            # 七维-兼C4：folder 归一化（手机端发 "notes/<id>"）。此前被 kwargs
            # 静默丢弃 → 附件全部拍平到 transfer_files 根，笔记归档意图失效。
            folder_norm = (folder or "").replace("\\", "/").strip().strip("/")
            if folder_norm:
                parts = [p for p in folder_norm.split("/") if p]
                if not parts or any(p in ("..", ".") for p in parts):
                    return {"success": False, "error": "invalid path (traversal)"}
                folder_norm = "/".join(parts)
            rel_target = (folder_norm + "/" + os.path.basename(rel_name)) if folder_norm else os.path.basename(rel_name)

            # 兼容前端 data:image/...;base64, 前缀
            payload = data.strip()
            if "," in payload:
                payload = payload.split(",", 1)[1]
            if len(payload) > MAX_FILE_SIZE:
                return {"success": False, "error": "file too large (>50MB)"}

            # base64 解码（CPU 密集）+ 文件写入 + fsync（阻塞 I/O）整段包裹 to_thread
            def _do_upload():
                transfer_root = os.path.join(base_dir, "transfer_files")
                os.makedirs(transfer_root, exist_ok=True)
                # 七维-兼C4：folder+name 组合后仍走 _resolve_file_store_path
                # 二次穿越校验（双保险），folder 注入 "../" 在事件循环层已拒。
                resolved = _resolve_file_store_path(transfer_root, rel_target)
                if resolved is None:
                    return {"success": False, "error": "invalid path (traversal)"}
                target = resolved
                # 七维-兼C4：folder 场景需建中间目录（旧逻辑只有 transfer_files 一层）
                os.makedirs(os.path.dirname(target), exist_ok=True)
                try:
                    blob = base64.b64decode(payload, validate=True)
                except Exception as exc:
                    logger.exception("transfer.upload base64 解码失败")
                    return {"success": False, "error": f"invalid base64: {exc}"}
                if len(blob) > MAX_FILE_SIZE:  # 50MB cap
                    return {"success": False, "error": "file too large (>50MB)"}
                # H5 修复：原子写入。先写 .tmp（含进程 PID 防并发同名冲突），
                # 然后 fsync + os.replace 原子换名。这样进程 crash 时只会留下
                # 一个不完整的 .tmp 残片（启动期可清理），不会污染正式文件。
                # PH-D3（2026-09-17）：os.replace 会静默覆盖同名文件 —— 上传不
                # 因此阻断，但必须在返回体如实告知 overwritten，避免用户以为
                # 是新建文件。
                overwritten = os.path.exists(target)
                tmp_path = f"{target}.{os.getpid()}.tmp"
                try:
                    with open(tmp_path, "wb") as f:
                        f.write(blob)
                        f.flush()
                        try:
                            os.fsync(f.fileno())
                        except OSError:
                            # 某些 FS（network shares）不支持 fsync — 不致命
                            pass
                    os.replace(tmp_path, target)
                except Exception:
                    # 失败时清理 tmp 残片
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise
                return {
                    "success": True,
                    "path": rel_target,
                    "size": len(blob),
                    "overwritten": overwritten,
                }

            return await asyncio.to_thread(_do_upload)
        except Exception as exc:
            logger.warning("transfer.upload failed: %s", exc)
            return {"success": False, "error": str(exc)}

    registry.register("transfer.upload", transfer_upload)

    # ── transfer.download ── 下载文件（base64）──
    async def transfer_download(path: str = "", **kwargs: Any) -> dict[str, Any]:
        """下载 transfer 目录下的文件。

        PH-D1（2026-09-17）：path 接受**含 folder 的相对路径**（如
        "notes/<note_id>/a.png"，即 transfer.upload 返回的 path）。此前只取
        os.path.basename → folder 归档的附件永远下载失败（上传/读取不对称）。
        """
        auth_err = _check_auth(**kwargs)
        if auth_err:
            return {"success": False, "error": auth_err}
        if not path:
            return {"success": False, "error": "path required"}

        try:
            import base64

            ds = getattr(context, "local_data_store", None)
            base_dir = getattr(ds, "base_dir", None) if ds else None
            if not base_dir:
                return {"success": False, "error": "data_store unavailable"}

            # 路径穿越 + 盘符/绝对路径保护（纯计算，先于任何 I/O）
            rel_name = path.strip().replace("\\", "/")
            if ".." in rel_name:
                return {"success": False, "error": "invalid path (traversal)"}
            if _is_drive_or_absolute(rel_name):
                return {"success": False, "error": "invalid path (absolute path not allowed)"}

            # 文件读取（阻塞 I/O，大文件会阻塞事件循环）+ base64 编码（CPU 密集）
            def _do_download():
                transfer_root = os.path.join(base_dir, "transfer_files")
                # PH-D1：复用与 upload 相同的解析器，按原 rel_target（含 folder）定位。
                target = _resolve_file_store_path(transfer_root, rel_name)
                if target is None:
                    return {"success": False, "error": "invalid path (traversal)"}
                if not os.path.exists(target) or os.path.isdir(target):
                    return {"success": False, "error": "file not found"}
                if os.path.getsize(target) > 50 * 1024 * 1024:
                    return {"success": False, "error": "file too large to send back"}
                with open(target, "rb") as f:
                    blob = f.read()
                return {
                    "success": True,
                    "data": base64.b64encode(blob).decode("ascii"),
                    "size": len(blob),
                }

            return await asyncio.to_thread(_do_download)
        except Exception as exc:
            logger.warning("transfer.download failed: %s", exc)
            return {"success": False, "error": str(exc)}

    registry.register("transfer.download", transfer_download)

    # ── transfer.files ── 文件列表 ──
    async def transfer_files(folder: str = "", **kwargs: Any) -> list[dict[str, Any]]:
        """列出 transfer_files 目录下的文件。"""
        auth_err = _check_auth(**kwargs)
        if auth_err:
            return [{"error": auth_err}]

        try:
            ds = getattr(context, "local_data_store", None)
            base_dir = getattr(ds, "base_dir", None) if ds else None
            if not base_dir:
                return []

            # listdir + 多次 stat（每个文件 3 次 syscall）整段包裹 to_thread
            def _list_files():
                transfer_root = os.path.join(base_dir, "transfer_files")
                rel = folder.strip().replace("\\", "/").lstrip("/")
                if ".." in rel or (rel and not os.path.isdir(os.path.join(transfer_root, rel))):
                    return []
                target_dir = os.path.join(transfer_root, rel) if rel else transfer_root
                if not os.path.isdir(target_dir):
                    return []
                files = []
                for name in os.listdir(target_dir):
                    file_path = os.path.join(target_dir, name)
                    if os.path.isfile(file_path):
                        files.append(
                            {
                                "name": name,
                                "size": os.path.getsize(file_path),
                                "modified": os.path.getmtime(file_path),
                            }
                        )
                return files

            # 帧守卫（2026-10-15）：裸 list 契约不变，超 2000 条截断 + 告警
            return cap_list(await asyncio.to_thread(_list_files), method="transfer.files")
        except Exception as exc:
            logger.warning("transfer.files failed: %s", exc)
            return []

    registry.register("transfer.files", transfer_files)

    # ── transfer.delete ── 删除文件 ──
    async def transfer_delete(path: str = "", **kwargs: Any) -> dict[str, Any]:
        """删除 transfer_files 目录下的文件。

        PH-D1（2026-09-17）：path 接受**含 folder 的相对路径**（与 download 对称）；
        此前只按 basename 定位 → folder 归档的附件永远删不掉。
        """
        auth_err = _check_auth(**kwargs)
        if auth_err:
            return {"success": False, "error": auth_err}
        if not path:
            return {"success": False, "error": "path required"}

        try:
            ds = getattr(context, "local_data_store", None)
            base_dir = getattr(ds, "base_dir", None) if ds else None
            if not base_dir:
                return {"success": False, "error": "data_store unavailable"}

            # 路径穿越 + 盘符/绝对路径 + 目录 basename 校验（纯计算，先于 I/O）
            rel_name = path.strip().replace("\\", "/")
            if ".." in rel_name:
                return {"success": False, "error": "invalid path (traversal)"}
            if _is_drive_or_absolute(rel_name):
                return {"success": False, "error": "invalid path (absolute path not allowed)"}
            base_name = os.path.basename(rel_name)
            if not base_name or base_name in (".", ".."):
                return {"success": False, "error": "cannot delete directory"}

            # 文件存在性检查 + os.remove（阻塞 I/O）包裹 to_thread
            def _do_delete():
                transfer_root = os.path.join(base_dir, "transfer_files")
                # PH-D1：复用与 upload 相同的解析器，按原 rel_target（含 folder）定位。
                target = _resolve_file_store_path(transfer_root, rel_name)
                if target is None:
                    return {"success": False, "error": "invalid path (traversal)"}
                if not os.path.exists(target):
                    return {"success": False, "error": "file not found"}
                if os.path.isdir(target):
                    return {"success": False, "error": "cannot delete directory"}
                os.remove(target)
                return {"success": True}

            return await asyncio.to_thread(_do_delete)
        except Exception as exc:
            logger.warning("transfer.delete failed: %s", exc)
            return {"success": False, "error": str(exc)}

    registry.register("transfer.delete", transfer_delete)

    # ── transfer.history (authenticated) ──
    async def transfer_history(limit: int = 20, **kwargs: Any) -> list[dict[str, Any]]:
        auth_err = _check_auth(**kwargs)
        if auth_err:
            return [{"error": auth_err}]
        try:
            path = _get_transfer_path(context)
            if os.path.exists(path):
                # P1 修复：传送历史文件损坏/读取失败时无日志，加 warning 并记录异常
                # ★ 包裹 to_thread 避免文件 I/O 阻塞事件循环
                def _read_history():
                    with open(path, "r", encoding="utf-8") as f:
                        # 帧守卫（2026-10-15）：limit<=0 收紧为默认（原负值语义
                        # 是 json[:-N] 反向截断，属隐藏 bug），上限 clamp 2000
                        return json.load(f)[: clamp_limit(limit, default=20)]

                return await asyncio.to_thread(_read_history)
        except Exception as exc:
            logger.warning("transfer.history failed to read %s: %s", path, exc, exc_info=True)
        return []

    registry.register("transfer.history", transfer_history)
