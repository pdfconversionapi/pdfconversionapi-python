"""Make a QR code. This conversion takes no input file, only the Data parameter.

python qr_code.py "https://pdfconversionapi.com"
"""

from __future__ import annotations

import sys

from pdfconversionapi import PdfConversionApi


def main() -> None:
    data = sys.argv[1] if len(sys.argv) > 1 else "https://pdfconversionapi.com"
    with PdfConversionApi() as client:
        result = client.convert("data", "qrcode", params={"Data": data})
        print("wrote", result.file.save("."))


if __name__ == "__main__":
    main()
