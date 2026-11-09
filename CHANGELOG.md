# Changelog

## 0.1.0 (unreleased)

The first release.

- `PdfConversionApi` and `AsyncPdfConversionApi`, with `convert`, `convert_file`, `usage`,
  `conversions`, `download`, `download_to` and `delete_stored_file`.
- Inputs from paths, bytes, binary streams and `UploadFile`; several inputs as repeated `Files`;
  named file parameters such as `ImageFile`; conversions without an input file.
- Results with `files`, `file`, `save_all`, and stored files that download on `save()`.
- Typed errors for every status the API documents, and `str(error)` with the request id.
- Retries that follow the API's documentation: dropped connections, `Retry-After` on 429 and 503
  up to 60 seconds, and one retry of a 500. A 240-second timeout per attempt.
