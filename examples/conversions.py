"""List the conversions the API offers. Needs no key.

python conversions.py [from-format]
"""

from __future__ import annotations

import sys

from pdfconversionapi import PdfConversionApi


def main() -> None:
    source = sys.argv[1].lower() if len(sys.argv) > 1 else "docx"
    with PdfConversionApi() as client:
        conversions = client.conversions()
    print(len(conversions), "conversions in all; from", source + ":")
    for conversion in conversions:
        if conversion.from_format == source:
            print(f"  {conversion.from_format}/to/{conversion.to_format}  {conversion.title}")


if __name__ == "__main__":
    main()
