"""Regression coverage for the Gateway-owned LangGraph API runtime."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_root_makefile_no_longer_exposes_transition_gateway_targets():
    makefile = _read("Makefile")

    assert "dev-pro" not in makefile
    assert "start-pro" not in makefile
    assert "dev-daemon-pro" not in makefile
    assert "start-daemon-pro" not in makefile
    assert "docker-start-pro" not in makefile
    assert "up-pro" not in makefile
    assert not re.search(r"serve\.sh .*--gateway", makefile)
    assert "docker.sh start --gateway" not in makefile
    assert "deploy.sh --gateway" not in makefile


def test_service_launchers_always_use_gateway_runtime():
    operational_files = {
        "scripts/serve.sh": _read("scripts/serve.sh"),
        "scripts/docker.sh": _read("scripts/docker.sh"),
        "scripts/deploy.sh": _read("scripts/deploy.sh"),
        "docker/docker-compose-dev.yaml": _read("docker/docker-compose-dev.yaml"),
        "docker/docker-compose.yaml": _read("docker/docker-compose.yaml"),
    }

    for path, content in operational_files.items():
        assert "start --gateway" not in content, path
        assert "deploy.sh --gateway" not in content, path
        assert "langgraph dev" not in content, path
        assert "LANGGRAPH_UPSTREAM" not in content, path
        assert "LANGGRAPH_REWRITE" not in content, path


def test_docker_dev_mounts_mutable_configs_through_project_directory():
    compose = _read("docker/docker-compose-dev.yaml")

    assert re.search(r"^\s*-\s*\.\./:/app/project(?:\:\S+)?\s*$", compose, re.M)
    assert not re.search(r"^\s*-\s*[^\n#]*config\.yaml\s*:\s*[^\n#]*$", compose, re.M)
    assert not re.search(r"^\s*-\s*[^\n#]*extensions_config\.json\s*:\s*[^\n#]*$", compose, re.M)
    assert "DEER_FLOW_CONFIG_PATH=/app/project/config.yaml" in compose
    assert "DEER_FLOW_EXTENSIONS_CONFIG_PATH=/app/project/extensions_config.json" in compose


def test_local_dev_gateway_reload_excludes_runtime_state_with_absolute_dirs():
    serve_sh = _read("scripts/serve.sh")

    assert 'export DEER_FLOW_PROJECT_ROOT="$REPO_ROOT"' in serve_sh
    assert 'BACKEND_RUNTIME_HOME="$REPO_ROOT/backend/.deer-flow"' in serve_sh
    assert 'export DEER_FLOW_HOME="$BACKEND_RUNTIME_HOME"' in serve_sh
    # Every absolute reload-exclude must be pre-created, including backend/sandbox
    # (#3459 / #3454) — see test_uvicorn_reload_exclude.py for the mechanism.
    assert 'mkdir -p "$DEER_FLOW_HOME" "$BACKEND_RUNTIME_HOME" "$REPO_ROOT/backend/sandbox"' in serve_sh
    assert "--reload-exclude='$DEER_FLOW_HOME'" in serve_sh
    assert "--reload-exclude='$BACKEND_RUNTIME_HOME'" in serve_sh
    assert "--reload-exclude='sandbox/'" not in serve_sh
    assert "--reload-exclude='.deer-flow/'" not in serve_sh


def test_backend_container_only_exposes_gateway_port():
    dockerfile = _read("backend/Dockerfile")

    assert not re.search(r"^EXPOSE\s+.*\b2024\b", dockerfile, re.M)
    assert "langgraph: 2024" not in dockerfile
    assert re.search(r"^EXPOSE\s+8001\b", dockerfile, re.M)


def test_root_makefile_clean_does_not_reference_langgraph_server_cache():
    makefile = _read("Makefile")

    assert ".langgraph_api" not in makefile


def test_nginx_routes_official_langgraph_prefix_to_gateway_api():
    for path in ("docker/nginx/nginx.local.conf", "docker/nginx/nginx.conf"):
        content = _read(path)

        assert "/api/langgraph-compat" not in content
        assert "proxy_pass http://langgraph" not in content
        assert "rewrite ^/api/langgraph/(.*) /api/$1 break;" in content
        assert "proxy_pass http://gateway" in content or "proxy_pass http://$gateway_upstream" in content


def test_nginx_defers_cors_to_gateway_allowlist():
    for path in ("docker/nginx/nginx.local.conf", "docker/nginx/nginx.conf"):
        content = _read(path)

        assert "Access-Control-Allow-Origin" not in content
        assert "Access-Control-Allow-Methods" not in content
        assert "Access-Control-Allow-Headers" not in content
        assert "Access-Control-Allow-Credentials" not in content
        assert "proxy_hide_header 'Access-Control-Allow-" not in content
        assert "if ($request_method = 'OPTIONS')" not in content


def test_gateway_cors_configuration_uses_gateway_allowlist():
    gateway_config = _read("backend/app/gateway/config.py")
    gateway_app = _read("backend/app/gateway/app.py")
    csrf_middleware = _read("backend/app/gateway/csrf_middleware.py")

    assert not re.search(r"(?<!GATEWAY_)[\"']CORS_ORIGINS[\"']", gateway_config)
    assert "cors_origins" not in gateway_config
    assert "get_configured_cors_origins" in gateway_app
    assert "GATEWAY_CORS_ORIGINS" in csrf_middleware


def test_frontend_rewrites_langgraph_prefix_to_gateway():
    next_config = _read("frontend/next.config.js")
    api_client = _read("frontend/src/core/api/api-client.ts")

    assert "DEER_FLOW_INTERNAL_LANGGRAPH_BASE_URL" not in next_config
    assert "http://127.0.0.1:2024" not in next_config
    assert "langgraph-compat" not in api_client


# ---------------------------------------------------------------------------
# Docs / skills guards for the standalone-LangGraph-server removal.
#
# These three guards each pinned a hardcoded list of upstream DeerFlow paths
# (``.agent/skills/smoke-test/**``, ``.github/copilot-instructions.md``,
# ``backend/docs/AUTH_UPGRADE.md``, ...). This fork deleted every one of those
# files, so the ``_read`` calls raised ``FileNotFoundError`` and the guards
# stopped guarding anything at all.
#
# They now glob the doc / skill roots this fork actually ships. The intent is
# unchanged -- no shipped doc may describe the standalone LangGraph service
# (port 2024, ``langgraph dev``, ``langgraph.log``) now that the runtime is
# Gateway-embedded -- but the guard follows the tree instead of a snapshot of
# upstream's. Each guard asserts it matched at least one file, so removing an
# entire doc root fails loudly rather than passing vacuously.
# ---------------------------------------------------------------------------

_TEXT_SUFFIXES = frozenset({".md", ".sh", ".yaml", ".yml", ".txt", ".py", ".json", ".js", ".ts", ".tsx"})

_STANDALONE_SERVER_MARKERS = (
    "localhost:2024",
    "127.0.0.1:2024",
    "deer-flow-langgraph",
    "langgraph.log",
    "LangGraph service",
    "langgraph dev",
    "Starts LangGraph",
)

_TRANSITION_MODE_MARKERS = (
    "make dev-pro",
    "./scripts/deploy.sh --gateway",
    "docker compose --profile gateway",
    "`/api/langgraph/*` → LangGraph",
)


def _glob_text_files(*patterns: str) -> dict[str, str]:
    """Repo-relative path -> content for every text file matching *patterns*."""
    found: dict[str, str] = {}
    for pattern in patterns:
        for path in sorted(REPO_ROOT.glob(pattern)):
            if path.is_file() and path.suffix in _TEXT_SUFFIXES:
                found[path.relative_to(REPO_ROOT).as_posix()] = path.read_text(encoding="utf-8")
    return found


def _named_text_files(*paths: str) -> dict[str, str]:
    """Repo-relative path -> content for the named paths that exist."""
    return {path: (REPO_ROOT / path).read_text(encoding="utf-8") for path in paths if (REPO_ROOT / path).is_file()}


def _assert_markers_absent(files: dict[str, str], markers: tuple[str, ...], root: str) -> None:
    assert files, f"guard matched no files under {root} -- that doc root was removed or renamed; update this test to point at where it moved"
    for path, content in files.items():
        for marker in markers:
            assert marker not in content, f"{path} still references {marker!r}"


def test_agent_skill_docs_do_not_expect_standalone_langgraph_server():
    _assert_markers_absent(
        _glob_text_files(".agent/skills/**/*", ".github/skills/**/*", "skills/**/*"),
        _STANDALONE_SERVER_MARKERS,
        ".agent/skills, .github/skills, skills",
    )


def test_gateway_runtime_docs_do_not_reference_transition_modes():
    _assert_markers_absent(
        _glob_text_files("docs/**/*", "backend/docs/**/*"),
        _TRANSITION_MODE_MARKERS,
        "docs, backend/docs",
    )


def test_agent_instruction_docs_do_not_reference_standalone_langgraph_server():
    """Agent instruction docs must describe only the Gateway-embedded runtime
    -- no standalone LangGraph service, port 2024, or langgraph.log."""
    _assert_markers_absent(
        _named_text_files("AGENTS.md", "CLAUDE.md", "backend/AGENTS.md", "frontend/AGENTS.md"),
        _STANDALONE_SERVER_MARKERS,
        "AGENTS.md / CLAUDE.md",
    )
