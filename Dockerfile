FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py generator.py job_store.py pdf_builder.py .
COPY templates/ templates/

ENV PORT=5000
EXPOSE 5000

# --workers 1 keeps job state (SQLite + background threads) in a single
# process; a second worker would never see jobs started on the first.
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:5000", "--timeout", "300", "--worker-class", "gthread", "--workers", "1", "--threads", "4"]
