"""P2-2 / P2-3 隐私过滤收紧的专项回归。

覆盖：
- 银行卡号位数（13-19）边界 & 误判规避
- 身份证号 17+1 校验位形态 & 不再误匹配 18 个 X
- 标准 "token" 关键词移除（独立单词），改由显式 token= 模式触发
- 否定语境豁免（"没有密码"、"我不是 password"）
"""

from __future__ import annotations

from memory.extractor._privacy import PrivacyFilter


# ──────────────────────────────────────────────────────────────────────────
# P2-2: 银行卡号
# ──────────────────────────────────────────────────────────────────────────

class TestBankCardTightening:
    def test_real_bank_card_still_detected(self):
        # 16 位真实卡号 — 应当继续被识别
        assert PrivacyFilter.is_sensitive("我的银行卡是1234567890123456")
        assert PrivacyFilter.is_sensitive("credit card number: 4111111111111111")

    def test_13_digit_minimum_detected(self):
        # 13 位（VISA 最短）— 仍属于卡号范围
        assert PrivacyFilter.is_sensitive("我的银行卡是1234567890123")

    def test_19_digit_maximum_detected(self):
        # 19 位（银联最长）— 仍属于卡号范围
        assert PrivacyFilter.is_sensitive("我的银行卡是1234567890123456789")

    def test_12_digit_order_number_not_misjudged(self):
        # 12 位订单号在"银行卡"上下文里曾经被 \d{10,} 误命中；现在应豁免
        assert not PrivacyFilter.is_sensitive("银行卡 订单 123456789012")

    def test_20_digit_long_number_not_misjudged(self):
        # 超过 19 位的连续数字不应被认为是银行卡
        assert not PrivacyFilter.is_sensitive("银行卡 流水号 12345678901234567890")


# ──────────────────────────────────────────────────────────────────────────
# P2-2: 身份证号
# ──────────────────────────────────────────────────────────────────────────

class TestIdCardTightening:
    def test_18_digit_id_card_still_detected(self):
        # 18 位数字身份证
        assert PrivacyFilter.is_sensitive("我的身份证是110101199001011234")

    def test_id_card_with_X_check_digit_still_detected(self):
        # 17 位数字 + X 校验位
        assert PrivacyFilter.is_sensitive("我的身份证是11010119900101123X")
        assert PrivacyFilter.is_sensitive("身份证: 11010119900101123x")

    def test_18_X_chars_not_misjudged(self):
        # 18 个连续 X — 旧 [\dXx]{18} 会误命中，现在应豁免
        assert not PrivacyFilter.is_sensitive("身份证 XXXXXXXXXXXXXXXXXX")

    def test_X_in_middle_not_misjudged(self):
        # X 出现在中间（非末位）不应当作身份证
        assert not PrivacyFilter.is_sensitive("身份证 11010X1990010112345")


# ──────────────────────────────────────────────────────────────────────────
# P2-3: token 关键词收窄
# ──────────────────────────────────────────────────────────────────────────

class TestTokenKeywordRefinement:
    def test_standalone_token_word_no_longer_sensitive(self):
        # 旧实现把任何包含 "token" 的句子都拒掉，包括无害语义
        assert not PrivacyFilter.is_sensitive("This article explains how JWT token works in theory.")
        assert not PrivacyFilter.is_sensitive("学习 OAuth token 的原理")

    def test_token_assignment_still_detected(self):
        # 真正泄露场景：token = xxx / token: xxx / token 是 xxx
        assert PrivacyFilter.is_sensitive("token=abc123def456")
        assert PrivacyFilter.is_sensitive("TOKEN: ghp_FAKE_demo_token_value")
        assert PrivacyFilter.is_sensitive("token 是 xyz789")

    def test_access_token_assignment_still_detected(self):
        # access_token / api_key 老路径继续生效
        assert PrivacyFilter.is_sensitive("access_token=eyJhbGciOiJIUzI1NiJ9")
        assert PrivacyFilter.is_sensitive("api_key=sk-1234567890abcdef")


# ──────────────────────────────────────────────────────────────────────────
# P2-3: 否定语境豁免
# ──────────────────────────────────────────────────────────────────────────

class TestNegationContextExemption:
    def test_password_negated_chinese(self):
        # 用户陈述"没有密码 / 不要密码"不视为敏感
        assert not PrivacyFilter.is_sensitive("我没有密码")
        assert not PrivacyFilter.is_sensitive("这个服务不要密码")
        assert not PrivacyFilter.is_sensitive("账号未设置密码")

    def test_password_negated_english(self):
        assert not PrivacyFilter.is_sensitive("I don't have a password")
        assert not PrivacyFilter.is_sensitive("login without password is allowed")

    def test_password_still_detected_in_normal_context(self):
        # 正常陈述敏感信息时不会被否定语境豁免误放行
        assert PrivacyFilter.is_sensitive("我的密码是abc12345")
        assert PrivacyFilter.is_sensitive("password=mySecretPwd")

    def test_secret_negated(self):
        # 关键词 "secret" 同样支持否定语境
        assert not PrivacyFilter.is_sensitive("there is no secret here")

    def test_multiple_occurrences_one_negated_one_not(self):
        # 第一处 "密码" 被否定但第二处真的暴露 → 仍应判敏感
        assert PrivacyFilter.is_sensitive("我没有密码？错了，我的密码是abc")
