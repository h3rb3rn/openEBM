"""
Optional error tracking via the Sentry SDK. A no-op unless SENTRY_DSN is
configured — this app must keep working fully air-gapped/offline with no
mandatory external service, and the previous only error visibility was
"grep the container logs".

Self-hosted Sentry (or a compatible ingester like GlitchTip) is the
sovereignty-appropriate choice here, not Sentry's SaaS — SENTRY_DSN can
point at either, this module doesn't care.
"""
import logging

from ..config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def init_error_tracking() -> None:
    if not settings.sentry_dsn:
        logger.info("Error tracking disabled (SENTRY_DSN not set)")
        return

    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        traces_sample_rate=0.0,  # error tracking only, no performance/APM tracing
        send_default_pii=False,  # never send patient-adjacent request data by default
    )
    logger.info("Error tracking enabled (environment=%s)", settings.environment)
