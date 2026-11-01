"""Event publishing via GCP Pub/Sub -- the Event-Driven piece of the architecture.

Purely additive: the pipeline still runs synchronously end-to-end (Decision ->
Risk -> Execution -> Journal -> Notify) exactly as before. Publishing these events
alongside that just makes the pipeline's key moments observable to anything else
that cares, without those subscribers needing to be known here -- that decoupling
is what makes it Choreography-capable at the edges, even though the core money-
moving flow stays deliberately Orchestrated (see core/pipeline.py) for
predictability and debuggability.

Graceful no-op if GCP_PROJECT_ID isn't set or the topic doesn't exist yet (e.g.
local dev, or before the GCP deployment step creates it) -- same pattern as the
GCS mirroring in core/storage.py and Cloud Logging in core/cloud_logging.py.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any, Optional

from core.config import settings

logger = logging.getLogger("events")


@lru_cache(maxsize=1)
def _publisher():
    if not settings.gcp_project_id:
        return None
    try:
        from google.cloud import pubsub_v1

        return pubsub_v1.PublisherClient()
    except Exception:
        logger.warning("Pub/Sub client unavailable; events will only be logged locally", exc_info=True)
        return None


def publish_event(event_type: str, payload: dict[str, Any]) -> None:
    """Fire-and-forget -- never raises, never blocks the pipeline on Pub/Sub being
    down or not yet configured. event_type examples: signal_received,
    decision_made, trade_executed."""
    logger.info("event: %s %s", event_type, payload)

    publisher = _publisher()
    if publisher is None:
        return

    topic_path = publisher.topic_path(settings.gcp_project_id, settings.pubsub_events_topic)
    data = json.dumps({"event_type": event_type, **payload}).encode("utf-8")
    try:
        publisher.publish(topic_path, data, event_type=event_type)
    except Exception:
        logger.warning("Failed to publish event %s to Pub/Sub", event_type, exc_info=True)
