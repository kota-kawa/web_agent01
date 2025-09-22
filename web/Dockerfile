FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install and activate Python 3.11 before performing any checks so the
# remainder of the build (and the runtime environment) uses the expected
# interpreter version.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3.11 \
        python3.11-distutils \
        python3.11-venv \
        python3-pip \
    && rm -rf /var/lib/apt/lists/* \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.10 1 \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.11 2 \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.10 1 \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 2

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
