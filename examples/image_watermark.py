"""Stamp an image on every page of a PDF. The image goes in a named file field, ImageFile.

python image_watermark.py proposal.pdf logo.png
"""

from __future__ import annotations

import sys
from pathlib import Path

from pdfconversionapi import PdfConversionApi


def main() -> None:
    pdf = sys.argv[1] if len(sys.argv) > 1 else "proposal.pdf"
    logo = Path(sys.argv[2] if len(sys.argv) > 2 else "logo.png")
    with PdfConversionApi() as client:
        # A parameter whose value is a Path, bytes, a stream or an UploadFile is sent as a file.
        # A str value is always text, so pass the image as a Path.
        result = client.convert(
            "pdf", "image-watermark", pdf, {"ImageFile": logo, "Opacity": 30}, file_name="stamped"
        )
        print("wrote", result.file.save("."))


if __name__ == "__main__":
    main()
