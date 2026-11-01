import core.events as events
from core.config import settings


def test_publish_event_is_noop_without_gcp_project(monkeypatch, caplog):
    monkeypatch.setattr(settings, "gcp_project_id", "")
    events._publisher.cache_clear()

    with caplog.at_level("INFO"):
        events.publish_event("signal_received", {"symbol": "BTC/USD"})

    assert "event: signal_received" in caplog.text


def test_publish_event_never_raises_when_publisher_fails(monkeypatch):
    monkeypatch.setattr(settings, "gcp_project_id", "some-project")
    events._publisher.cache_clear()

    class BoomPublisher:
        def topic_path(self, project, topic):
            return f"projects/{project}/topics/{topic}"

        def publish(self, *args, **kwargs):
            raise RuntimeError("Pub/Sub is down")

    monkeypatch.setattr(events, "_publisher", lambda: BoomPublisher())

    # Should not raise despite the publisher blowing up.
    events.publish_event("trade_executed", {"trade_id": "abc123"})


def test_publish_event_calls_publisher_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "gcp_project_id", "some-project")
    monkeypatch.setattr(settings, "pubsub_events_topic", "trading-bot-events")
    events._publisher.cache_clear()

    calls = []

    class FakePublisher:
        def topic_path(self, project, topic):
            return f"projects/{project}/topics/{topic}"

        def publish(self, topic_path, data, **attrs):
            calls.append((topic_path, data, attrs))

    monkeypatch.setattr(events, "_publisher", lambda: FakePublisher())

    events.publish_event("decision_made", {"symbol": "ETH/USD", "action": "hold"})

    assert len(calls) == 1
    topic_path, data, attrs = calls[0]
    assert topic_path == "projects/some-project/topics/trading-bot-events"
    assert b"decision_made" in data
    assert attrs["event_type"] == "decision_made"
