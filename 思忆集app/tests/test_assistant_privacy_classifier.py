"""PrivacyClassifier 单元测试（对应 Android PrivacyClassifierTest.kt）。"""

from assistant.routing.privacy_classifier import PrivacyClassifier, PrivacyLevel


def test_classify_sensitive_chinese_keywords_returns_local_only():
    c = PrivacyClassifier()
    assert c.classify("这是我的日记") == PrivacyLevel.LOCAL_ONLY
    assert c.classify("私人笔记内容") == PrivacyLevel.LOCAL_ONLY
    assert c.classify("这是一个秘密") == PrivacyLevel.LOCAL_ONLY
    assert c.classify("隐私设置") == PrivacyLevel.LOCAL_ONLY


def test_classify_sensitive_english_keywords_returns_local_only():
    c = PrivacyClassifier()
    assert c.classify("my password is") == PrivacyLevel.LOCAL_ONLY
    assert c.classify("this is secret") == PrivacyLevel.LOCAL_ONLY
    assert c.classify("private information") == PrivacyLevel.LOCAL_ONLY


def test_classify_expanded_chinese_patterns():
    c = PrivacyClassifier()
    assert c.classify("我的银行卡号") == PrivacyLevel.LOCAL_ONLY
    assert c.classify("身份证号码") == PrivacyLevel.LOCAL_ONLY
    assert c.classify("手机号是138") == PrivacyLevel.LOCAL_ONLY
    assert c.classify("家庭住址") == PrivacyLevel.LOCAL_ONLY
    assert c.classify("健康报告") == PrivacyLevel.LOCAL_ONLY
    assert c.classify("工资收入") == PrivacyLevel.LOCAL_ONLY


def test_classify_expanded_english_patterns():
    c = PrivacyClassifier()
    assert c.classify("confidential document") == PrivacyLevel.LOCAL_ONLY
    assert c.classify("bank card number") == PrivacyLevel.LOCAL_ONLY
    assert c.classify("credit card info") == PrivacyLevel.LOCAL_ONLY
    assert c.classify("my phone number") == PrivacyLevel.LOCAL_ONLY
    assert c.classify("home address") == PrivacyLevel.LOCAL_ONLY
    assert c.classify("health record") == PrivacyLevel.LOCAL_ONLY
    assert c.classify("api key is sk-") == PrivacyLevel.LOCAL_ONLY


def test_classify_my_notes_or_tasks_returns_local_only():
    c = PrivacyClassifier()
    assert c.classify("我的笔记") == PrivacyLevel.LOCAL_ONLY
    assert c.classify("我的任务") == PrivacyLevel.LOCAL_ONLY
    assert c.classify("我的日程") == PrivacyLevel.LOCAL_ONLY


def test_classify_innocuous_text_returns_default():
    c = PrivacyClassifier()
    assert c.classify("今天天气怎么样") == PrivacyLevel.SYNCABLE
    assert c.classify("翻译 hello world") == PrivacyLevel.SYNCABLE
    assert c.classify("写一首关于春天的诗") == PrivacyLevel.SYNCABLE


def test_classify_innocuous_text_custom_default():
    c = PrivacyClassifier()
    assert c.classify("今天天气怎么样", PrivacyLevel.SHAREABLE) == PrivacyLevel.SHAREABLE
    assert c.classify("hello", PrivacyLevel.MODEL_EGRESS_ALLOWED) == PrivacyLevel.MODEL_EGRESS_ALLOWED


def test_is_personal_content_chinese_patterns():
    c = PrivacyClassifier()
    assert c.is_personal_content("我的笔记")
    assert c.is_personal_content("我的任务")


def test_is_personal_content_english_patterns():
    c = PrivacyClassifier()
    assert c.is_personal_content("my note")
    assert c.is_personal_content("my task")
    assert c.is_personal_content("my diary")


def test_is_personal_content_non_personal_text():
    c = PrivacyClassifier()
    assert not c.is_personal_content("今天天气")
    assert not c.is_personal_content("写诗")
    assert not c.is_personal_content("搜索新闻")


def test_classify_case_insensitive():
    c = PrivacyClassifier()
    assert c.classify("My Password") == PrivacyLevel.LOCAL_ONLY
    assert c.classify("SECRET NOTE") == PrivacyLevel.LOCAL_ONLY


def test_classify_empty_text():
    c = PrivacyClassifier()
    assert c.classify("") == PrivacyLevel.SYNCABLE
    assert c.classify("", PrivacyLevel.LOCAL_ONLY) == PrivacyLevel.LOCAL_ONLY


def test_is_personal_content_empty():
    c = PrivacyClassifier()
    assert not c.is_personal_content("")
