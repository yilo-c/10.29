"""Desktop automation capability domain.

P0 capabilities that let AI operate the desktop:
  - window.find / window.activate  — window management via Win32 API
  - screen.read                    — screenshot + optional OCR
  - mouse.click                    — simulated mouse click at coordinates
  - keyboard.type                  — simulated keyboard input
"""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("siyi.assistant.capability.desktop")

_HAS_PIL = False
try:
    from PIL import ImageGrab
    _HAS_PIL = True
except ImportError:
    logger.debug("PIL not available — screen.read will be limited")

_HAS_PYTESSERACT = False
try:
    import pytesseract
    _HAS_PYTESSERACT = True
except ImportError:
    logger.debug("pytesseract not available — OCR disabled in screen.read")

# ── Win32 API helpers via ctypes ────────────────────────────
import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32

# Constants
SW_RESTORE = 9
SW_SHOW = 5
SW_MINIMIZE = 6
SW_MAXIMIZE = 3
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
KEYEVENTF_KEYUP = 0x0002
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1


class _RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


# ── Window info structure for EnumWindows callback ──
_WINDOW_INFO_CACHE: list[dict[str, Any]] = []


def _enum_window_proc(hwnd: int, _lParam: int) -> bool:
    """Callback for EnumWindows — collects visible window info."""
    if not user32.IsWindowVisible(hwnd):
        return True
    length = user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return True
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    title = buf.value.strip()
    if not title:
        return True

    rect = _RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))

    # Get class name
    cls_buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls_buf, 256)

    # Get process ID
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

    _WINDOW_INFO_CACHE.append({
        "hwnd": hwnd,
        "title": title,
        "class_name": cls_buf.value,
        "pid": pid.value,
        "rect": {
            "left": rect.left, "top": rect.top,
            "right": rect.right, "bottom": rect.bottom,
        },
        "width": rect.right - rect.left,
        "height": rect.bottom - rect.top,
    })
    return True


# ── DesktopAutomationCapabilityDomain ───────────────────────


class DesktopAutomationCapabilityDomain:
    """Desktop automation: window management, screen, mouse, keyboard."""

    def __init__(self, router) -> None:
        self.router = router

    # ── Window Management ──────────────────────────────────

    def window_find(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Find windows by title (partial match) or class name.

        Payload:
          title: str — window title to search for (partial match)
          class_name: str — optional Win32 class name filter
          max_results: int — max results to return (default 20)
        """
        query = str(payload.get("title") or "").strip().lower()
        class_filter = str(payload.get("class_name") or "").strip().lower()
        max_results = max(1, min(int(payload.get("max_results") or 20), 100))

        global _WINDOW_INFO_CACHE
        _WINDOW_INFO_CACHE = []
        user32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(_enum_window_proc), 0)

        results = []
        for win in _WINDOW_INFO_CACHE:
            if query and query not in win["title"].lower():
                continue
            if class_filter and class_filter not in win["class_name"].lower():
                continue
            results.append(win)
            if len(results) >= max_results:
                break

        return {
            "success": True,
            "count": len(results),
            "windows": results,
            "query": payload.get("title", ""),
        }

    def window_activate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Activate a window by handle or title.

        Payload (one of):
          hwnd: int — window handle from window.find
          title: str — window title to find and activate
        """
        hwnd = payload.get("hwnd")
        if hwnd is None:
            title = str(payload.get("title") or "").strip()
            if not title:
                return {"success": False, "reason": "provide hwnd or title"}
            # Find window by title
            find_result = self.window_find({"title": title, "max_results": 1})
            windows = find_result.get("windows", [])
            if not windows:
                return {"success": False, "reason": f"window not found: {title}"}
            hwnd = windows[0]["hwnd"]

        try:
            hwnd = int(hwnd)
        except (ValueError, TypeError):
            return {"success": False, "reason": "invalid hwnd"}

        # Restore if minimized, then bring to foreground
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.05)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.05)

        # Get window info for return
        rect = _RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)

        return {
            "success": True,
            "hwnd": hwnd,
            "title": buf.value,
            "rect": {
                "left": rect.left, "top": rect.top,
                "right": rect.right, "bottom": rect.bottom,
            },
        }

    # ── Screen ─────────────────────────────────────────────

    def screen_read(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Capture screen and optionally run OCR.

        Payload:
          ocr: bool — whether to run OCR (default false, requires pytesseract)
          return_boxes: bool — return OCR text with bounding boxes (default false)
          region: dict — optional crop region {left, top, width, height}
          return_image: bool — whether to return base64 image (default false)
        """
        if not _HAS_PIL:
            return {"success": False, "reason": "PIL not available — install Pillow"}

        do_ocr = bool(payload.get("ocr", False))
        return_image = bool(payload.get("return_image", False))
        return_boxes = bool(payload.get("return_boxes", False))
        region = payload.get("region")

        try:
            if region:
                bbox = (region["left"], region["top"],
                        region["left"] + region.get("width", 0),
                        region["top"] + region.get("height", 0))
                img = ImageGrab.grab(bbox=bbox)
            else:
                img = ImageGrab.grab()

            # Get screen dimensions
            screen_width, screen_height = img.size

            result: dict[str, Any] = {
                "success": True,
                "width": screen_width,
                "height": screen_height,
            }

            if return_image:
                import base64
                import io
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                result["image_base64"] = base64.b64encode(buf.getvalue()).decode("utf-8")
                result["image_size"] = len(buf.getvalue())

            if do_ocr:
                if not _HAS_PYTESSERACT:
                    result["ocr"] = False
                    result["ocr_error"] = "pytesseract not installed"
                else:
                    try:
                        if return_boxes:
                            data = pytesseract.image_to_data(
                                img, lang="chi_sim+eng",
                                output_type=pytesseract.Output.DICT,
                            )
                            boxes = []
                            text_parts = []
                            n = len(data.get("text", []))
                            for i in range(n):
                                t = (data.get("text") or [""])[i] or ""
                                conf_str = data.get("conf") or [0]
                                conf = int(conf_str[i]) if i < len(conf_str) else 0
                                if not t.strip():
                                    continue
                                if conf < 30:
                                    continue
                                left = (data.get("left") or [0])[i]
                                top = (data.get("top") or [0])[i]
                                w = (data.get("width") or [0])[i]
                                h = (data.get("height") or [0])[i]
                                boxes.append({
                                    "text": t.strip()[:100],
                                    "x": left, "y": top,
                                    "width": w, "height": h,
                                    "confidence": conf,
                                })
                                text_parts.append(t.strip())
                            result["ocr"] = True
                            result["text"] = "\n".join(text_parts)
                            result["boxes"] = boxes
                        else:
                            text = pytesseract.image_to_string(img, lang="chi_sim+eng")
                            result["ocr"] = True
                            result["text"] = text.strip()
                    except Exception as exc:
                        result["ocr"] = False
                        result["ocr_error"] = str(exc)
            else:
                result["ocr"] = False

            return result

        except Exception as exc:
            logger.warning("screen.read failed: %s", exc)
            return {"success": False, "reason": str(exc)}

    def desktop_screenshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        """截取整个桌面并保存，可选用视觉模型描述画面内容。

        Payload:
          describe: bool — 是否尝试用视觉模型描述截图内容（默认 true，
            无视觉能力时自动降级为仅保存）
        """
        if not _HAS_PIL:
            return {"success": False, "reason": "PIL 不可用，无法截屏"}

        import base64
        import io
        from datetime import datetime
        from pathlib import Path

        describe = bool(payload.get("describe", True))

        try:
            try:
                img = ImageGrab.grab(all_screens=True)
            except TypeError:
                img = ImageGrab.grab()

            # 缩放到最长边 1600px，控制体积
            max_side = 1600
            if max(img.size) > max_side:
                scale = max_side / max(img.size)
                img = img.resize(
                    (max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                )

            # 保存目录：data/screenshots/
            base_dir = getattr(getattr(self.router, "data_store", None), "base_dir", None)
            if base_dir:
                shot_dir = Path(base_dir) / "screenshots"
            else:
                shot_dir = Path(self.router.shell_execution.workspace_root) / "data" / "screenshots"
            shot_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            shot_path = shot_dir / f"screen_{ts}.jpg"
            img.convert("RGB").save(shot_path, format="JPEG", quality=80)

            result: dict[str, Any] = {
                "success": True,
                "path": str(shot_path),
                "width": img.width,
                "height": img.height,
            }

            # 视觉描述（可选，失败自动降级）
            if describe:
                ai = getattr(self.router, "ai_client", None)
                if ai is not None and hasattr(ai, "chat_completion_vision"):
                    try:
                        buf = io.BytesIO()
                        thumb = img.copy()
                        thumb.thumbnail((1024, 1024))
                        thumb.convert("RGB").save(buf, format="JPEG", quality=70)
                        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
                        description = ai.chat_completion_vision(
                            text="请用简洁中文描述这张桌面截图里有什么（打开的窗口、应用、文件等），供用户了解当前屏幕状态。",
                            image_base64=b64,
                            image_format="jpeg",
                            max_tokens=400,
                        )
                        if isinstance(description, dict):
                            description = description.get("content") or description.get("text") or ""
                        if description:
                            result["description"] = str(description).strip()
                    except Exception as exc:
                        logger.debug("desktop.screenshot vision failed: %s", exc)
                        result["vision_note"] = "视觉模型不可用，已仅保存截图"
                else:
                    result["vision_note"] = "未配置视觉模型，已仅保存截图"

            # 前端展示用缩略图 artifact（base64 data URL）
            try:
                buf2 = io.BytesIO()
                artifact_img = img.copy()
                artifact_img.thumbnail((1024, 1024))
                artifact_img.convert("RGB").save(buf2, format="JPEG", quality=75)
                data_url = "data:image/jpeg;base64," + base64.b64encode(buf2.getvalue()).decode("utf-8")
                result["artifacts"] = [{
                    "id": f"screenshot_{ts}",
                    "type": "image",
                    "src": data_url,
                    "alt": "桌面截图",
                    "title": f"桌面截图 {ts}",
                    "status": "ready",
                    "source": "desktop.screenshot",
                }]
            except Exception:
                logger.debug("desktop.screenshot artifact build failed", exc_info=True)

            return result
        except Exception as exc:
            logger.warning("desktop.screenshot failed: %s", exc)
            return {"success": False, "reason": str(exc)}

    # ── Mouse ──────────────────────────────────────────────

    def mouse_click(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Simulate mouse click at coordinates.

        Payload:
          x: int — screen x coordinate
          y: int — screen y coordinate
          button: str — "left" (default) or "right"
          clicks: int — number of clicks (default 1, max 3)
          absolute: bool — if True use absolute coords (default), else relative to active window
        """
        x = int(payload.get("x", 0))
        y = int(payload.get("y", 0))
        button = str(payload.get("button", "left")).strip().lower()
        clicks = max(1, min(int(payload.get("clicks", 1)), 3))

        try:
            # Move cursor first
            user32.SetCursorPos(x, y)
            time.sleep(0.03)

            # Determine event flags
            if button == "right":
                down_flag = 0x0008  # MOUSEEVENTF_RIGHTDOWN
                up_flag = 0x0010    # MOUSEEVENTF_RIGHTUP
            else:
                down_flag = MOUSEEVENTF_LEFTDOWN
                up_flag = MOUSEEVENTF_LEFTUP

            for _ in range(clicks):
                user32.mouse_event(down_flag, 0, 0, 0, 0)
                time.sleep(0.02)
                user32.mouse_event(up_flag, 0, 0, 0, 0)
                time.sleep(0.05)

            return {"success": True, "x": x, "y": y, "button": button, "clicks": clicks}
        except Exception as exc:
            logger.warning("mouse.click failed: %s", exc)
            return {"success": False, "reason": str(exc)}

    # ── OCR Text Click ─────────────────────────────────────

    def mouse_click_text(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Click on text found via OCR on screen.

        Payload:
          text: str — text to search for (substring match)
          button: str — "left" (default) or "right"
          clicks: int — number of clicks (default 1)
          fuzzy: bool — case-insensitive match (default True)

        Returns: result with click coordinates or error.
        """
        target = str(payload.get("text") or "").strip()
        if not target:
            return {"success": False, "reason": "no_text"}
        button = str(payload.get("button", "left")).strip().lower()
        clicks = max(1, min(int(payload.get("clicks", 1)), 3))
        fuzzy = bool(payload.get("fuzzy", True))

        # Capture screen with OCR bounding boxes
        screen = self.screen_read({"ocr": True, "return_boxes": True})
        if not screen.get("success"):
            return {"success": False, "reason": f"screen capture failed: {screen.get('reason')}"}
        if not screen.get("ocr"):
            return {"success": False, "reason": "OCR not available"}

        boxes = screen.get("boxes") or []
        if not boxes:
            return {"success": False, "reason": "no text detected on screen"}

        # Search for matching text
        target_lower = target.lower()
        candidates = []
        for box in boxes:
            box_text = str(box.get("text") or "")
            if fuzzy:
                match = target_lower in box_text.lower()
            else:
                match = target in box_text
            if match:
                # Score: exact match > substring match, higher conf better
                score = 1.0 if box_text.strip() == target else 0.5
                score *= min(1.0, (box.get("confidence", 0) or 0) / 100.0)
                candidates.append((score, box))

        if not candidates:
            return {
                "success": False,
                "reason": f"text '{target}' not found on screen",
                "available_texts": [b["text"] for b in boxes[:50]],
            }

        # Pick best match
        candidates.sort(key=lambda x: -x[0])
        best = candidates[0][1]
        cx = best["x"] + best["width"] // 2
        cy = best["y"] + best["height"] // 2

        # Click at center of matched text
        try:
            user32.SetCursorPos(cx, cy)
            time.sleep(0.03)
            if button == "right":
                user32.mouse_event(0x0008, 0, 0, 0, 0)
                time.sleep(0.02)
                user32.mouse_event(0x0010, 0, 0, 0, 0)
            else:
                for _ in range(clicks):
                    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                    time.sleep(0.02)
                    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
                    time.sleep(0.05)
            return {
                "success": True,
                "matched_text": best["text"],
                "x": cx, "y": cy,
                "button": button, "clicks": clicks,
                "confidence": best.get("confidence", 0),
            }
        except Exception as exc:
            logger.warning("mouse.click_text failed: %s", exc)
            return {"success": False, "reason": str(exc)}

    # ── Keyboard ───────────────────────────────────────────

    def keyboard_type(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Simulate keyboard input (types text at current focus).

        Payload:
          text: str — text to type
          enter: bool — press Enter after typing (default false)
          delay: float — delay between keystrokes in seconds (default 0.01)
        """
        text = str(payload.get("text") or "")
        if not text:
            return {"success": False, "reason": "no_text"}
        press_enter = bool(payload.get("enter", False))
        delay = max(0.001, min(float(payload.get("delay", 0.01)), 0.5))

        try:
            typed = self._send_unicode_text(text, delay)

            if press_enter:
                self._send_key(0x0D)  # VK_RETURN
                time.sleep(0.05)

            return {"success": True, "characters": len(text), "typed": typed}
        except Exception as exc:
            logger.warning("keyboard.type failed: %s", exc)
            return {"success": False, "reason": str(exc)}

    def keyboard_press(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Press specific keys (modifier + key combinations).

        Payload:
          keys: list[str] — list of keys to press, e.g. ["ctrl", "c"]
          OR
          key: str — single key

        Supported key names: ctrl, alt, shift, win, enter, tab, esc,
        backspace, delete, up, down, left, right, home, end, pgup, pgdn,
        f1-f12, and single characters.
        """
        keys = payload.get("keys") or []
        if isinstance(keys, str):
            keys = [keys]
        single_key = payload.get("key", "")
        if single_key and not keys:
            keys = [single_key]

        if not keys:
            return {"success": False, "reason": "no_keys"}

        try:
            vk_codes = []
            for k in keys:
                vk = self._key_name_to_vk(str(k).lower())
                if vk is not None:
                    vk_codes.append(vk)

            if not vk_codes:
                return {"success": False, "reason": "no_recognizable_keys"}

            # Press all down in order
            for vk in vk_codes:
                user32.keybd_event(vk, 0, 0, 0)
                time.sleep(0.02)

            # Release all in reverse order
            for vk in reversed(vk_codes):
                user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
                time.sleep(0.02)

            return {"success": True, "keys": keys, "vk_codes": vk_codes}
        except Exception as exc:
            logger.warning("keyboard.press failed: %s", exc)
            return {"success": False, "reason": str(exc)}

    # ── Internal helpers ───────────────────────────────────

    def _send_unicode_text(self, text: str, delay: float) -> int:
        """Send Unicode text via WM_CHAR messages."""
        for ch in text:
            vk = ord(ch)
            # Key down
            user32.keybd_event(vk, 0, 0, 0)
            time.sleep(delay)
            # Key up
            user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
            time.sleep(delay)
        return len(text)

    def _send_key(self, vk: int) -> None:
        """Press and release a single virtual key."""
        user32.keybd_event(vk, 0, 0, 0)
        time.sleep(0.02)
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.02)

    @staticmethod
    def _key_name_to_vk(name: str) -> int | None:
        """Map key name to Windows virtual key code."""
        KEY_MAP = {
            "ctrl": 0x11, "control": 0x11,
            "alt": 0x12,
            "shift": 0x10,
            "win": 0x5B, "windows": 0x5B,
            "enter": 0x0D, "return": 0x0D,
            "tab": 0x09,
            "esc": 0x1B, "escape": 0x1B,
            "backspace": 0x08, "bs": 0x08,
            "delete": 0x2E, "del": 0x2E,
            "insert": 0x2D, "ins": 0x2D,
            "up": 0x26, "down": 0x28,
            "left": 0x25, "right": 0x27,
            "home": 0x24, "end": 0x23,
            "pgup": 0x21, "pagedown": 0x22, "pgdn": 0x22,
            "space": 0x20,
            "capslock": 0x14, "caps": 0x14,
            "numlock": 0x90,
            "scrolllock": 0x91,
            "pause": 0x13, "break": 0x13,
            "printscreen": 0x2C, "prtsc": 0x2C,
        }
        # F-keys
        if name.startswith("f") and len(name) <= 3:
            try:
                num = int(name[1:])
                if 1 <= num <= 12:
                    return 0x70 + (num - 1)
            except ValueError:
                pass
        # Single character
        if len(name) == 1:
            return ord(name.upper())
        return KEY_MAP.get(name, None)
