"""The errors this library raises.

Every error is a :class:`PdfConversionApiError`. Bad arguments raise the built-in
:class:`ValueError` or :class:`TypeError` instead, before anything is sent.
"""

from __future__ import annotations

__all__ = [
    "APIConnectionError",
    "APITimeoutError",
    "ApiError",
    "AuthenticationError",
    "BadRequestError",
    "GatewayTimeoutError",
    "InternalServerError",
    "InvalidResponseError",
    "MissingApiKeyError",
    "NotFoundError",
    "PayloadTooLargeError",
    "PdfConversionApiError",
    "PermissionDeniedError",
    "RateLimitError",
    "ServiceUnavailableError",
    "StoredFileError",
    "UnprocessableEntityError",
]


def _with_request_id(text: str, request_id: str | None) -> str:
    return f"{text} (request id: {request_id})" if request_id else text


class PdfConversionApiError(Exception):
    """The base class of every error raised by this library."""

    message: str

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class MissingApiKeyError(PdfConversionApiError):
    """A call that needs an API key was made without one. Nothing was sent.

    Pass ``api_key=`` to the client or set the ``PDFCONVERSIONAPI_KEY`` environment variable.
    """


class StoredFileError(PdfConversionApiError):
    """The data of a stored file (``store_file=True``) was read. Call ``download()`` instead."""


class APIConnectionError(PdfConversionApiError):
    """No response came back: the connection failed or was closed, after every retry."""


class APITimeoutError(PdfConversionApiError):
    """The client's own timeout ran out. Never retried.

    A conversion may still be running on the server and counts toward the month if it
    succeeds, so raise ``timeout`` rather than retrying at once.
    """


class InvalidResponseError(PdfConversionApiError):
    """A successful response whose body can't be used: bad JSON, no ``Files``, a size mismatch."""

    request_id: str | None

    def __init__(self, message: str, *, request_id: str | None = None) -> None:
        super().__init__(message)
        self.request_id = request_id

    def __str__(self) -> str:
        return _with_request_id(self.message, self.request_id)


class ApiError(PdfConversionApiError):
    """The API answered with an error status.

    Attributes:
        status: the HTTP status code.
        message: the API's message, or a description of a body that had none.
        request_id: the ``x-request-id`` header, or ``None`` when the response had none
            (such responses come from the network in front of the API).
        retry_after: the ``Retry-After`` header in seconds (sent on 429 and 503), else ``None``.
        body: the raw response body, for debugging.
    """

    status: int
    request_id: str | None
    retry_after: int | None
    body: str

    def __init__(
        self,
        status: int,
        message: str,
        *,
        request_id: str | None = None,
        retry_after: int | None = None,
        body: str = "",
    ) -> None:
        # args holds (status, message) so that pickling rebuilds the error; the keyword
        # attributes travel in __dict__.
        Exception.__init__(self, status, message)
        self.status = status
        self.message = message
        self.request_id = request_id
        self.retry_after = retry_after
        self.body = body

    def __str__(self) -> str:
        return _with_request_id(f"HTTP {self.status}: {self.message}", self.request_id)


class BadRequestError(ApiError):
    """400: the request is malformed, or a parameter has a value the conversion can't use."""


class AuthenticationError(ApiError):
    """401: the API key is missing, unknown or no longer valid."""


class PermissionDeniedError(ApiError):
    """403: the key's plan isn't set up."""


class NotFoundError(ApiError):
    """404: an unsupported conversion, or a stored file that expired or was deleted."""


class PayloadTooLargeError(ApiError):
    """413: the request, an image or a page is over a limit."""


class UnprocessableEntityError(ApiError):
    """422: the file can't be read or converted as asked."""


class RateLimitError(ApiError):
    """429: too many requests, too many conversions at once, or the month's conversions used up."""


class InternalServerError(ApiError):
    """500: an unexpected failure on the server. Retried once."""


class ServiceUnavailableError(ApiError):
    """503: the servers are busy, or conversions on the plan are paused."""


class GatewayTimeoutError(ApiError):
    """504: the conversion ran out of time on the server. Not retried."""


ERRORS_BY_STATUS: dict[int, type[ApiError]] = {
    400: BadRequestError,
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    413: PayloadTooLargeError,
    422: UnprocessableEntityError,
    429: RateLimitError,
    500: InternalServerError,
    503: ServiceUnavailableError,
    504: GatewayTimeoutError,
}
