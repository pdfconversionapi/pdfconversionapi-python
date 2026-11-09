# pdfconversionapi

The official Python client for the [PDF Conversion API](https://pdfconversionapi.com): convert
Word, Excel, PowerPoint, images, HTML and more to PDF and back, and merge, split, compress,
protect or watermark PDFs, with one call.

- Docs: https://pdfconversionapi.com/docs
- API keys: https://app.pdfconversionapi.com
- Pricing: https://pdfconversionapi.com/pricing

It has a synchronous client, `PdfConversionApi`, and an asynchronous one,
`AsyncPdfConversionApi`, with the same methods. Python 3.9 or later; its only dependency is
[httpx](https://www.python-httpx.org).

## Install

```bash
pip install pdfconversionapi
```

## Your API key

Sign in at https://app.pdfconversionapi.com to get a free key, and put it in the
`PDFCONVERSIONAPI_KEY` environment variable, where the client reads it:

```bash
export PDFCONVERSIONAPI_KEY="paste-your-key-here"
```

You can also pass it as `PdfConversionApi(api_key=...)`. Keep the key on your server: anyone
who has it can convert files on your account, and the API sends no CORS headers, so it can't be
called from a web page anyway.

## Quick start

```python
from pdfconversionapi import PdfConversionApi

client = PdfConversionApi()                      # reads PDFCONVERSIONAPI_KEY
result = client.convert_file("report.docx", "pdf")
result.save_all("out")                           # writes out/report.pdf
```

`convert_file` takes the input format from the file's extension. `convert` names both formats,
and takes one input file or a list of them:

```text
client.convert(from_format, to_format, files=None, params=None, *,
               file_name=None, store_file=False, timeout=None)
```

The examples below use our sample files. To run them as written, save
[report.docx](https://pdfconversionapi.com/samples/docx-to-pdf/input-quarterly-report.docx),
[season-guide.pdf](https://pdfconversionapi.com/samples/pdf-to-jpg/input-season-guide.pdf),
[invoice.pdf](https://pdfconversionapi.com/samples/merge/input-invoice.pdf),
[terms.pdf](https://pdfconversionapi.com/samples/merge/input-terms.pdf),
[timesheet.pdf](https://pdfconversionapi.com/samples/merge/input-timesheet.pdf) and
[proposal.pdf](https://pdfconversionapi.com/samples/watermark/input-proposal.pdf) under those
names, plus any PNG image as `logo.png`. The `examples` folder of the source distribution has
the same programs as scripts.

## Input files

An input can be any of these:

| You pass | Sent as |
|---|---|
| A path: `"report.docx"` or `Path("report.docx")` | The file's own name, `report.docx`. The file is read again for each retry. |
| Bytes: `b"..."` | `file` |
| A binary stream, such as `open("report.docx", "rb")` or `io.BytesIO(...)` | An open file's own name, or `file` |
| `UploadFile.from_path(path, name)`, `UploadFile.from_bytes(data, name)`, `UploadFile.from_stream(stream, name)` | The name you give |

**A `str` is always a path, never the file's content.** Only the file's own name is sent,
never its folders, and the API names the output after it (`report.docx` becomes `report.pdf`).
A seekable stream is rewound for each retry; any other stream is read into memory once.

```python
from pathlib import Path

from pdfconversionapi import PdfConversionApi, UploadFile

client = PdfConversionApi()
data = Path("report.docx").read_bytes()
result = client.convert("docx", "pdf", UploadFile.from_bytes(data, "Q3 report.docx"))
print(result.file.name)       # Q3 report.pdf
result.file.save(".")         # writes ./Q3 report.pdf
```

## Several output files: PDF to JPG

Some conversions return one file per page. `result.files` holds them in order, and
`result.file` is the first.

```python
from pdfconversionapi import PdfConversionApi

client = PdfConversionApi()
result = client.convert_file("season-guide.pdf", "jpg", {"ImageResolution": 72})
for file in result.files:
    print(file.name, file.size)
result.save_all("pages")      # writes pages/season-guide-1.jpg and pages/season-guide-2.jpg
```

```text
season-guide-1.jpg 65272
season-guide-2.jpg 66627
```

## Several input files: merge

A list of inputs is sent in the order given. `pdf/to/merge`, `images/to/pdf`,
`images/to/join` and `file/to/zip` take several files; the others take one.

```python
from pdfconversionapi import PdfConversionApi

client = PdfConversionApi()
result = client.convert(
    "pdf", "merge", ["invoice.pdf", "terms.pdf", "timesheet.pdf"], file_name="october-billing"
)
print(result.file.name, result.file.size)  # october-billing.pdf 107308
result.file.save(".")
```

## A second file: image watermark

A parameter whose value is a file is sent as a file field of that name, such as `ImageFile` for
`pdf/to/image-watermark` or `OverlayFile` for `pdf/to/pdf-watermark`. Pass the file as a
`Path`, bytes, a stream or an `UploadFile`: a `str` parameter value is always text.

```python
from pathlib import Path

from pdfconversionapi import PdfConversionApi

client = PdfConversionApi()
result = client.convert(
    "pdf", "image-watermark", "proposal.pdf", {"ImageFile": Path("logo.png"), "Opacity": 30}
)
result.file.save("proposal-stamped.pdf")
```

## Parameters

`params` is a dict of the conversion's parameters, named as on each conversion's page (for
example [PDF to JPG](https://pdfconversionapi.com/convert/pdf-to-jpg)). Numbers are sent in
plain decimal, booleans as `true` or `false`, and `None` is left out. Two parameters work on
every conversion and have their own keyword arguments: `file_name` (the output's name, without
the extension) and `store_file` (below). They win over the same names in `params`.

```python
from pdfconversionapi import PdfConversionApi

client = PdfConversionApi()
result = client.convert_file(
    "season-guide.pdf",
    "jpg",
    {"PageRange": "2", "ImageResolution": 72, "ImageWidth": None},  # None is left out
    file_name="guide",
)
print([file.name for file in result.files])  # ['guide-2.jpg']
```

The client doesn't check parameter names or values: the API answers a mistake with a
`BadRequestError` that names the parameter.

## No input file: QR codes and barcodes

`data/to/qrcode` and `data/to/barcode` take no file, only the `Data` parameter.

```python
from pdfconversionapi import PdfConversionApi

client = PdfConversionApi()
result = client.convert("data", "qrcode", params={"Data": "https://pdfconversionapi.com"})
result.file.save(".")         # writes ./qrcode.svg
```

## Stored files: download and delete

By default the converted files come back inside the response. With `store_file=True` the API
keeps them instead and returns a link per file that works for 3 hours and needs no key. Treat
the link like a password. Reading `file.data` of a stored file raises `StoredFileError`;
`save()`, `save_all()`, `download()` and `download_to()` fetch it, and saving streams it to
disk.

```python
from pdfconversionapi import PdfConversionApi

client = PdfConversionApi()
result = client.convert_file("season-guide.pdf", "jpg", {"ImageResolution": 72}, store_file=True)
result.save_all("stored")                         # downloads every file
for file in result.files:
    print(file.name, file.url)
    deleted = client.delete_stored_file(file.file_id)  # True: deleted, False: nothing to delete
```

`file.download()` returns the bytes, and `client.download(url)` and
`client.download_to(url, path)` take a link you kept. Downloads never send your key.

## Usage

```python
from pdfconversionapi import PdfConversionApi

client = PdfConversionApi()
usage = client.usage()
print(usage.plan, usage.used, usage.included, usage.remaining)
print("the count starts again on", usage.period_end)  # a UTC datetime
```

`client.conversions()` lists every conversion the API offers (`from_format`, `to_format`,
`title`, `category` and `inputs`); it needs no key.

## Async

`AsyncPdfConversionApi` has the same methods, awaited, and so do its results (`save_all`,
`save`, `download`, `download_to`). Share one client between tasks, and keep to your plan's
conversions at once.

```python
import asyncio

from pdfconversionapi import AsyncPdfConversionApi


async def main() -> None:
    async with AsyncPdfConversionApi() as client:
        results = await asyncio.gather(
            client.convert_file("report.docx", "pdf"),
            client.convert_file("season-guide.pdf", "jpg", {"ImageResolution": 72}),
        )
        for result in results:
            await result.save_all("out")


asyncio.run(main())
```

## Errors and retries

Every error is a `PdfConversionApiError`. An error status from the API raises an `ApiError`
subclass with `status`, `message` (the API's own text), `request_id` (the `x-request-id`
header; quote it when you write to support), `retry_after` and `body`. `str(error)` reads like
`HTTP 429: too many requests; slow down (request id: 1a2b...)`, and never contains your key.

| Error | When |
|---|---|
| `BadRequestError` | 400: a parameter or the request is wrong; the message names it |
| `AuthenticationError` | 401: the key is missing, unknown or replaced |
| `PermissionDeniedError` | 403: the key's plan isn't set up |
| `NotFoundError` | 404: an unsupported conversion, or a stored file that expired or was deleted |
| `PayloadTooLargeError` | 413: the upload, an image or a page is over a limit |
| `UnprocessableEntityError` | 422: the file can't be read or converted as asked |
| `RateLimitError` | 429: too many requests or conversions at once, or the month's conversions are used up |
| `InternalServerError` | 500 |
| `ServiceUnavailableError` | 503: the servers are busy, or conversions on the plan are paused |
| `GatewayTimeoutError` | 504: the conversion ran out of time on the server |
| `ApiError` | any other status |
| `MissingApiKeyError` | no key for a call that needs one; nothing was sent |
| `APITimeoutError` | the client's own timeout ran out |
| `APIConnectionError` | no response, after the retries |
| `InvalidResponseError` | a success response the client can't use |
| `StoredFileError` | `data` of a stored file was read; use `download()` |

Bad arguments, such as a format name that isn't 1 to 20 letters, digits or hyphens, raise
`ValueError` or `TypeError` before anything is sent.

```python
from pdfconversionapi import (
    APIConnectionError,
    ApiError,
    APITimeoutError,
    NotFoundError,
    PdfConversionApi,
    RateLimitError,
)

client = PdfConversionApi()
try:
    client.convert_file("report.docx", "dwg")
except NotFoundError as error:
    print(error.status, error.message)  # 404 conversion from docx to dwg is not supported
    print(error.request_id)
except RateLimitError as error:
    print("retry after", error.retry_after, "seconds:", error.message)
except ApiError as error:
    print(error)
except (APITimeoutError, APIConnectionError) as error:
    print(error)
```

**Retries happen for you** before an error is raised, up to 4 times (5 attempts):

- A request that got no response (refused or dropped connection, a connect timeout) is
  retried after a random wait of up to 2, 4, 8 and 16 seconds.
- 429 and 503 are retried after the `Retry-After` seconds plus that random wait, when
  `Retry-After` is 60 seconds or less. A longer one, such as a used-up month or paused
  conversions, is raised at once with `retry_after` set.
- A 500 is retried once.
- Nothing else is retried: not 400, 401, 403, 404, 413, 422 or 504, not the client's own
  timeout, and not a connection that drops while the response is arriving, because the
  conversion may already have run and counted.

Retrying is safe: a conversion changes nothing on the server, and only successful attempts
count toward your month. `max_retries=0` turns retries off.

## Timeouts

Each attempt may take 240 seconds for the whole response, the time the API needs for its
slowest conversions. When the client's timeout runs out it raises `APITimeoutError` and doesn't
retry: the conversion may still be running on the server, and it counts toward your month if it
succeeds. Raise the timeout rather than retrying at once, for every call or for one:

```python
from pdfconversionapi import PdfConversionApi

client = PdfConversionApi(timeout=600)
result = client.convert_file("report.docx", "pdf", timeout=900)
```

## Large files

The whole response is held in memory while it is read, and files come back base64-encoded, so a
100 MB result is about 133 MB of JSON. For large outputs, ask for `store_file=True` and save
the files: each one is streamed from its link to disk. Uploads are streamed from disk.

## Configuration

```python
import os

import httpx

from pdfconversionapi import PdfConversionApi

client = PdfConversionApi(
    api_key=os.environ["PDFCONVERSIONAPI_KEY"],  # the default
    base_url=os.environ.get("PDFCONVERSIONAPI_BASE_URL"),  # None: https://api.pdfconversionapi.com
    timeout=240,  # seconds per attempt, for the whole response
    connect_timeout=10,  # seconds to open a connection
    max_retries=4,  # retries after the first attempt; 0 turns them off
    max_retry_wait=60,  # the longest Retry-After to wait for
    http_client=httpx.Client(limits=httpx.Limits(max_connections=20)),
)
```

| Option | Default |
|---|---|
| `api_key` | `PDFCONVERSIONAPI_KEY`. It may be missing: `conversions()` and downloads need no key, and other calls raise `MissingApiKeyError` before sending anything. |
| `base_url` | `PDFCONVERSIONAPI_BASE_URL`, else `https://api.pdfconversionapi.com` |
| `timeout` | 240 seconds |
| `connect_timeout` | 10 seconds |
| `max_retries` | 4 |
| `max_retry_wait` | 60 seconds |
| `http_client` | the client's own. Pass an `httpx.Client` (an `httpx.AsyncClient` for the async client) for proxies (`httpx.Client(proxy=...)`), your own certificates or connection limits. The client doesn't close one you pass in. |

One client is safe to share between threads (or tasks, for the async one). Close it with
`client.close()` (`await client.aclose()`), or use it in a `with` (`async with`) block.

Retries are logged at INFO level on the `pdfconversionapi` logger:

```python
import logging

logging.basicConfig(level=logging.WARNING)
logging.getLogger("pdfconversionapi").setLevel(logging.INFO)
```

## Links

- [Docs](https://pdfconversionapi.com/docs): requests, responses, errors, limits and every
  conversion
- [Dashboard](https://app.pdfconversionapi.com): your API key, usage and plan
- [Pricing](https://pdfconversionapi.com/pricing)

MIT license.
