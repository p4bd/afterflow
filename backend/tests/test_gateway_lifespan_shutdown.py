"""Gateway lifespan regressions retained by the focused AfterFlow build."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI


@asynccontextmanager
async def _noop_langgraph_runtime(_app, _startup_config):
    yield


async def _run_lifespan_with_upload_staging_cleanup():
    from app.gateway.app import lifespan

    app = FastAPI()
    startup_config = SimpleNamespace(
        log_level="INFO",
        memory=SimpleNamespace(token_counting="char"),
        scheduler=SimpleNamespace(
            enabled=False,
            poll_interval_seconds=1,
            lease_seconds=30,
            max_concurrent_runs=1,
        ),
    )
    cleanup_upload_staging_files = MagicMock(return_value=2)
    close_oidc_service = AsyncMock()

    with (
        patch("app.gateway.app.get_app_config", return_value=startup_config),
        patch(
            "app.gateway.app.get_gateway_config",
            return_value=MagicMock(host="x", port=0),
        ),
        patch("app.gateway.app.langgraph_runtime", _noop_langgraph_runtime),
        patch(
            "app.gateway.app.cleanup_stale_upload_staging_files",
            cleanup_upload_staging_files,
        ),
        patch("app.gateway.app.auth.close_oidc_service", close_oidc_service),
    ):
        async with lifespan(app):
            pass

    return cleanup_upload_staging_files, close_oidc_service


def test_lifespan_sweeps_upload_staging_files_on_startup():
    cleanup_upload_staging_files, close_oidc_service = asyncio.run(_run_lifespan_with_upload_staging_cleanup())

    cleanup_upload_staging_files.assert_called_once_with()
    close_oidc_service.assert_awaited_once()
