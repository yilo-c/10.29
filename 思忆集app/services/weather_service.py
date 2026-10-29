from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import urlencode

from core.settings_schema import KEY_WEATHER_CITY, KEY_WEATHER_CITY_LABEL


class WeatherService:
    def __init__(self, settings_repository, default_city, default_city_label, cache_minutes=30):
        self._settings_repository = settings_repository
        self._default_city = default_city
        self._default_city_label = default_city_label
        self._cache_minutes = cache_minutes

    def get_snapshot(self, now, snapshot, force_refresh=False):
        fallback = self._build_weather_snapshot(now, snapshot)
        city = self._default_city
        city_label = self._default_city_label
        if self._settings_repository is not None:
            city = (
                str(self._settings_repository.get_setting(KEY_WEATHER_CITY, self._default_city) or self._default_city).strip() or self._default_city
            )
            city_label = (
                str(
                    self._settings_repository.get_setting(
                        KEY_WEATHER_CITY_LABEL,
                        city if city != self._default_city else self._default_city_label,
                    )
                    or city
                ).strip()
                or city
            )

        cache_payload = None if force_refresh else self._load_weather_cache(city)
        if cache_payload is not None:
            return self._merge_weather_with_hint(cache_payload, fallback, city_label)

        live_payload = self._fetch_live_weather(city, city_label)
        if live_payload is not None:
            self._save_weather_cache(city, live_payload)
            return self._merge_weather_with_hint(live_payload, fallback, city_label)
        return fallback

    def _build_weather_snapshot(self, now, snapshot):
        month = now.month
        hour = now.hour
        # U0 实测（2026-09-19）：chat_context_anchor 以空 snapshot 调用 →
        # KeyError 'pending_tasks' 每条消息刷 Traceback。snapshot 是可选提示，
        # 缺键按 0 处理。
        pending_count = len(snapshot.get("pending_tasks") or [])
        if month in (12, 1, 2):
            season_text = "冬日"
            base_tip = "适合把任务切小一点，留出一点热饮和整理时间。"
        elif month in (3, 4, 5):
            season_text = "春天"
            base_tip = "适合做一点开始型动作，比如记一条新笔记或开一个新任务。"
        elif month in (6, 7, 8):
            season_text = "夏天"
            base_tip = "适合先做短任务，避免把注意力一次拉得太长。"
        else:
            season_text = "秋天"
            base_tip = "适合整理和收束，把零散记录沉淀成更稳的内容。"

        if hour < 11:
            daypart = "上午"
            mood = "适合启动"
        elif hour < 17:
            daypart = "下午"
            mood = "适合推进"
        else:
            daypart = "晚上"
            mood = "适合收尾"

        if pending_count >= 5:
            task_hint = f"当前还有 {pending_count} 个待办，今天更适合先清主线。"
        elif pending_count:
            task_hint = f"待办还有 {pending_count} 个，节奏还算可控。"
        else:
            task_hint = "待办压力不高，可以留一点时间给记录和整理。"

        return {
            "title": f"{season_text}{daypart}",
            "value": now.strftime("%m 月 %d 日 %H:%M"),
            "meta": f"{mood} · {task_hint}",
            "tip": base_tip,
        }

    def _load_weather_cache(self, city):
        if self._settings_repository is None:
            return None
        cache = self._settings_repository.get_setting("weather_cache", {})
        if not isinstance(cache, dict):
            return None
        if cache.get("city") != city:
            return None
        fetched_at = str(cache.get("fetched_at") or "").strip()
        if not fetched_at:
            return None
        try:
            cached_time = datetime.fromisoformat(fetched_at)
        except ValueError:
            return None
        if (datetime.now() - cached_time).total_seconds() > self._cache_minutes * 60:
            return None
        payload = cache.get("payload")
        return payload if isinstance(payload, dict) else None

    def _save_weather_cache(self, city, payload):
        if self._settings_repository is None:
            return
        self._settings_repository.set_setting(
            "weather_cache",
            {
                "city": city,
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
                "payload": payload,
            },
        )

    def _fetch_live_weather(self, city, city_label):
        try:
            geo_data = self._fetch_json(
                "https://geocoding-api.open-meteo.com/v1/search?" + urlencode({"name": city, "count": 1, "language": "zh", "format": "json"}),
                timeout=1.5,
            )
            results = geo_data.get("results") or []
            if not results:
                return None
            first_result = results[0]
            latitude = first_result.get("latitude")
            longitude = first_result.get("longitude")
            if latitude is None or longitude is None:
                return None
            weather_data = self._fetch_json(
                "https://api.open-meteo.com/v1/forecast?"
                + urlencode(
                    {
                        "latitude": latitude,
                        "longitude": longitude,
                        "current": "temperature_2m,apparent_temperature,weather_code,is_day,wind_speed_10m",
                        "timezone": "auto",
                    }
                ),
                timeout=1.5,
            )
            current = weather_data.get("current") or {}
            temperature = current.get("temperature_2m")
            apparent_temperature = current.get("apparent_temperature")
            weather_code = current.get("weather_code")
            wind_speed = current.get("wind_speed_10m")
            if temperature is None or weather_code is None:
                return None
            condition_text = self._map_weather_code(weather_code, bool(current.get("is_day", 1)))
            title = f"{city_label} · {condition_text}"
            value = f"{round(float(temperature))}C"
            meta = f"体感 {round(float(apparent_temperature or temperature))}C · 风速 {round(float(wind_speed or 0))} km/h"
            tip = self._build_weather_tip_from_condition(condition_text, float(temperature), float(apparent_temperature or temperature))
            return {"title": title, "value": value, "meta": meta, "tip": tip}
        except Exception:
            return None

    def _fetch_json(self, url, timeout=1.5):
        """抓取 JSON 响应（含 SSRF 防护，P0-5 修复）。

        weather 调用 open-meteo / wttr.in 的远端 URL，理论上是固定常量，但
        url 由 _fetch_geo 等拼装 ?name= 参数后传入，防止配置/未来改动引入
        内网地址作为攻击面。
        """
        # 延迟 import 避免循环依赖
        from services.url_safety import SSRFBlocked, safe_urlopen

        try:
            with safe_urlopen(url, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except SSRFBlocked as exc:
            # 被防护拦截 → 视为请求失败（调用方已 try/except None 路径）
            raise OSError(f"weather URL blocked by SSRF guard: {exc}")

    def _merge_weather_with_hint(self, live_payload, fallback_payload, city_label):
        merged = dict(fallback_payload)
        merged.update(live_payload)
        merged["meta"] = f"{city_label} · {live_payload.get('meta', '')}".strip(" ·")
        return merged

    def _map_weather_code(self, code, is_daytime):
        code = int(code)
        mapping = {
            0: "晴",
            1: "少云",
            2: "多云",
            3: "阴",
            45: "雾",
            48: "雾凇",
            51: "小毛雨",
            53: "毛雨",
            55: "强毛雨",
            61: "小雨",
            63: "中雨",
            65: "大雨",
            71: "小雪",
            73: "中雪",
            75: "大雪",
            80: "阵雨",
            81: "强阵雨",
            82: "暴雨",
            95: "雷暴",
        }
        text = mapping.get(code, "天气平稳")
        if code == 0 and not is_daytime:
            return "晴夜"
        return text

    def _build_weather_tip_from_condition(self, condition_text, temperature, apparent_temperature):
        if "雨" in condition_text or "雷" in condition_text:
            return "今天出门更适合带伞，主页里的任务也尽量按轻重排一下。"
        if "雪" in condition_text:
            return "天气偏冷，适合把需要专注的事放在室内稳定推进。"
        if temperature >= 30:
            return "气温偏高，适合把重任务拆短，给自己留一点缓冲。"
        if apparent_temperature <= 5:
            return "体感偏冷，适合先做启动成本低的事情，把节奏慢慢拉起来。"
        if "晴" in condition_text:
            return "天气不错，适合把需要出门或需要切换场景的事排进去。"
        return "天气比较平稳，适合按既定节奏推进今天的安排。"
