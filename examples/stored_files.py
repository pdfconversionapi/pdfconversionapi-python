"""Store the results instead of receiving them inline, download them, then delete them early.

python stored_files.py season-guide.pdf
"""

from __future__ import annotations

import sys
from pathlib import Path

from pdfconversionapi import PdfConversionApi


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "season-guide.pdf"
    folder = Path("stored")
    folder.mkdir(exist_ok=True)
    with PdfConversionApi() as client:
        result = client.convert_file(path, "jpg", {"ImageResolution": 72}, store_file=True)
        for file in result.files:
            # The link works for 3 hours and needs no key; treat it like a password.
            written = file.save(folder)  # streamed to disk
            assert file.file_id is not None
            deleted = client.delete_stored_file(file.file_id)
            print(f"{written} ({file.size} bytes); deleted the stored copy: {deleted}")


if __name__ == "__main__":
    main()
