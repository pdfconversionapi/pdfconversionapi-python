"""The part of the client that the sync and async versions share: settings and the calls."""

from __future__ import annotations

import os
from collections.abc import Mapping

from ._core import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_MAX_RETRIES,
    DEFAULT_MAX_RETRY_WAIT,
    DEFAULT_TIMEOUT,
    Call,
    ParamValue,
    Settings,
    call_timeout,
    check_file_id,
    check_format,
    check_url,
    convert_fields,
    require_key,
    resolve_settings,
)
from ._inputs import UploadFile, path_extension

__all__ = ["ClientBase"]


class ClientBase:
    """Settings and request building; holds no per-request state, so it is safe to share."""

    _settings: Settings

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        max_retry_wait: float = DEFAULT_MAX_RETRY_WAIT,
    ) -> None:
        self._settings = resolve_settings(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            connect_timeout=connect_timeout,
            max_retries=max_retries,
            max_retry_wait=max_retry_wait,
        )

    @property
    def base_url(self) -> str:
        """The API's address, without a trailing slash."""
        return self._settings.base_url

    @property
    def timeout(self) -> float:
        """Seconds each attempt may take, for the whole response."""
        return self._settings.timeout

    @property
    def connect_timeout(self) -> float:
        """Seconds to wait for a connection to open."""
        return self._settings.connect_timeout

    @property
    def max_retries(self) -> int:
        """Retries after the first attempt (section "Errors and retries" of the README)."""
        return self._settings.max_retries

    @property
    def max_retry_wait(self) -> float:
        """The longest ``Retry-After`` that is waited for; a longer one is raised at once."""
        return self._settings.max_retry_wait

    def __repr__(self) -> str:
        key = "set" if self._settings.api_key else "missing"
        return f"{type(self).__name__}(base_url={self.base_url!r}, api_key=<{key}>)"

    # Calls. Each validates its arguments and raises before anything is sent.

    def _convert_call(
        self,
        from_format: str,
        to_format: str,
        files: object,
        params: Mapping[str, ParamValue] | None,
        file_name: str | None,
        store_file: bool,
        timeout: float | None,
    ) -> Call:
        source = check_format(from_format, "from_format")
        target = check_format(to_format, "to_format")
        fields = convert_fields(files, params, file_name, store_file)
        seconds = call_timeout(self._settings, timeout)
        require_key(self._settings)
        return Call(
            "POST",
            f"{self._settings.base_url}/v2/convert/{source}/to/{target}",
            seconds,
            authorized=True,
            fields=fields,
            conversion=True,
            label=f"{source}/to/{target}",
        )

    @staticmethod
    def _file_input(path: str | os.PathLike[str]) -> tuple[str, UploadFile]:
        return path_extension(path), UploadFile.from_path(path)

    def _usage_call(self, timeout: float | None) -> Call:
        seconds = call_timeout(self._settings, timeout)
        require_key(self._settings)
        url = f"{self._settings.base_url}/v2/usage"
        return Call("GET", url, seconds, authorized=True, label="usage")

    def _conversions_call(self, timeout: float | None) -> Call:
        seconds = call_timeout(self._settings, timeout)
        url = f"{self._settings.base_url}/v2/info"
        return Call("GET", url, seconds, label="conversions")

    def _download_call(self, url: str, timeout: float | None) -> Call:
        checked = check_url(url)
        seconds = call_timeout(self._settings, timeout)
        return Call("GET", checked, seconds, accept_json=False, label="download")

    def _delete_call(self, file_id: str, timeout: float | None) -> Call:
        checked = check_file_id(file_id)
        seconds = call_timeout(self._settings, timeout)
        require_key(self._settings)
        url = f"{self._settings.base_url}/v2/d/{checked}"
        return Call("DELETE", url, seconds, authorized=True, label="delete_stored_file")
