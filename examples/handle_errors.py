"""Handle errors. Retries already happened by the time an error is raised.

python handle_errors.py report.docx
"""

from __future__ import annotations

import sys

from pdfconversionapi import (
    APIConnectionError,
    ApiError,
    APITimeoutError,
    BadRequestError,
    NotFoundError,
    PdfConversionApi,
    RateLimitError,
)


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "report.docx"
    with PdfConversionApi() as client:
        try:
            client.convert_file(path, "dwg")
        except NotFoundError as error:  # a pair the API doesn't convert
            print("not supported:", error.message)
        try:
            client.convert_file(path, "pdf", {"PageRange": "3-1"})
        except BadRequestError as error:  # change the request
            print(f"bad request: {error.message} (status {error.status}, id {error.request_id})")
        except RateLimitError as error:  # a long Retry-After, such as a used-up month
            print(f"rate limited; retry after {error.retry_after} s: {error.message}")
        except ApiError as error:  # any other status
            print(error)
        except APITimeoutError as error:  # the conversion may still be running
            print("timed out:", error)
        except APIConnectionError as error:  # no response after the retries
            print("network problem:", error)


if __name__ == "__main__":
    main()
