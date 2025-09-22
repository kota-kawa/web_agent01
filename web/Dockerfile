FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Ensure the base image provides a new enough interpreter for browser-use
RUN python - <<'PY'
import sys
minimum = (3, 11)
if sys.version_info < minimum:
    raise SystemExit(
        f"Python {minimum[0]}.{minimum[1]}+ is required, found {sys.version}"
    )
PY

COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install chromium

COPY . .

EXPOSE 5000
CMD ["python", "app.py"]
