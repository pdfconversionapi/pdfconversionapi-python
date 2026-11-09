"""Render the pages of a PDF as JPG images: one output file per page.

python pdf_to_jpg.py season-guide.pdf [PageRange]
"""

from __future__ import annotations

import sys

from pdfconversionapi import PdfConversionApi


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "season-guide.pdf"
    params: dict[str, str | int] = {"ImageResolution": 72}
    if len(sys.argv) > 2:
        params["PageRange"] = sys.argv[2]
    with PdfConversionApi() as client:
        result = client.convert_file(path, "jpg", params)
        for file in result.files:
            print(file.name, file.size, "bytes")
        result.save_all("pages")


if __name__ == "__main__":
    main()
