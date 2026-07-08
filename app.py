#!/usr/bin/env python3
"""
AI Coloring Book Generator — Web UI + REST API backend.

Generates unique black-and-white coloring-book page designs from a theme via
the Gemini API and assembles them into a KDP-ready interior PDF (no title or
bonus pages — just the alternating [design, blank] content pages).

Run locally:  python3 app.py
Cloud:        gunicorn app:app --workers 1 --threads 4 --timeout 300
"""

import hmac
import os
import threading
import traceback
from pathlib import Path

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
MAX_DESIGNS = 150


def _check_api_key():
    if not API_KEY:
        return True
    supplied = request.headers.get("X-API-Key", "")
    return hmac.compare_digest(supplied, API_KEY)


def _run_job(job_id):
    job = store.get_job(job_id)
    if job is None:
        return
    job_thumbs_dir = THUMBS_DIR / job_id
    job_thumbs_dir.mkdir(parents=True, exist_ok=True)

    try:
        store.update_job(job_id, status="planning")
        generator = ColoringPageGenerator()
        phrases = generator.generate_variations(job["theme"], job["num_designs"])

        store.update_job(job_id, status="generating")
        aspect_ratio = closest_aspect_ratio(job["page_size"])
        style = job["style"]
        design_images = []
        for i, phrase in enumerate(phrases):
            try:
                image = generator.generate_image(phrase, style=style, aspect_ratio=aspect_ratio)
            except GenerationError:
                fresh_phrase = generator.generate_single_variation(job["theme"], phrases[: i + 1])
                image = generator.generate_image(fresh_phrase, style=style, aspect_ratio=aspect_ratio)

            thumb_name = f"{i:03d}.png"
            image.convert("RGB").save(job_thumbs_dir / thumb_name)
            design_images.append(image)
            store.add_thumbnail(job_id, thumb_name)

        store.update_job(job_id, status="assembling")
        output_path = OUTPUT_DIR / f"{job_id}.pdf"
        build_interior_pdf(design_images, job["page_size"], str(output_path))

        store.update_job(job_id, status="done", output_path=str(output_path))
    except Exception as e:
        traceback.print_exc()
        store.update_job(job_id, status="error", error=str(e))


@app.route("/")
def index():
    return render_template("index.html", page_sizes=list(PAGE_SIZES.keys()))


@app.route("/api/jobs", methods=["POST"])
def create_job():
    if not _check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    theme = (request.form.get("theme") or "").strip()
    if not theme:
        return jsonify({"error": "theme is required"}), 400

    style = (request.form.get("style") or "").strip()

    try:
        num_designs = int(request.form.get("num_designs", 50))
    except ValueError:
        return jsonify({"error": "num_designs must be an integer"}), 400
    if num_designs < 1 or num_designs > MAX_DESIGNS:
        return jsonify({"error": f"num_designs must be between 1 and {MAX_DESIGNS}"}), 400

    page_size = request.form.get("page_size", "8.5x11")
    if page_size not in PAGE_SIZES:
        return jsonify({"error": f"page_size must be one of {list(PAGE_SIZES.keys())}"}), 400

    job_id = store.create_job(theme, num_designs, page_size, style=style)
    threading.Thread(target=_run_job, args=(job_id,), daemon=True).start()

    return jsonify({"job_id": job_id}), 202


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
    return send_file(
        job["output_path"],
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"{job['theme'].replace(' ', '_')}_coloring_book.pdf",
    )


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 9003))
    app.run(debug=True, port=port)
