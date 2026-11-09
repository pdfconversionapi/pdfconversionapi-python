"""Convert a Word document to PDF.

export PDFCONVERSIONAPI_KEY=...
python quickstart.py report.docx
"""

from __future__ import annotations

import sys

from pdfconversionapi import PdfConversionApi


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "report.docx"
    with PdfConversionApi() as client:  # reads PDFCONVERSIONAPI_KEY
        result = client.convert_file(path, "pdf")
        for written in result.save_all("out"):
            print("wrote", written)
        print(f"took {result.conversion_time} s on the server (request id {result.request_id})")


if __name__ == "__main__":
    main()
