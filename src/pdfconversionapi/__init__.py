"""The official Python client for the PDF Conversion API (https://pdfconversionapi.com).

>>> from pdfconversionapi import PdfConversionApi
>>> client = PdfConversionApi()                      # reads PDFCONVERSIONAPI_KEY
>>> result = client.convert_file("report.docx", "pdf")
>>> result.save_all("out")                           # writes out/report.pdf

Docs: https://pdfconversionapi.com/docs
"""

from ._async_client import AsyncPdfConversionApi
from ._client import PdfConversionApi
from ._core import DEFAULT_BASE_URL, ParamValue
from ._exceptions import (
    APIConnectionError,
    ApiError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    GatewayTimeoutError,
    InternalServerError,
    InvalidResponseError,
    MissingApiKeyError,
    NotFoundError,
    PayloadTooLargeError,
    PdfConversionApiError,
    PermissionDeniedError,
    RateLimitError,
    ServiceUnavailableError,
    StoredFileError,
    UnprocessableEntityError,
)
from ._inputs import FileInput, SupportsRead, UploadFile
from ._models import (
    AsyncConversionResult,
    AsyncOutputFile,
    Conversion,
    ConversionResult,
    OutputFile,
    Usage,
)
from ._version import __version__

VERSION = __version__
"""The library's version, as on PyPI."""

__all__ = [
    "DEFAULT_BASE_URL",
    "VERSION",
    "APIConnectionError",
    "APITimeoutError",
    "ApiError",
    "AsyncConversionResult",
    "AsyncOutputFile",
    "AsyncPdfConversionApi",
    "AuthenticationError",
    "BadRequestError",
    "Conversion",
    "ConversionResult",
    "FileInput",
    "GatewayTimeoutError",
    "InternalServerError",
    "InvalidResponseError",
    "MissingApiKeyError",
    "NotFoundError",
    "OutputFile",
    "ParamValue",
    "PayloadTooLargeError",
    "PdfConversionApi",
    "PdfConversionApiError",
    "PermissionDeniedError",
    "RateLimitError",
    "ServiceUnavailableError",
    "StoredFileError",
    "SupportsRead",
    "UnprocessableEntityError",
    "UploadFile",
    "Usage",
    "__version__",
]
