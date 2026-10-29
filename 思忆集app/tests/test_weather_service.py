from datetime import datetime

from services.weather_service import WeatherService


class FakeSettingsRepository:
    def __init__(self, values=None):
        self._values = dict(values or {})

    def get_setting(self, key, default=None):
        return self._values.get(key, default)

    def set_setting(self, key, value):
        self._values[key] = value


class OfflineWeatherService(WeatherService):
    def _fetch_live_weather(self, city, city_label):
        return None


def test_weather_service_uses_cached_payload_when_available():
    repo = FakeSettingsRepository(
        {
            "weather_cache": {
                "city": "Beijing",
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
                "payload": {
                    "title": "北京 · 晴",
                    "value": "26°C",
                    "meta": "体感 26°C · 风速 2 km/h",
                    "tip": "天气不错",
                },
            }
        }
    )
    service = WeatherService(repo, default_city="Beijing", default_city_label="北京")

    result = service.get_snapshot(datetime(2026, 4, 7, 9, 0), {"pending_tasks": []})

    assert result["title"] == "北京 · 晴"
    assert result["value"] == "26°C"


def test_weather_service_falls_back_without_live_data():
    service = OfflineWeatherService(None, default_city="Beijing", default_city_label="北京")

    result = service.get_snapshot(datetime(2026, 4, 7, 20, 0), {"pending_tasks": [{"id": 1}]}, force_refresh=True)

    assert result["title"]
    assert result["value"]
    assert "待办" in result["meta"]