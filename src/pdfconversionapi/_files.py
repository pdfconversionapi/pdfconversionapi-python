"""Writing results to disk safely."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from urllib.parse import unquote

import httpx

from ._exceptions import InvalidResponseError
from ._inputs import own_name


def safe_file_name(name: str) -> str:
    """The file's own name, refusing names that would leave the folder or be empty."""
    own = own_name(name)
    if own in ("", ".", "..") or "\x00" in own:
        raise InvalidResponseError(f"refusing to write a file named {name!r}")
    return own


def save_target(path: str | os.PathLike[str], name: str) -> Path:
    """``path`` itself, or ``name`` inside it when ``path`` is an existing folder."""
    target = Path(path)
    return target / safe_file_name(name) if target.is_dir() else target


def url_file_name(url: str) -> str:
    return unquote(httpx.URL(url).path.rsplit("/", 1)[-1])


def discard_partial(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()
