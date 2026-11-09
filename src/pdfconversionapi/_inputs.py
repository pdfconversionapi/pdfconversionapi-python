"""Input files: what can be uploaded, and how its bytes are read for each attempt."""

from __future__ import annotations

import io
import os
import re
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, Union

if TYPE_CHECKING:
    from typing_extensions import TypeAlias

__all__ = ["FileInput", "SupportsRead", "UploadFile"]

_CHUNK = 64 * 1024
_FOLDERS = re.compile(r"^.*[/\\]")


class SupportsRead(Protocol):
    """A binary stream: anything with ``read(size) -> bytes``."""

    def read(self, size: int = ..., /) -> bytes: ...


def own_name(name: str) -> str:
    """The file's own name: everything after the last ``/`` or ``\\``."""
    return _FOLDERS.sub("", name)


_UNSENDABLE = str.maketrans({'"': "_", "\r": "_", "\n": "_"})


def sendable_name(name: str) -> str:
    """The name as sent in ``filename="..."``: the file's own name, with ``"``, CR and LF
    replaced by ``_`` (the API reads ``%22`` literally, and turns a quote into ``_`` itself)."""
    return own_name(name).translate(_UNSENDABLE) or "file"


class UploadFile:
    """One file to upload: where its bytes come from, and the name sent with them.

    Most calls accept a path, bytes or a binary stream directly; use these factories to set
    the name sent with bytes or a stream (the API names its output after it):

    >>> UploadFile.from_path("reports/q3.docx")          # sent as "q3.docx"
    >>> UploadFile.from_bytes(data, "q3.docx")
    >>> UploadFile.from_stream(response_body, "q3.docx")

    Only the file's own name is sent, never its folders, with ``"``, CR and LF replaced by
    ``_``. Bytes and streams without a name are sent as ``file``; an open file object is sent
    under its own file name.
    """

    __slots__ = ("_data", "_name", "_path", "_size", "_start", "_stream")

    _name: str
    _path: str | None
    _data: bytes | None
    _stream: SupportsRead | None
    _start: int
    _size: int

    def __init__(self) -> None:
        raise TypeError(
            "use UploadFile.from_path(), UploadFile.from_bytes() or UploadFile.from_stream()"
        )

    @classmethod
    def _make(cls, name: str) -> UploadFile:
        self = object.__new__(cls)
        self._name = sendable_name(name)
        self._path = None
        self._data = None
        self._stream = None
        self._start = 0
        self._size = 0
        return self

    @classmethod
    def from_path(cls, path: str | os.PathLike[str], name: str | None = None) -> UploadFile:
        """A file on disk, read again for each attempt. The name sent is the path's last part.

        A string is always a path, never the file's content.
        """
        if not isinstance(path, (str, os.PathLike)):
            raise TypeError(f"path must be a str or os.PathLike, not {type(path).__name__}")
        text = os.fsdecode(path)
        if not text:
            raise ValueError("path must not be empty")
        self = cls._make(_check_name(name) if name is not None else os.path.basename(text))
        self._path = text
        return self

    @classmethod
    def from_bytes(
        cls, data: bytes | bytearray | memoryview, name: str | None = None
    ) -> UploadFile:
        """Bytes in memory. Without a name they are sent as ``file``."""
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError(
                f"data must be bytes, bytearray or memoryview, not {type(data).__name__}"
            )
        self = cls._make(_check_name(name) if name is not None else "file")
        self._data = bytes(data)
        self._size = len(self._data)
        return self

    @classmethod
    def from_stream(cls, stream: SupportsRead, name: str | None = None) -> UploadFile:
        """A binary stream. A seekable stream is rewound for each attempt; any other stream is
        read into memory once, now, so that retries send the same bytes.

        Without a name, an open file is sent under its own file name and any other stream as
        ``file``.
        """
        read = getattr(stream, "read", None)
        if not callable(read):
            raise TypeError(f"stream must have a read() method, not {type(stream).__name__}")
        if name is None:
            stream_name = getattr(stream, "name", None)
            if isinstance(stream_name, str) and stream_name and not stream_name.startswith("<"):
                name = os.path.basename(stream_name)
            else:
                name = "file"
        self = cls._make(_check_name(name))
        if _seekable(stream):
            probe = stream.read(0)
            _check_binary(probe)
            seeker: io.IOBase = stream  # type: ignore[assignment]
            self._start = seeker.tell()
            self._size = seeker.seek(0, io.SEEK_END) - self._start
            seeker.seek(self._start)
            self._stream = stream
        else:
            chunks = []
            while True:
                chunk = stream.read(_CHUNK)
                _check_binary(chunk)
                if not chunk:
                    break
                chunks.append(chunk)
            self._data = b"".join(chunks)
            self._size = len(self._data)
        return self

    @property
    def name(self) -> str:
        """The name sent in the upload's ``filename``."""
        return self._name

    def __repr__(self) -> str:
        if self._path is not None:
            return f"UploadFile.from_path({self._path!r}, name={self._name!r})"
        kind = "from_stream" if self._stream is not None else "from_bytes"
        return f"UploadFile.{kind}(<{self._size} bytes>, name={self._name!r})"

    # Used by the multipart encoder, once per attempt.

    def _content_size(self) -> int:
        if self._path is not None:
            return os.stat(self._path).st_size
        return self._size

    def _chunks(self, size: int) -> Iterator[bytes]:
        """Yields exactly ``size`` bytes of content, read from the start."""
        if self._data is not None:
            view = memoryview(self._data)
            for offset in range(0, len(view), _CHUNK):
                yield bytes(view[offset : offset + _CHUNK])
            return
        if self._path is not None:
            with open(self._path, "rb") as f:
                yield from _read_exactly(f, size, self._path)
            return
        stream = self._stream
        assert stream is not None
        seeker: io.IOBase = stream  # type: ignore[assignment]
        seeker.seek(self._start)
        yield from _read_exactly(stream, size, self._name)


FileInput: TypeAlias = Union[
    str, "os.PathLike[str]", bytes, bytearray, memoryview, SupportsRead, UploadFile
]
"""One input file: a path (``str`` or ``os.PathLike``), bytes, a binary stream, or an
:class:`UploadFile`. A ``str`` is always a path."""


def _check_name(name: object) -> str:
    if not isinstance(name, str):
        raise TypeError(f"name must be a str, not {type(name).__name__}")
    return name


def _check_binary(chunk: object) -> None:
    if not isinstance(chunk, (bytes, bytearray)):
        raise TypeError("streams must be opened in binary mode ('rb') and return bytes from read()")


def _seekable(stream: object) -> bool:
    seekable = getattr(stream, "seekable", None)
    try:
        return bool(callable(seekable) and seekable())
    except (OSError, ValueError):
        return False


def _read_exactly(stream: SupportsRead, size: int, label: str) -> Iterator[bytes]:
    remaining = size
    while remaining > 0:
        chunk = stream.read(min(_CHUNK, remaining))
        if not chunk:
            raise OSError(f"{label} became shorter while it was being uploaded")
        remaining -= len(chunk)
        yield bytes(chunk)


def to_upload(value: object, what: str) -> UploadFile:
    """Turns one accepted input form into an UploadFile. A str is a path."""
    if isinstance(value, UploadFile):
        return value
    if isinstance(value, (str, os.PathLike)):
        return UploadFile.from_path(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return UploadFile.from_bytes(value)
    if callable(getattr(value, "read", None)):
        return UploadFile.from_stream(value)  # type: ignore[arg-type]
    raise TypeError(
        f"{what} must be a path (str or os.PathLike), bytes, a binary stream or an UploadFile, "
        f"not {type(value).__name__}"
    )


def is_single_input(value: object) -> bool:
    return isinstance(value, (UploadFile, str, os.PathLike, bytes, bytearray, memoryview)) or (
        callable(getattr(value, "read", None))
    )


def to_uploads(files: object) -> list[UploadFile]:
    """One input, a list of inputs, or None (no file), as a list."""
    if files is None:
        return []
    if is_single_input(files):
        return [to_upload(files, "files")]
    if isinstance(files, Sequence):
        return [to_upload(item, "each item of files") for item in files]
    raise TypeError(
        "files must be a path, bytes, a binary stream, an UploadFile, or a list of these, "
        f"not {type(files).__name__}"
    )


def path_extension(path: str | os.PathLike[str]) -> str:
    """The lower-case extension of a path, without the dot. ValueError when there is none."""
    if not isinstance(path, (str, os.PathLike)):
        raise TypeError(f"path must be a str or os.PathLike, not {type(path).__name__}")
    suffix = Path(os.fsdecode(path)).suffix
    if len(suffix) < 2:
        raise ValueError(
            f"can't tell the format of {os.fsdecode(path)!r}: it has no extension; "
            "use convert() and name the format"
        )
    return suffix[1:].lower()
