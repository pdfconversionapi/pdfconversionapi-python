"""Convert several documents at the same time with the async client.

python async_batch.py report.docx notes.docx slides.pptx
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from pdfconversionapi import AsyncPdfConversionApi

AT_ONCE = 2  # stay within your plan's conversions at once


async def main() -> None:
    paths = sys.argv[1:] or ["report.docx"]
    limit = asyncio.Semaphore(AT_ONCE)
    async with AsyncPdfConversionApi() as client:

        async def convert(path: str) -> list[Path]:
            async with limit:
                result = await client.convert_file(path, "pdf")
            return await result.save_all("out")

        for written in await asyncio.gather(*(convert(p) for p in paths)):
            print("wrote", *written)


if __name__ == "__main__":
    asyncio.run(main())
