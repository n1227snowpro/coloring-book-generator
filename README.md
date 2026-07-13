# 🎨 Coloring Book Generator

**Live:** https://coloringbook.srv1213330.hstgr.cloud (Docker container on the Hostinger box, behind Traefik)

Generates a unique, print-ready KDP coloring book **interior** PDF from a single
theme, using Google's Gemini API (`gemini-3.1-flash-image`, aka "Nano Banana")
for the line art. No title page, "belongs to" page, copyright page, or bonus
page is added — just the alternating `[design, blank]` content pages, matching
standard single-sided coloring book layout.

Works as a web UI and as a REST API (e.g. from n8n), and runs both locally on
this Mac and hosted (Docker / Hostinger).

---

## How it works

You give it a **topic** (what's depicted, e.g. "forest animals") and,
optionally, a **theme** (mood/setting, e.g. "whimsical enchanted woodland")
and a **style** (art rendering, e.g. "storybook illustration"). Topic drives
what each page *shows*; theme and style are applied identically to *every*
page so the whole book looks like one consistent product rather than a grab
bag of unrelated images.

1. Gemini text model generates N distinct short subject phrases from the
   topic (+ theme context), so every page is a different composition.
2. Gemini image model (`gemini-3.1-flash-image`) turns each phrase into a
   black-and-white line-art coloring page, with the theme/style baked into
   every single image call.
3. Each design fills its own page (no drawn border — the generated art
   already has its own frame) and is followed by a blank page; everything is
   assembled into one PDF at your chosen trim size (default 8.5×11in, 300
   DPI), with KDP-correct gutter margins that scale with page count.
4. If you didn't supply a **title**, one is auto-generated from
   topic/theme/keyword and written into the PDF's metadata (never rendered
   on a page — there's still no title page in the book itself).

Because Gemini's image output resolution is below 300 DPI print spec at these
trim sizes, each design is upscaled (Lanczos) to fit the page. Line art holds
up well to this; if you see visible softness at full size, regenerate at a
smaller trim size or treat the PDF as a proof rather than final print file.

Generation of 50-100 images takes several minutes — this runs as a background
job with progress polling, not a single request/response.

---

## Local setup

```bash
cd ~/coloring-book-generator
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then paste in your GEMINI_API_KEY
```

Get a Gemini API key at https://aistudio.google.com/apikey.

Run it:

```bash
export $(grep -v '^#' .env | xargs)   # or use python-dotenv / your shell's env loader
python3 app.py
```

Open **http://localhost:9003** (or whatever `PORT` you set).

Data (job database, thumbnails, output PDFs) is stored under
`~/Library/Application Support/ColoringBookGenerator/`, never inside this
project folder.

---

## REST API

Base URL: `https://coloringbook.srv1213330.hstgr.cloud` (or `http://localhost:9003`
locally). Every endpoint accepts an optional `X-API-Key: <API_KEY env var>`
header — only enforced if you set `API_KEY` in `.env` (unset = open).

### `POST /api/title` — optional, generate a title without starting a job

`multipart/form-data`:

| field | type | required | notes |
|---|---|---|---|
| `topic` | string | yes | e.g. `forest animals` |
| `theme` | string | no | e.g. `whimsical enchanted woodland` |
| `keyword` | string | no | e.g. `relaxing coloring for adults` |

Returns `200 { "title": "..." }` immediately (single fast text call, no job
created). This is what the web UI's "Generate Title" button calls.

```bash
curl -F topic="forest animals" -F theme="whimsical enchanted woodland" \
     -F keyword="relaxing coloring for adults" \
     https://coloringbook.srv1213330.hstgr.cloud/api/title
```

### `POST /api/jobs` — generate the book

`multipart/form-data`:

| field | type | required | notes |
|---|---|---|---|
| `topic` | string | **yes** | Subject matter — what's depicted on each page. e.g. `forest animals` |
| `keyword` | string | no | Marketing/audience context, e.g. `relaxing coloring for adults`. Folded into title generation and written to the PDF's Keywords metadata. Doesn't affect the art. |
| `title` | string | no | Book title. If omitted, one is auto-generated (same logic as `/api/title`) and returned in the job status. Written to PDF metadata only — no title page is rendered. |
| `theme` | string | no | Mood/setting, e.g. `whimsical enchanted woodland`. Applied to **every** page, same as style. |
| `style` | string | no | Art rendering style, e.g. `storybook illustration`. Applied to **every** page. Defaults to plain clean line art if omitted. |
| `page_count` | int | no (default `100`) | **Total physical pages** in the PDF — must be even (each unique design pairs with a blank page). 2-300. `page_count=100` → 50 unique designs. |
| `trim_size` | string | no (default `8.5x11`) | One of `8.25x11`, `8.5x11`, `6x9`, `5.5x8.5` |
| `task_id` | string | no | Your own id for this book. If set, the server **POSTs the result to a callback URL** when the job finishes (see below) instead of (in addition to) you having to poll. Echoed back in the `202` response and in every `GET /api/jobs/<id>`. |
| `callback_url` | string | no | Where to POST the result. Defaults to the server's `CALLBACK_URL` env var if omitted — only need to pass this if you want a different callback than the configured default. |

Returns `202 { "job_id": "...", "task_id": "..." }` immediately; generation
runs as a background job (a 100-page book takes several minutes — this is
not a single request/response).

```bash
curl https://coloringbook.srv1213330.hstgr.cloud/api/jobs \
  -F topic="forest animals" \
  -F theme="whimsical enchanted woodland" \
  -F style="storybook illustration" \
  -F keyword="relaxing coloring for adults" \
  -F title="Enchanted Forest Friends" \
  -F page_count=100 \
  -F trim_size=8.5x11 \
  -F task_id="book-row-42"
# => {"job_id": "8d1cb6dd1ae4", "task_id": "book-row-42"}
```

#### Callback (webhook) instead of polling

When a job created with `task_id` finishes, the server POSTs the result to
`callback_url` (or the `CALLBACK_URL` env default) as `application/json`:

- Success: `{"task_id": "book-row-42", "pdf_url": "https://coloringbook.srv1213330.hstgr.cloud/api/jobs/8d1cb6dd1ae4/download"}`
- Failure: `{"task_id": "book-row-42", "error": "<reason>"}`

`pdf_url` is built from the `PUBLIC_BASE_URL` env var — it must be set for
callbacks to carry a usable URL (defaults to
`https://coloringbook.srv1213330.hstgr.cloud` in `docker-compose.yml`; set
it explicitly in `.env` for any other deployment). The callback POST is
best-effort — a delivery failure is logged but does not fail the job or get
retried; poll `GET /api/jobs/<id>` as a fallback if you need certainty.

### `GET /api/jobs/<job_id>` — poll status

Returns the full job record: `status` (`queued` → `planning` → `generating`
→ `assembling` → `done`/`error`), `completed`/`total` (designs finished so
far, for a progress bar), `thumbnail_urls` (grows as each design finishes),
and — once `status` is `done` — `download_url`.

```bash
curl https://coloringbook.srv1213330.hstgr.cloud/api/jobs/8d1cb6dd1ae4
```

```json
{
  "status": "done",
  "completed": 50,
  "total": 50,
  "page_count": 100,
  "title": "Enchanted Forest Friends",
  "download_url": "/api/jobs/8d1cb6dd1ae4/download"
}
```

### `GET /api/jobs/<job_id>/download` — get the PDF

Streams the finished interior PDF, named from the title (or topic if no
title was set).

### `GET /api/jobs/<job_id>/thumb/<filename>` — preview a single page

Serves one design's PNG thumbnail (as listed in `thumbnail_urls`).

### Typical n8n flow

1. **HTTP Request** node → `POST /api/jobs` with your topic/theme/style/etc.
   as form-data → grab `job_id` from the response.
2. **Wait** node (e.g. 30s) → **HTTP Request** node → `GET /api/jobs/{{job_id}}`
   → **If** node checking `status == "done"` (loop back to Wait if not, stop
   on `error`).
3. Once done, **HTTP Request** node → `GET /api/jobs/{{job_id}}/download`
   (set "Response Format" to file) to pull the finished PDF into the rest of
   your workflow.

---

## Deployment (Hostinger)

Deployed at `/opt/coloring-book-generator` on the Hostinger box (`72.62.121.91`,
alias `hostinger-books` in `~/.ssh/config` on this Mac, key
`~/.ssh/coloring_book_deploy_ed25519`), following the same
`docker-compose.yml` + Traefik-label pattern as the other Docker apps on that
server (`cheap-ticket`, `audiobook-generator`, `pdf-cover-converter`).
Routed via the `n8n_default` Traefik network, TLS auto-issued by
`mytlschallenge`, DNS is a wildcard (`*.srv1213330.hstgr.cloud`).

To redeploy after a code change:

```bash
ssh hostinger-books "cd /opt/coloring-book-generator && git pull && docker compose up -d --build"
```

`docker-compose.yml` sets `DATA_DIR=/app/data` (a named volume,
`coloring_book_data`) so job state/output PDFs survive container restarts —
`app.py` falls back to this Mac's `~/Library/Application Support` path when
`DATA_DIR` isn't set (i.e. when run locally).

`GEMINI_API_KEY` lives in `/opt/coloring-book-generator/.env` on the server
(gitignored, not in the repo) — update it there directly if the key rotates:

```bash
ssh hostinger-books "nano /opt/coloring-book-generator/.env && cd /opt/coloring-book-generator && docker compose up -d"
```

`gunicorn` is configured with `--workers 1 --threads 4` — job state lives in
one process (SQLite + in-memory thread), so don't scale to multiple workers
without moving job state to something shared (e.g. Redis) first.

---

## Requirements

| Package | Purpose |
|---|---|
| Flask | Web framework |
| Pillow | Image processing & PDF assembly |
| google-genai | Gemini API client |
| gunicorn | Production WSGI server |
