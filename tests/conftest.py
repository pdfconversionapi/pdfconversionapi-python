from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def find_conformance_dir() -> Path:
    """$PDFCONVERSIONAPI_CONFORMANCE_DIR, else conformance/ next to tests/ (a repository of its
    own), else ../conformance (the folder of every SDK)."""
    if os.environ.get("PDFCONVERSIONAPI_CONFORMANCE_DIR"):
        return Path(os.environ["PDFCONVERSIONAPI_CONFORMANCE_DIR"])
    inside = ROOT / "conformance"
    return inside if (inside / "cases.json").exists() else ROOT.parent / "conformance"


CONFORMANCE_DIR = find_conformance_dir()
REQUIRE_CONFORMANCE = os.environ.get("PDFCONVERSIONAPI_REQUIRE_CONFORMANCE") == "1"


LOCAL_API_ENV = CONFORMANCE_DIR / ".local-api.env"
SAMPLES = Path(os.environ.get("PDFCONVERSIONAPI_SAMPLES_DIR") or CONFORMANCE_DIR / "samples")

# Read when the tests load, before _clean_env below removes the key for each test.
LIVE_BASE_URL = os.environ.get("PDFCONVERSIONAPI_LIVE_BASE_URL", "")
LIVE_KEY = os.environ.get("PDFCONVERSIONAPI_KEY", "")


def read_local_api_env() -> dict[str, str]:
    """PDFCONVERSIONAPI_LOCAL_BASE_URL and PDFCONVERSIONAPI_LOCAL_KEY. Never print them."""
    values = {}
    for line in LOCAL_API_ENV.read_text("utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, _, value = line.partition("=")
            values[name.strip().removeprefix("export ")] = value.strip().strip("'\"")
    return values


@pytest.fixture(scope="session")
def live_api() -> dict[str, str]:
    """The API the live tests call, as {"base_url": ..., "key": ...}. Never print them.

    From ../conformance/.local-api.env (the real API running locally) when it exists, else from
    PDFCONVERSIONAPI_LIVE_BASE_URL and PDFCONVERSIONAPI_KEY (the release workflow sets them for
    the production API). The test is skipped without either; the key alone doesn't start it.
    """
    if LOCAL_API_ENV.exists():
        env = read_local_api_env()
        base_url = env.get("PDFCONVERSIONAPI_LOCAL_BASE_URL", "")
        key = env.get("PDFCONVERSIONAPI_LOCAL_KEY", "")
        if not base_url or not key:
            pytest.skip(f"{LOCAL_API_ENV} lacks the base URL or the key")
        return {"base_url": base_url, "key": key}
    if LIVE_BASE_URL and LIVE_KEY:
        return {"base_url": LIVE_BASE_URL, "key": LIVE_KEY}
    pytest.skip(
        f"no {LOCAL_API_ENV}, and PDFCONVERSIONAPI_LIVE_BASE_URL or PDFCONVERSIONAPI_KEY is not set"
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test sees the caller's key or base URL unless it sets them."""
    monkeypatch.delenv("PDFCONVERSIONAPI_KEY", raising=False)
    monkeypatch.delenv("PDFCONVERSIONAPI_BASE_URL", raising=False)


@pytest.fixture(scope="session")
def mock_url() -> Iterator[str]:
    """Starts ../conformance/mock-server.mjs on a random port for the whole test run."""
    script = CONFORMANCE_DIR / "mock-server.mjs"
    if not script.exists():
        message = f"the conformance suite is not at {CONFORMANCE_DIR}"
        if REQUIRE_CONFORMANCE:
            pytest.fail(message)
        pytest.skip(message)
    node = shutil.which("node")
    if node is None:
        pytest.fail("the conformance tests need Node.js 18 or later (node) to run the mock API")
    process = subprocess.Popen(
        [node, str(script), "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        assert process.stdout is not None
        line = process.stdout.readline().strip()
        if not line.startswith("LISTENING "):
            pytest.fail(f"the mock API did not start: {line!r}")
        yield line.split(" ", 1)[1]
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        if process.stdout is not None:
            process.stdout.close()
