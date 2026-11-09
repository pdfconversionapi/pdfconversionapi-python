"""Merge several PDFs into one, in the order given.

python merge.py invoice.pdf terms.pdf timesheet.pdf
"""

from __future__ import annotations

import sys

from pdfconversionapi import PdfConversionApi


def main() -> None:
    paths = sys.argv[1:] or ["invoice.pdf", "terms.pdf", "timesheet.pdf"]
    with PdfConversionApi() as client:
        result = client.convert("pdf", "merge", paths, file_name="october-billing")
        path = result.file.save(".")
        print("wrote", path, result.file.size, "bytes")


if __name__ == "__main__":
    main()
