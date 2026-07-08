# 🎨 Coloring Book Generator

Generates a unique, print-ready KDP coloring book **interior** PDF from a single
theme, using Google's Gemini API (`gemini-3.1-flash-image`, aka "Nano Banana")
for the line art. No title page, "belongs to" page, copyright page, or bonus
page is added — just the alternating `[design, blank]` content pages, matching
standard single-sided coloring book layout.

Works as a web UI and as a REST API (e.g. from n8n), and runs both locally on
this Mac and hosted (Docker / Hostinger).

---

## How it works

1. You give it a **theme** (e.g. "mandalas", "ocean animals") and a design
   count (50 → 100 pages, 100 → 200 pages, or custom).
2. Gemini text model generates that many distinct short subject phrases so
   every page is a different design.
3. Gemini image model (`gemini-3.1-flash-image`) turns each phrase into a
   black-and-white line-art coloring page.
4. Each design is framed on its own page with a thin border and followed by a
   blank page; everything is assembled into one PDF at your chosen KDP trim
   size (default 8.5×11in, 300 DPI).

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

### `POST /api/jobs`

`multipart/form-data`:

| field | type | required | notes |
|---|---|---|---|
| `theme` | string | yes | e.g. `mandalas` |
| `num_designs` | int | no (default 50) | 1-150; unique designs, PDF has 2× this many pages |
| `page_size` | string | no (default `8.5x11`) | one of `8.25x11`, `8.5x11`, `6x9`, `5.5x8.5` |

Optional header `X-API-Key: <API_KEY env var>` if you set one.

Returns `202 { "job_id": "..." }`.

```bash
curl -F theme=mandalas -F num_designs=4 http://localhost:9003/api/jobs
```

### `GET /api/jobs/<job_id>`

Returns job status, progress, thumbnail URLs, and (once done) a
`download_url`.

### `GET /api/jobs/<job_id>/download`

Streams the finished PDF.

---

## Deployment (Hostinger / any Docker host)

```bash
docker build -t coloring-book-generator .
docker run -p 5000:5000 --env-file .env coloring-book-generator
```

Deploy the image behind Nginx/whatever reverse proxy you already use for the
other tools on this server, same as `book-generator-web`. Note: inside the
container, generated files live at the container's home directory rather than
this Mac's `~/Library/Application Support`; mount a volume there if you need
jobs/output to survive container restarts.

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
