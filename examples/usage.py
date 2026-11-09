"""Print your plan, its limits and this month's count. Uses no conversion.

python usage.py
"""

from __future__ import annotations

from pdfconversionapi import PdfConversionApi


def main() -> None:
    with PdfConversionApi() as client:
        usage = client.usage()
        print(f"{usage.plan}: {usage.used} of {usage.included} conversions used this month")
        print(f"{usage.remaining} left until {usage.period_end:%Y-%m-%d %H:%M} UTC")
        print(f"up to {usage.concurrent_limit} at once, {usage.max_upload_mb} MB per request")


if __name__ == "__main__":
    main()
