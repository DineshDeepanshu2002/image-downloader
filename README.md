# image-downloader

A small command line tool that reads a plaintext file containing image URLs
(one per line) and downloads all of them to the local disk.

```
http://mywebserver.com/images/271947.jpg
http://mywebserver.com/images/24174.jpg
http://somewebsrv.com/img/992147.jpg
```

## Requirements

* Python 3.10+
* `requests` (see `requirements.txt`)

## Installation

```bash
python -m venv .venv
source .venv/bin/activate        # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Usage

```bash
python -m image_downloader urls.txt
```

Images are stored in `./downloads` by default. Common options:

```bash
python -m image_downloader urls.txt \
    --output-dir ./images \      # where to store the files
    --workers 8 \                # parallel downloads (default 4)
    --timeout 15 \               # per-request timeout in seconds
    --retries 3 \                # retries for transient errors (429/5xx, network)
    --max-size 52428800 \        # abort any single download over 50 MB
    --verbose                    # debug logging
```

Run `python -m image_downloader --help` for the full list.

### Input format

* One URL per line; surrounding whitespace is ignored.
* Blank lines and lines starting with `#` are treated as comments.
* Duplicate URLs are downloaded only once.
* Invalid lines (wrong scheme, no host, unparseable) are logged with
  their line number and skipped — they never abort the run.

### Exit codes

| Code | Meaning |
|------|---------|
| 0    | every valid URL was downloaded |
| 1    | at least one URL failed or was skipped |
| 2    | the run could not start (unreadable file, no usable URLs, bad flags) |

This makes the tool easy to use from cron jobs or CI pipelines, where the
exit code decides whether a step "went green".

## Design decisions

The brief asks to treat this as production code that other developers will
maintain, so a few choices are worth explaining:

**Atomic writes.** Each image is streamed to a hidden `.part` temp file in
the target directory and moved into place with `os.replace` only when
complete. A crash, Ctrl-C or full disk can never leave a half-written file
behind that looks like a valid image to a downstream consumer.

**One bad URL never kills the batch.** Every URL yields an independent
result (`ok` / `skipped` / `failed` with a reason). Failures are reported
at the end and reflected in the exit code, but the remaining downloads
always proceed.

**Retries for transient errors only.** Connection problems and 429/5xx
responses are retried with exponential backoff. A 404 is not retried —
it will still be a 404 the third time.

**Content-Type validation.** Servers frequently answer dead image links
with an HTML error page and status 200. By default, responses whose
`Content-Type` is not `image/*` are skipped rather than saved as broken
"images". `--allow-any-content-type` disables this if you need it.

**Safe filenames.** Names are derived from the URL path, percent-decoded,
and sanitised to a conservative character set, so a malicious or weird URL
can't escape the output directory or produce an invalid filename. Name
collisions (same `cat.jpg` on two hosts, or a file left from a previous
run) get a `_1`, `_2`... suffix instead of silently overwriting data. URLs
without a file extension get one from the response `Content-Type`.

**Bounded memory.** Responses are streamed in 64 KB chunks; a 2 GB image
never has to fit in RAM. An optional `--max-size` guards against a URL
list that unexpectedly points at huge files.

**Separation of concerns.** File parsing (`urlfile.py`), downloading
(`downloader.py`) and the CLI (`cli.py`) are separate modules. The
`ImageDownloader` class can be imported and reused from other Python code
without going through the command line.

### Consciously left out

Kept out of scope to avoid speculative complexity, but noted as natural
next steps if the live system needs them:

* Resuming partial downloads (HTTP `Range` requests)
* Rate limiting per host / politeness delays
* Verifying image integrity beyond the Content-Type header (e.g. decoding
  with Pillow)
* Async IO — a thread pool is simpler and entirely sufficient for
  IO-bound downloads at this scale

## Development

```bash
pip install -r requirements-dev.txt
pytest                      # run all tests
pytest --cov=image_downloader --cov-report=term-missing
black --check .             # formatting
mypy image_downloader       # strict type-checking
```

## Test strategy

The suite (26 tests, 92% line coverage) is layered so each level catches a
different class of regression and failures point straight at the cause:

1. **Unit tests** (`test_urlfile.py`) — pure input parsing: comments,
   blank lines, dedup, invalid lines with line numbers. Fast, no IO
   beyond a temp file.
2. **Component tests** (`test_downloader.py`) — the download logic
   against mocked HTTP (`responses`). Mocking makes failure modes cheap
   to simulate deterministically: 404s, connection errors, wrong
   Content-Type, oversized bodies, filename collisions. These would be
   flaky or impossible to trigger reliably against a real network.
3. **End-to-end tests** (`test_cli.py`) — the CLI as a user runs it,
   against a real local HTTP server on a random port. This is the layer
   that would catch anything the mocks hide (real socket handling,
   streaming, actual files on disk, exit codes).
4. **BDD acceptance tests** (`test_acceptance_bdd.py` +
   `features/download.feature`) — the original requirement expressed in
   Gherkin, executable via `pytest-bdd`. The feature file doubles as
   living documentation of what the tool promises.

Quality gates, as also enforced by CI (`.github/workflows/ci.yml`, runs
on Python 3.10–3.12):

* `black --check` — consistent formatting
* `mypy --strict` — full static type coverage of the package
* `pytest` with a 90% coverage floor
