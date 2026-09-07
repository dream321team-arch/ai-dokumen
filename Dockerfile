FROM python:3.12-slim

WORKDIR /app

# build-essential only for the rare wheel-less dependency; removed from the
# final layer isn't worth multi-stage complexity here (image stays small enough).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Non-root user, matching what Hugging Face Spaces (and most container
# platforms) expect/recommend for Docker-based Spaces.
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Hugging Face Spaces (Docker SDK) routes traffic to port 7860 by default.
# src/cli.py's `serve` command already reads $PORT (falls back to 8000), and
# binds host 0.0.0.0 by default, so this is a drop-in fit with no code changes.
ENV PORT=7860
EXPOSE 7860

CMD ["python", "run.py", "serve"]
