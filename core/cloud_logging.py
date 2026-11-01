"""Optional GCP Cloud Logging integration.

Only activates when GCP_PROJECT_ID is set and credentials are actually available
(the VM's attached service account once deployed, or local Application Default
Credentials if you've run `gcloud auth application-default login`). Falls back
to plain local logging otherwise -- same graceful-degradation pattern as the GCS
mirroring in core/storage.py, so local dev never needs GCP credentials.
"""

from __future__ import annotations

import logging

from core.config import settings

logger = logging.getLogger("cloud_logging")


def setup_cloud_logging() -> bool:
    """Attaches a Cloud Logging handler to the root logger. Returns True if it
    actually activated, False if it fell back to local-only logging."""
    if not settings.gcp_project_id:
        logger.info("GCP_PROJECT_ID not set; logging locally only")
        return False

    try:
        import google.cloud.logging as gcp_logging

        client = gcp_logging.Client(project=settings.gcp_project_id)
        client.setup_logging(log_level=logging.INFO)
        logger.info("Cloud Logging active (project=%s)", settings.gcp_project_id)
        return True
    except Exception:
        logger.warning("Cloud Logging unavailable; continuing with local-only logging", exc_info=True)
        return False
