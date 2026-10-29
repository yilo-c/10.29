"""微信聊天记录解析器测试（M1：WeChatMsg/留痕 导出 txt/csv 导入）。"""
from __future__ import annotations

import os
import tempfile

import pytest

from integrations.chat_importer import (
    _parse_wechat_csv,
    _parse_wechat_text,
    _parse_wechat_txt_file,
)

WECHAT_TXT_SAMPLE = """2024-01-01 10:00:00 张三: 周末一起去爬山吗？
2024-01-01 10:05:00 我: 好啊，几点出发
2024-01-01 10:06:00 张三: 早上八点，老地方集合
你撤回了一条消息
2024-01-01 10:10:00 我: 收到，到时候联系
2024/1/1 10:12:00 张三: 没问题👌
"""


class TestParseWechatText:
    def test_basic_parse_and_role_mapping(self):
        convs = _parse_wechat_text(WECHAT_TXT_SAMPLE, title="张三")
        assert len(convs) == 1
        conv = convs[0]
        assert conv["source"] == "wechat"
        assert conv["title"] == "张三"
        messages = conv["messages"]
        # 撤回的系统消息被过滤，剩余 5 条
        assert len(messages) == 5
        # 我 → user，张三 → assistant
        roles = [m["role"] for m in messages]
        assert roles == ["assistant", "user", "assistant", "user", "assistant"]
        # name 保留真实发送人
        assert messages[0]["name"] == "张三"
        assert messages[1]["name"] == "我"
        # 内容原文保留
        assert messages[0]["content"] == "周末一起去爬山吗？"
        assert messages[1]["content"] == "好啊，几点出发"

    def test_ts_normalized_to_iso(self):
        convs = _parse_wechat_text(WECHAT_TXT_SAMPLE, title="张三")
        assert convs[0]["messages"][0]["timestamp"] == "2024-01-01T10:00:00"
        # 斜杠日期也能解析
        last = convs[0]["messages"][-1]
        assert last["content"] == "没问题👌"
        assert last["timestamp"] == "2024/1/1T10:12:00"

    def test_empty_and_system_only(self):
        assert _parse_wechat_text("", title="x") == []
        system_only = "2024-01-01 10:00:00 张三: 你撤回了一条消息\n2024-01-01 10:01:00 张三: 拍了拍你\n"
        assert _parse_wechat_text(system_only, title="x") == []

    def test_multi_sender_group(self):
        text = (
            "2024-01-01 10:00:00 张三: 大家好\n"
            "2024-01-01 10:01:00 李四: 在的\n"
            "2024-01-01 10:02:00 我: 收到\n"
        )
        convs = _parse_wechat_text(text, title="家庭群")
        assert len(convs) == 1
        assert [m["name"] for m in convs[0]["messages"]] == ["张三", "李四", "我"]
        assert [m["role"] for m in convs[0]["messages"]] == ["assistant", "assistant", "user"]


class TestParseWechatCsv:
    def test_csv_with_english_headers(self):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".csv", encoding="utf-8", delete=False
        ) as f:
            f.write(
                "time,sender,content\n"
                "2024-01-01 10:00:00,张三,周末一起去爬山吗？\n"
                "2024-01-01 10:05:00,我,好啊\n"
            )
            path = f.name
        try:
            convs = _parse_wechat_csv(path)
            assert len(convs) == 1
            msgs = convs[0]["messages"]
            assert len(msgs) == 2
            assert msgs[0]["name"] == "张三"
            assert msgs[0]["role"] == "assistant"
            assert msgs[1]["role"] == "user"
        finally:
            os.unlink(path)

    def test_csv_with_chinese_headers(self):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".csv", encoding="utf-8-sig", delete=False
        ) as f:
            f.write(
                "时间,发送人,内容\n"
                "2024-01-01 10:00:00,张三,在吗\n"
                "2024-01-01 10:01:00,我,在的\n"
            )
            path = f.name
        try:
            convs = _parse_wechat_csv(path)
            assert len(convs) == 1
            assert [m["content"] for m in convs[0]["messages"]] == ["在吗", "在的"]
        finally:
            os.unlink(path)

    def test_csv_missing_columns(self):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".csv", encoding="utf-8", delete=False
        ) as f:
            f.write("a,b,c\n1,2,3\n")
            path = f.name
        try:
            assert _parse_wechat_csv(path) == []
        finally:
            os.unlink(path)


class TestParseWechatTxtFile:
    def test_file_roundtrip(self):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", encoding="utf-8", delete=False
        ) as f:
            f.write(WECHAT_TXT_SAMPLE)
            path = f.name
        try:
            convs = _parse_wechat_txt_file(path)
            assert len(convs) == 1
            assert len(convs[0]["messages"]) == 5
        finally:
            os.unlink(path)

    def test_invalid_utf8_no_crash(self):
        path = os.path.join(tempfile.gettempdir(), "wechat_bad_utf8_test.txt")
        with open(path, "wb") as f:
            f.write(b"\xff\xfe\x00 2024-01-01 10:00:00 \xe5\xbc\xa0\xe4\xb8\x89: hi\n")
        try:
            # errors="ignore" 保证不抛异常，解析结果可为空但不崩
            convs = _parse_wechat_txt_file(path)
            assert isinstance(convs, list)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
