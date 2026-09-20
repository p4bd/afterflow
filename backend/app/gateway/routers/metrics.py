"""Prometheus-format ``/metrics`` endpoint.

The real Gateway auth middleware protects ``GET /metrics`` because it is not
on the public-path allowlist. The bundled Nginx config also does not proxy
``/metrics`` to the Gateway. A direct authenticated Gateway scrape works;
deployments that expose it to a scraper must add an explicit protected route.

The body is the Prometheus text exposition format produced by
``app.after_sales.metrics.render()``. We use ``PlainTextResponse`` so
the ``Content-Type`` is ``text/plain; charset=utf-8`` and the body is
served verbatim with no JSON encoding.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.after_sales.metrics import get_metrics

router = APIRouter(tags=["metrics"])


@router.get("/metrics", response_class=PlainTextResponse)
def metrics() -> PlainTextResponse:
    """Return the current AfterFlow counters in Prometheus text format."""
    return PlainTextResponse(get_metrics().render())
