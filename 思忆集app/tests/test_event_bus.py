from core.events import AppEvent, EventBus


def test_event_bus_supports_wildcard_subscription():
    bus = EventBus()
    received = []

    bus.on("browser.*", lambda event: received.append(event.event_type))
    bus.emit(AppEvent("browser.summarized", payload={"url": "https://example.com"}))

    assert received == ["browser.summarized"]


def test_event_bus_tracks_recent_history():
    bus = EventBus()

    bus.emit(AppEvent("one"))
    bus.emit(AppEvent("two"))

    assert [event.event_type for event in bus.recent(2)] == ["one", "two"]