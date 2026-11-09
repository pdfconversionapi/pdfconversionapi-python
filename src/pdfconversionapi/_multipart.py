"""The multipart/form-data body, written by hand.

Stock encoders differ on file names: some write only ``filename*=``, percent-encode, or
MIME-encode non-ASCII names, and httpx doubles backslashes. The API reads only a plain
``filename="..."`` in raw UTF-8, so this module writes the parts itself: file names in raw
UTF-8 as UploadFile.name gives them (``"``, CR and LF already replaced by ``_``), and field
names with ``"``, CR and LF escaped as ``%22``, ``%0D`` and ``%0A`` so the header stays intact
(the API refuses such names with a 400).

The body is streamed with a known length, so a large file is never copied into memory, and a
file on disk is read again for each attempt.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from typing import Union

from ._inputs import UploadFile

__all__ = ["MultipartBody"]

FieldValue = Union[str, UploadFile]

_ESCAPES = str.maketrans({'"': "%22", "\r": "%0D", "\n": "%0A"})


def _quote(value: str) -> str:
    return value.translate(_ESCAPES)


class MultipartBody:
    """One attempt's request body. Build a new one for each attempt."""

    def __init__(self, fields: Sequence[tuple[str, FieldValue]]) -> None:
        self.boundary = secrets.token_hex(16)
        self.content_type = f"multipart/form-data; boundary={self.boundary}"
        delimiter = f"--{self.boundary}\r\n".encode()
        self._pieces: list[bytes | tuple[UploadFile, int]] = []
        length = 0
        for name, value in fields:
            if isinstance(value, UploadFile):
                head = (
                    f'Content-Disposition: form-data; name="{_quote(name)}"; '
                    f'filename="{value.name}"\r\n'
                    "Content-Type: application/octet-stream\r\n\r\n"
                ).encode()
                size = value._content_size()
                self._pieces += [delimiter + head, (value, size), b"\r\n"]
                length += len(delimiter) + len(head) + size + 2
            else:
                part = (
                    delimiter
                    + f'Content-Disposition: form-data; name="{_quote(name)}"\r\n\r\n'.encode()
                    + value.encode()
                    + b"\r\n"
                )
                self._pieces.append(part)
                length += len(part)
        closing = f"--{self.boundary}--\r\n".encode()
        self._pieces.append(closing)
        self.content_length = length + len(closing)

    def chunks(self, check: Callable[[], None]) -> Iterator[bytes]:
        """The body's bytes. ``check`` runs before each chunk (it raises when time is up)."""
        for piece in self._pieces:
            check()
            if isinstance(piece, bytes):
                yield piece
            else:
                upload, size = piece
                for chunk in upload._chunks(size):
                    check()
                    yield chunk

    async def achunks(self, check: Callable[[], None]) -> AsyncIterator[bytes]:
        """The same bytes for an async client."""
        for chunk in self.chunks(check):
            yield chunk
