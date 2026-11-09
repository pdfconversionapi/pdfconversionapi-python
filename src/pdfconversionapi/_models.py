"""Results: converted files, usage and the list of conversions."""

from __future__ import annotations

import os
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Generic, Protocol, TypeVar

from ._exceptions import StoredFileError
from ._files import safe_file_name, save_target

if TYPE_CHECKING:
    from ._core import FileEntry

__all__ = [
    "AsyncConversionResult",
    "AsyncOutputFile",
    "Conversion",
    "ConversionResult",
    "OutputFile",
    "Usage",
]


@dataclass(frozen=True)
class Usage:
    """Your plan, its limits and this month's count (``GET /v2/usage``)."""

    plan: str
    """The plan's name, for example ``Free``."""
    included: int
    """Successful conversions included each calendar month."""
    used: int
    """Successful conversions so far this month."""
    remaining: int | None
    """What is left of ``included`` this month."""
    overage: bool
    """Whether the plan keeps converting past ``included``."""
    running: int
    """Conversions running for the account right now."""
    concurrent_limit: int
    """How many conversions the plan runs at the same time."""
    max_upload_mb: int
    """The largest request the plan accepts, all files together, in MB."""
    period_start: datetime
    """The start of this month's count (UTC)."""
    period_end: datetime
    """The end of this month's count (UTC); the count starts again then."""


@dataclass(frozen=True)
class Conversion:
    """One conversion the API offers (an entry of ``GET /v2/info``)."""

    from_format: str
    """The format in the path's ``{from}``, such as ``docx``."""
    to_format: str
    """The format or tool in the path's ``{to}``, such as ``pdf`` or ``merge``."""
    title: str
    category: str
    inputs: str
    """How many files it takes: ``one``, ``many`` or ``optional``."""


class _SyncFetcher(Protocol):
    def download(self, url: str, *, timeout: float | None = None) -> bytes: ...

    def download_to(
        self, url: str, path: str | os.PathLike[str], *, timeout: float | None = None
    ) -> Path: ...


class _AsyncFetcher(Protocol):
    def download(self, url: str, *, timeout: float | None = None) -> Awaitable[bytes]: ...

    def download_to(
        self, url: str, path: str | os.PathLike[str], *, timeout: float | None = None
    ) -> Awaitable[Path]: ...


class _OutputFileBase:
    __slots__ = ("_entry",)

    def __init__(self, entry: FileEntry) -> None:
        self._entry = entry

    @property
    def name(self) -> str:
        """The file's name, extension included, as the API returned it."""
        return self._entry.name

    @property
    def ext(self) -> str:
        """The extension in lower case, without the dot: ``pdf``, ``jpg``."""
        return self._entry.ext

    @property
    def size(self) -> int:
        """The size in bytes."""
        return self._entry.size

    @property
    def stored(self) -> bool:
        """True when the file was stored (``store_file=True``): it has a ``url`` instead of data."""
        return self._entry.data is None

    @property
    def url(self) -> str | None:
        """The download link of a stored file (works for 3 hours, needs no key), else None."""
        return self._entry.url

    @property
    def file_id(self) -> str | None:
        """The id of a stored file, for ``delete_stored_file()``, else None."""
        return self._entry.file_id

    @property
    def data(self) -> bytes:
        """The file's bytes. For a stored file this raises StoredFileError: use ``download()``."""
        data = self._entry.data
        if data is None:
            raise StoredFileError(
                f"{self.name} is stored on the server (store_file=True), so the response has "
                "no data: call download() or save() to fetch it"
            )
        return data

    def __repr__(self) -> str:
        where = f", url={self.url!r}" if self.stored else ""
        return (
            f"{type(self).__name__}(name={self.name!r}, ext={self.ext!r}, size={self.size}, "
            f"stored={self.stored}{where})"
        )


class OutputFile(_OutputFileBase):
    """One converted file."""

    __slots__ = ("_client",)

    def __init__(self, entry: FileEntry, client: _SyncFetcher) -> None:
        super().__init__(entry)
        self._client = client

    def download(self) -> bytes:
        """The file's bytes: fetched from ``url`` for a stored file (no key sent), else ``data``."""
        if self._entry.url is not None and self._entry.data is None:
            return self._client.download(self._entry.url)
        return self.data

    def save(self, path: str | os.PathLike[str]) -> Path:
        """Writes the file and returns the path written.

        If ``path`` is an existing folder, the file is written inside it under its own name.
        An existing file is overwritten. A stored file is downloaded, streamed to disk.
        """
        target = save_target(path, self.name)
        if self._entry.url is not None and self._entry.data is None:
            return self._client.download_to(self._entry.url, target)
        target.write_bytes(self.data)
        return target

    def download_to(self, path: str | os.PathLike[str]) -> Path:
        """The same as :meth:`save`."""
        return self.save(path)


class AsyncOutputFile(_OutputFileBase):
    """One converted file, from :class:`AsyncPdfConversionApi`."""

    __slots__ = ("_client",)

    def __init__(self, entry: FileEntry, client: _AsyncFetcher) -> None:
        super().__init__(entry)
        self._client = client

    async def download(self) -> bytes:
        """The file's bytes: fetched from ``url`` for a stored file (no key sent), else ``data``."""
        if self._entry.url is not None and self._entry.data is None:
            return await self._client.download(self._entry.url)
        return self.data

    async def save(self, path: str | os.PathLike[str]) -> Path:
        """Writes the file and returns the path written.

        If ``path`` is an existing folder, the file is written inside it under its own name.
        An existing file is overwritten. A stored file is downloaded, streamed to disk.
        """
        target = save_target(path, self.name)
        if self._entry.url is not None and self._entry.data is None:
            return await self._client.download_to(self._entry.url, target)
        target.write_bytes(self.data)
        return target

    async def download_to(self, path: str | os.PathLike[str]) -> Path:
        """The same as :meth:`save`."""
        return await self.save(path)


F = TypeVar("F", bound=_OutputFileBase)


class _ConversionResultBase(Generic[F]):
    __slots__ = ("_conversion_cost", "_conversion_time", "_files", "_request_id")

    def __init__(
        self,
        files: list[F],
        conversion_cost: int,
        conversion_time: float,
        request_id: str | None,
    ) -> None:
        self._files = files
        self._conversion_cost = conversion_cost
        self._conversion_time = conversion_time
        self._request_id = request_id

    @property
    def files(self) -> list[F]:
        """Every output file, in the API's order."""
        return list(self._files)

    @property
    def file(self) -> F:
        """The first output file: the only one for most conversions."""
        return self._files[0]

    @property
    def conversion_cost(self) -> int:
        """Conversions this request used (always 1)."""
        return self._conversion_cost

    @property
    def conversion_time(self) -> float:
        """Seconds the conversion took on the server, excluding upload, queueing and download."""
        return self._conversion_time

    @property
    def request_id(self) -> str | None:
        """The ``x-request-id`` of the response: quote it when you ask support about a call."""
        return self._request_id

    def __repr__(self) -> str:
        names = ", ".join(repr(f.name) for f in self._files)
        return (
            f"{type(self).__name__}(files=[{names}], conversion_cost={self.conversion_cost}, "
            f"conversion_time={self.conversion_time}, request_id={self.request_id!r})"
        )


class ConversionResult(_ConversionResultBase[OutputFile]):
    """What a conversion returned."""

    __slots__ = ()

    def save_all(self, directory: str | os.PathLike[str]) -> list[Path]:
        """Writes every file into ``directory`` under its own name and returns the paths.

        Creates the folder if needed, overwrites existing files, and downloads stored files.
        """
        folder = Path(directory)
        folder.mkdir(parents=True, exist_ok=True)
        return [f.save(folder / safe_file_name(f.name)) for f in self._files]


class AsyncConversionResult(_ConversionResultBase[AsyncOutputFile]):
    """What a conversion returned, from :class:`AsyncPdfConversionApi`."""

    __slots__ = ()

    async def save_all(self, directory: str | os.PathLike[str]) -> list[Path]:
        """Writes every file into ``directory`` under its own name and returns the paths.

        Creates the folder if needed, overwrites existing files, and downloads stored files.
        """
        folder = Path(directory)
        folder.mkdir(parents=True, exist_ok=True)
        return [await f.save(folder / safe_file_name(f.name)) for f in self._files]
