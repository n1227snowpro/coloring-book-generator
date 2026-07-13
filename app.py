#!/usr/bin/env python3
"""
AI Coloring Book Generator — Web UI + REST API backend.

Generates unique black-and-white coloring-book page designs from a topic via
the Gemini API and assembles them into a KDP-ready interior PDF (no title or
bonus pages — just the alternating [design, blank] content pages).

Run locally:  python3 app.py
Cloud:        gunicorn app:app --workers 1 --threads 4 --timeout 300
"""

import hmac
import os
import re
import threading
import traceback
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_file, send_from_directory

load_dotenv()

from generator import ColoringPageGenerator, GenerationError
from job_store import JobStore
from pdf_builder import PAGE_SIZES, build_interior_pdf, closest_aspect_ratio

DATA_DIR_ENV = os.environ.get("DATA_DIR")
APP_SUPPORT_DIR = (
    Path(DATA_DIR_ENV)
    if DATA_DIR_ENV
    else Path.home() / "Library" / "Application Support" / "ColoringBookGenerator"
)
OUTPUT_DIR = APP_SUPPORT_DIR / "output"
THUMBS_DIR = APP_SUPPORT_DIR / "thumbnails"
DB_PATH = APP_SUPPORT_DIR / "jobs.db"

for d in (OUTPUT_DIR, THUMBS_DIR):
    d.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
store = JobStore(DB_PATH)

API_KEY = os.environ.get("API_KEY", "")
MAX_PAGE_COUNT = 300  # 150 unique designs, matches KDP's supported page range

DEFAULT_CALLBACK_URL = os.environ.get("CALLBACK_URL", "")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


def _check_api_key():
    if not API_KEY:
        return True
    supplied = request.headers.get("X-API-Key", "")
    return hmac.compare_digest(supplied, API_KEY)


def _send_callback(callback_url, task_id, pdf_url=None, error=None):
    """POST the result to an external caller's webhook once a job finishes.
    Best-effort: failures are logged, never raised (must not break the job)."""
    if not callback_url or not task_id:
        return
    payload = {"task_id": task_id}
    if error:
        payload["error"] = error
    else:
        payload["pdf_url"] = pdf_url
    try:
        requests.post(callback_url, json=payload, timeout=15)
    except Exception:
        traceback.print_exc()


def _run_job(job_id):
    job = store.get_job(job_id)
    if job is None:
        return
    job_thumbs_dir = THUMBS_DIR / job_id
    job_thumbs_dir.mkdir(parents=True, exist_ok=True)

    try:
        store.update_job(job_id, status="planning")
        generator = ColoringPageGenerator()

        title = job["title"]
        if not title:
            title = generator.generate_title(job["topic"], theme=job["theme"], keyword=job["keyword"])
            store.update_job(job_id, title=title)

        design_count = job["page_count"] // 2
        phrases = generator.generate_variations(job["topic"], design_count, theme=job["theme"])

        store.update_job(job_id, status="generating")
        aspect_ratio = closest_aspect_ratio(job["trim_size"])
        theme, style = job["theme"], job["style"]
        design_images = []
        for i, phrase in enumerate(phrases):
            try:
                image = generator.generate_image(phrase, theme=theme, style=style, aspect_ratio=aspect_ratio)
            except GenerationError:
                fresh_phrase = generator.generate_single_variation(job["topic"], phrases[: i + 1], theme=theme)
                image = generator.generate_image(fresh_phrase, theme=theme, style=style, aspect_ratio=aspect_ratio)

            thumb_name = f"{i:03d}.png"
            image.convert("RGB").save(job_thumbs_dir / thumb_name)
            design_images.append(image)
            store.add_thumbnail(job_id, thumb_name)

        store.update_job(job_id, status="assembling")
        output_path = OUTPUT_DIR / f"{job_id}.pdf"
        build_interior_pdf(
            design_images, job["trim_size"], str(output_path), title=title, keywords=job["keyword"]
        )

        store.update_job(job_id, status="done", output_path=str(output_path))

        if job["task_id"]:
            pdf_url = f"{PUBLIC_BASE_URL}/api/jobs/{job_id}/download" if PUBLIC_BASE_URL else None
            _send_callback(job["callback_url"], job["task_id"], pdf_url=pdf_url)
    except Exception as e:
        traceback.print_exc()
        store.update_job(job_id, status="error", error=str(e))
        if job["task_id"]:
            _send_callback(job["callback_url"], job["task_id"], error=str(e))


@app.route("/")
def index():
    return render_template("index.html", page_sizes=list(PAGE_SIZES.keys()))


@app.route("/api/title", methods=["POST"])
def generate_title():
    """Quick synchronous helper for the "Generate Title" button — no job created."""
    if not _check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    topic = (request.form.get("topic") or "").strip()
    if not topic:
        return jsonify({"error": "topic is required"}), 400
    theme = (request.form.get("theme") or "").strip()
    keyword = (request.form.get("keyword") or "").strip()

    try:
        generator = ColoringPageGenerator()
    except GenerationError as e:
        return jsonify({"error": str(e)}), 500

    title = generator.generate_title(topic, theme=theme, keyword=keyword)
    return jsonify({"title": title})


@app.route("/api/jobs", methods=["POST"])
def create_job():
    if not _check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    topic = (request.form.get("topic") or "").strip()
    if not topic:
        return jsonify({"error": "topic is required"}), 400

    theme = (request.form.get("theme") or "").strip()
    style = (request.form.get("style") or "").strip()
    keyword = (request.form.get("keyword") or "").strip()
    title = (request.form.get("title") or "").strip()

    try:
        page_count = int(request.form.get("page_count", 100))
    except ValueError:
        return jsonify({"error": "page_count must be an integer"}), 400
    if page_count % 2 != 0:
        return jsonify({"error": "page_count must be even (each design pairs with a blank page)"}), 400
    if page_count < 2 or page_count > MAX_PAGE_COUNT:
        return jsonify({"error": f"page_count must be between 2 and {MAX_PAGE_COUNT}"}), 400

    trim_size = request.form.get("trim_size", "8.5x11")
    if trim_size not in PAGE_SIZES:
        return jsonify({"error": f"trim_size must be one of {list(PAGE_SIZES.keys())}"}), 400

    task_id = (request.form.get("task_id") or "").strip()
    callback_url = (request.form.get("callback_url") or "").strip() or DEFAULT_CALLBACK_URL

    job_id = store.create_job(
        topic, page_count, trim_size, theme=theme, style=style, keyword=keyword, title=title,
        task_id=task_id, callback_url=callback_url,
    )
    threading.Thread(target=_run_job, args=(job_id,), daemon=True).start()

    return jsonify({"job_id": job_id, "task_id": task_id}), 202


@app.route("/api/jobs/<job_id>", methods=["GET"])
def get_job(job_id):
    job = store.get_job(job_id)
    if job is None:
        return jsonify({"error": "Job not found"}), 404

    result = dict(job)
    result["thumbnail_urls"] = [f"/api/jobs/{job_id}/thumb/{name}" for name in job["thumbnails"]]
    if job["status"] == "done":
        result["download_url"] = f"/api/jobs/{job_id}/download"
    return jsonify(result)


@app.route("/api/jobs/<job_id>/thumb/<filename>", methods=["GET"])
def get_thumbnail(job_id, filename):
    return send_from_directory(THUMBS_DIR / job_id, filename)


@app.route("/api/jobs/<job_id>/download", methods=["GET"])
def download_job(job_id):
    job = store.get_job(job_id)
    if job is None or job["status"] != "done" or not job["output_path"]:
        return jsonify({"error": "Job not ready"}), 404
    name_source = job["title"] or job["topic"]
    safe_name = re.sub(r"[^\w\-]+", "_", name_source).strip("_") or "coloring_book"
    return send_file(
        job["output_path"],
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"{safe_name}.pdf",
    )


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 9003))
    app.run(debug=True, port=port)
