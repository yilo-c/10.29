from __future__ import annotations

import hashlib
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests
from core.settings_schema import KEY_YOUDAO_APP_KEY, KEY_YOUDAO_APP_SECRET

logger = logging.getLogger("siyi.translate")


class TranslateServiceError(RuntimeError):
    """翻译服务配置或调用失败。"""


def _service_error_message(action: str, detail: str) -> str:
    return f"{action}失败: {detail}"


def _decrypt_if_secure(value: Any) -> Any:
    from core.secure_key_storage import SecureKeyStorage

    if not isinstance(value, str):
        return value
    secure = SecureKeyStorage(namespace="com.siyi.apikey")
    if secure.is_secure_token(value):
        return secure.decrypt(value)
    return value


@dataclass(frozen=True)
class YoudaoCredentialConfig:
    app_key: str | None = None
    app_secret: str | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self.app_key and self.app_secret)


def resolve_youdao_credential_config(settings_repository=None, environ=None):
    """优先从环境变量读取，其次回落到本地 settings.json。"""
    resolved_environ = environ or os.environ
    app_key = (
        resolved_environ.get("SIYI_YOUDAO_APP_KEY")
        or resolved_environ.get("YOUDAO_APP_KEY")
    )
    app_secret = (
        resolved_environ.get("SIYI_YOUDAO_APP_SECRET")
        or resolved_environ.get("YOUDAO_APP_SECRET")
    )

    if settings_repository is not None:
        app_key = app_key or settings_repository.get_setting(KEY_YOUDAO_APP_KEY, "")
        app_secret = app_secret or settings_repository.get_setting(KEY_YOUDAO_APP_SECRET, "")

    app_key = _decrypt_if_secure(app_key)
    app_secret = _decrypt_if_secure(app_secret)

    normalized_key = app_key.strip() if isinstance(app_key, str) and app_key.strip() else None
    normalized_secret = app_secret.strip() if isinstance(app_secret, str) and app_secret.strip() else None
    return YoudaoCredentialConfig(app_key=normalized_key, app_secret=normalized_secret)


def create_youdao_translate_api(settings_repository=None, app_key=None, app_secret=None):
    """按统一优先级创建有道翻译客户端。"""
    config = resolve_youdao_credential_config(settings_repository=settings_repository)
    return YoudaoTranslateAPI(
        app_key=app_key or config.app_key,
        app_secret=app_secret or config.app_secret,
    )


# 单词本功能扩展
class VocabularyBook:
    def __init__(self):
        self.words = []

    def add_word(self, word, translation):
        """添加单词到单词本"""
        self.words.append({
            "word": word,
            "translation": translation,
            "added_at": datetime.now().isoformat()
        })

    def review_words(self):
        """基于记忆曲线算法提醒复习单词"""
        # 简单实现：返回所有单词
        return self.words

    def export_to_file(self, file_path):
        """导出单词本为文件"""
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                for word in self.words:
                    f.write(f"{word['word']},{word['translation']},{word['added_at']}\n")
            return f"单词本已导出到 {file_path}"
        except Exception as exc:
            logger.warning("导出单词本失败: file=%s error=%s", file_path, exc)
            return _service_error_message("导出", str(exc))

    def test_words(self):
        """随机抽取单词进行测试"""
        if not self.words:
            return "单词本为空，无法测试"
        import random
        word = random.choice(self.words)
        return f"请拼写单词: {word['translation']}"


# ---------------------- 有道翻译API处理器 ----------------------
class YoudaoTranslateAPI:
    def __init__(self, app_key=None, app_secret=None, session=None):
        self.app_key = app_key.strip() if isinstance(app_key, str) else None
        self.app_secret = app_secret.strip() if isinstance(app_secret, str) else None
        self.url = "https://openapi.youdao.com/api"
        self.headers = {"Content-Type": "application/x-www-form-urlencoded"}
        self.translation_history = []  # 新增：翻译历史记录
        self.vocabulary_book = VocabularyBook()  # 初始化单词本
        self.session = session or requests.Session()

    def is_configured(self):
        return bool(self.app_key and self.app_secret)

    def _require_credentials(self):
        if self.is_configured():
            return
        raise TranslateServiceError(
            _service_error_message(
                "翻译服务配置",
                "请设置环境变量 SIYI_YOUDAO_APP_KEY / SIYI_YOUDAO_APP_SECRET，或在 settings.json 中写入 youdao_app_key / youdao_app_secret。",
            )
        )

    def _build_signed_params(self, text, from_lang, to_lang):
        self._require_credentials()
        salt = str(uuid.uuid4())
        curtime = str(int(time.time()))
        sign_str = self.app_key + self._truncate(text) + salt + curtime + self.app_secret
        sign = hashlib.sha256(sign_str.encode()).hexdigest()
        return {
            "q": text,
            "from": from_lang,
            "to": to_lang,
            "appKey": self.app_key,
            "salt": salt,
            "sign": sign,
            "signType": "v3",
            "curtime": curtime,
        }

    def _post_json(self, url, params, timeout=10):
        try:
            response = self.session.post(url, data=params, headers=self.headers, timeout=timeout)
            response.raise_for_status()
        except requests.exceptions.Timeout as exc:
            logger.warning("翻译请求超时: url=%s", url)
            raise TranslateServiceError(_service_error_message("翻译请求", "请求超时，请检查网络")) from exc
        except requests.exceptions.RequestException as exc:
            logger.warning("翻译请求失败: url=%s error=%s", url, exc)
            raise TranslateServiceError(_service_error_message("翻译请求", str(exc))) from exc

        try:
            return response.json()
        except ValueError as exc:
            logger.warning("翻译服务返回无效响应: url=%s", url)
            raise TranslateServiceError(_service_error_message("翻译请求", "服务返回了无效响应")) from exc

    def _truncate(self, q):
        """截断文本为前20个字符，用于生成有道翻译API的签名"""
        if len(q) <= 20:
            return q
        else:
            return q[0:10] + str(len(q)) + q[-10:]

    def translate(self, text, from_lang="auto", to_lang="zh-CHS"):
        """调用有道翻译API"""
        if not text.strip():
            return "请输入需要翻译的文本"

        params = self._build_signed_params(text, from_lang, to_lang)

        try:
            result = self._post_json(self.url, params)

            # 处理API错误
            if result.get("errorCode") != "0":
                error_message = self._get_error_message(result["errorCode"])
                logger.warning("翻译接口返回错误: code=%s message=%s", result.get("errorCode"), error_message)
                raise TranslateServiceError(_service_error_message("翻译请求", error_message))

            # 修复核心：兼容返回结构
            translations = []
            translation_data = result.get("translation")
            if not translation_data:
                raise TranslateServiceError(_service_error_message("翻译请求", "未获取到有效结果"))

            # 兼容"字符串列表"或"字典列表"
            if isinstance(translation_data, list):
                for item in translation_data:
                    if isinstance(item, dict) and "tgt" in item:
                        translations.append(item["tgt"])
                    elif isinstance(item, str):
                        translations.append(item)
            elif isinstance(translation_data, str):
                translations.append(translation_data)

            if not translations:
                raise TranslateServiceError(_service_error_message("翻译请求", "未解析到有效内容"))

            # 保存翻译历史记录
            translated_text = "\n".join(translations)
            self.translation_history.append({
                "text": text,
                "translated_text": translated_text,
                "from_lang": from_lang,
                "to_lang": to_lang,
                "timestamp": datetime.now().isoformat()
            })

            # 自动将单词加入单词本
            if len(text.split()) == 1:  # 如果是单个单词
                self.add_to_vocabulary_book(text, translated_text)

            return translated_text

        except TranslateServiceError:
            raise
        except Exception as exc:
            logger.exception("翻译调用出现未预期错误")
            raise TranslateServiceError(_service_error_message("翻译请求", str(exc))) from exc

    def get_word_explanation(self, word):
        """获取单词的详细释义和例句（扩展功能）"""
        # 首先进行基本翻译获取释义
        basic_translation = self.translate(word)

        # 标准API请求，移除了可能导致错误的 ext 和 dicts 参数
        params = self._build_signed_params(word, "en", "zh-CHS")

        try:
            result = self._post_json(self.url, params)

            # 检查API返回错误
            if result.get("errorCode") != "0":
                logger.warning("词典释义接口返回错误: code=%s message=%s", result.get("errorCode"), self._get_error_message(result["errorCode"]))
                return {
                    "word": word,
                    "phonetic": "",
                    "meaning": basic_translation,
                    "example": "",
                }

            # 提取音标
            phonetic = ""
            if result.get("basic"):
                basic_info = result["basic"]
                if "phonetic" in basic_info:
                    phonetic = basic_info["phonetic"]
                elif "us-phonetic" in basic_info:
                    phonetic = f"美式: {basic_info['us-phonetic']}"
                    if "uk-phonetic" in basic_info:
                        phonetic = f"英式: {basic_info['uk-phonetic']} {phonetic}"

            # 提取释义，按词性分组
            meaning = basic_translation
            # 优先从basic部分获取详细释义
            if "basic" in result and "explains" in result["basic"]:
                basic_explains = result["basic"]["explains"]
                meaning_parts = []
                for explain in basic_explains:
                    # 尝试分离词性和释义
                    if explain.startswith("[") and "]" in explain:
                        # 格式如 [n.] country, state; (demonyms) national
                        pos_end = explain.find("]")
                        pos_part = explain[1:pos_end]  # 提取 [ 和 ] 之间的词性
                        meaning_part = explain[pos_end+1:].strip()  # 提取释义
                        meaning_parts.append(f"{pos_part}. {meaning_part}")
                    else:
                        meaning_parts.append(explain)
                meaning = ";\n".join(meaning_parts)

            # 从ec部分获取更详细的词性信息
            if result.get("ec"):
                ec_result = result["ec"]
                if "word" in ec_result and len(ec_result["word"]) > 0:
                    word_info = ec_result["word"][0]
                    if "trs" in word_info:  # 包含词性释义信息
                        meaning_parts = []
                        for tr in word_info["trs"]:
                            pos = tr.get("pos", "未知")  # 词性，如 noun, verb 等
                            trans = tr.get("tran", "")  # 释义
                            meaning_parts.append(f"{pos}. {trans}")
                        if meaning_parts:
                            meaning = ";\n".join(meaning_parts)

            # 提取例句
            example = ""
            # 优先从ec部分获取例句
            if result.get("ec"):
                ec_result = result["ec"]
                if "word" in ec_result and len(ec_result["word"]) > 0:
                    word_info = ec_result["word"][0]
                    if "ex" in word_info and "sent" in word_info["ex"]:
                        examples = word_info["ex"]["sent"]
                        if examples:
                            example = "\n".join([f"例: {ex.get('source', '')}\n   {ex.get('target', '')}" for ex in examples[:3]])

            # 如果没有从ec获取到例句，尝试从其他字段获取
            if not example and "blng_sents" in result:
                blng_sents = result["blng_sents"]
                if blng_sents and len(blng_sents) > 0:
                    example = "\n".join([f"例: {sent.get('s', '')}\n   {sent.get('tran', '')}" for sent in blng_sents[:2]])

            # 如果还是没有例句，尝试从speak字段获取
            if not example and "speak" in result and "sentences" in result["speak"]:
                sentences = result["speak"]["sentences"]
                if sentences:
                    example = "\n".join([f"例: {sent.get('s', '')}\n   {sent.get('tran', '')}" for sent in sentences[:2]])

            # 如果还是没有例句，尝试从web_trans字段获取
            if not example and "web_trans" in result and result["web_trans"]:
                web_trans = result["web_trans"]
                if web_trans.get("web-translation"):
                    web_translations = web_trans["web-translation"]
                    if web_translations and len(web_translations) > 0:
                        first_translation = web_translations[0]
                        if first_translation.get("trans"):
                            trans_list = first_translation["trans"]
                            if trans_list and len(trans_list) > 0:
                                example = f"例: {word}\n   {trans_list[0].get('value', '')}"

            return {
                "word": word,
                "phonetic": phonetic,
                "meaning": meaning,
                "example": example,
            }

        except TranslateServiceError:
            raise
        except Exception:
            logger.exception("获取单词释义时出现未预期错误: word=%s", word)
            # 如果API调用失败，返回基本翻译
            return {
                "word": word,
                "phonetic": "",
                "meaning": basic_translation,
                "example": "",
            }

    # 新增：将单词加入单词本
    def add_to_vocabulary_book(self, word, translation):
        """将单词和翻译结果添加到单词本"""
        if word and translation:
            self.vocabulary_book.add_word(word, translation)

    def _get_error_message(self, error_code):
        """获取错误代码对应的错误信息"""
        error_messages = {
            "101": "缺少必填的参数",
            "102": "不支持的语言类型",
            "103": "翻译文本过长",
            "104": "不支持的API类型",
            "105": "不支持的签名类型",
            "106": "不支持的响应类型",
            "107": "不支持的传输加密类型",
            "108": "appKey无效",
            "109": "batchLog格式不正确",
            "110": "无相关服务的有效实例",
            "111": "开发者账号无效",
            "113": "翻译平台账号无效",
            "201": "解密失败",
            "202": "签名检验失败",
            "203": "访问IP地址不在可访问列表",
            "301": "辞典查询失败",
            "302": "翻译查询失败",
            "303": "服务端的其他异常",
            "401": "账户已经欠费",
            "411": "访问频率受限",
            "500": "系统错误"
        }
        return error_messages.get(error_code, f"未知错误 (错误代码: {error_code})")

    def get_translation_history(self):
        """获取翻译历史记录"""
        return self.translation_history

    def clear_translation_history(self):
        """清空翻译历史记录"""
        self.translation_history.clear()

    def pronounce(self, text, lang="en"):
        """调用有道语音合成API发音"""
        tts_url = "https://openapi.youdao.com/ttsapi"
        self._require_credentials()
        salt = str(uuid.uuid4())
        sign_str = self.app_key + text + salt + self.app_secret
        sign = hashlib.md5(sign_str.encode(), usedforsecurity=False).hexdigest()
        params = {
            "appKey": self.app_key,
            "q": text,
            "langType": lang,
            "salt": salt,
            "sign": sign
        }
        try:
            response = self.session.post(tts_url, data=params, headers=self.headers, timeout=10)
            if response.status_code == 200:
                with open("pronunciation.mp3", "wb") as f:
                    f.write(response.content)
                return "发音文件已保存为 pronunciation.mp3"
            else:
                raise TranslateServiceError(_service_error_message("发音请求", f"HTTP {response.status_code}"))
        except requests.exceptions.Timeout as exc:
            logger.warning("发音请求超时: lang=%s", lang)
            raise TranslateServiceError(_service_error_message("发音请求", "请求超时，请检查网络")) from exc
        except requests.exceptions.RequestException as exc:
            logger.warning("发音请求失败: lang=%s error=%s", lang, exc)
            raise TranslateServiceError(_service_error_message("发音请求", str(exc))) from exc
        except Exception as exc:
            logger.exception("发音调用出现未预期错误: lang=%s", lang)
            raise TranslateServiceError(_service_error_message("发音请求", str(exc))) from exc


# ── TranslateService（本地 AI 翻译，有道回退）─────────


class TranslateService:
    """翻译服务：优先使用本地 AI 翻译，不可用时回退到有道翻译 API。

    本地 AI 翻译基于 qwen2.5（Ollama），数据不出本机。
    有道回退保留以兼容未配置本地 AI 的用户。
    """

    def __init__(self, ai_client=None, youdao_api=None, ai_text_service=None):
        self._ai_client = ai_client
        self._youdao_api = youdao_api
        self._ai_service = ai_text_service

    @property
    def _local_available(self) -> bool:
        """本地 AI (Ollama) 是否可用。"""
        if self._ai_client is None:
            return False
        try:
            local = getattr(self._ai_client, "local", None)
            return local is not None and bool(getattr(local, "base_url", None))
        except Exception:
            return False

    def _translate_with_local_ai(self, text: str, source_lang: str, target_lang: str) -> str:
        """使用本地 AI 翻译（经 ai_text_service 端口）。"""
        if self._ai_service is not None:
            return self._ai_service.translate(text, source_lang, target_lang)
        if self._ai_client is None:
            return text
        # ← 无端口时的兼容路径（M5 删）
        prompt = (
            f"Translate the following {source_lang} text to {target_lang}. "
            f"Return only the translation, no explanation, no notes.\n\n{text}"
        )
        local = getattr(self._ai_client, "local", None)
        client = local if local is not None else self._ai_client
        resp = client.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=2000,
        )
        raw = ""
        if isinstance(resp, dict):
            choices = resp.get("choices") or []
            if choices:
                raw = (choices[0].get("message") or {}).get("content", "")
        elif isinstance(resp, str):
            raw = resp
        return raw.strip() or text

    def translate(self, text: str, source_lang: str = "auto", target_lang: str = "zh-CHS") -> str:
        """翻译文本：优先本地 AI，回退到有道 API。"""
        if not text.strip():
            return "请输入需要翻译的文本"

        # 优先本地 AI 翻译
        if self._local_available:
            try:
                result = self._translate_with_local_ai(text, source_lang, target_lang)
                if result and result != text:
                    logger.info("本地 AI 翻译完成: len=%d", len(text))
                    return result
            except Exception as exc:
                logger.warning("本地 AI 翻译失败，回退到有道: %s", exc)

        # 回退到有道
        if self._youdao_api is not None and self._youdao_api.is_configured():
            return self._youdao_api.translate(text, source_lang, target_lang)

        raise TranslateServiceError(
            _service_error_message("翻译", "未配置任何翻译服务（需要本地 AI 或有道翻译 API）"),
        )
