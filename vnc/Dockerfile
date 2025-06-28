FROM python:3.12-slim

# ---- 依存ライブラリ ---------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
      chromium xvfb x11vnc websockify novnc supervisor \
      wget curl ca-certificates gnupg \
      libnss3 libatk-bridge2.0-0 libu2f-udev libdrm2 \
      libgbm1 libgtk-3-0 libasound2 libxdamage1 libxfixes3 \
      libxrandr2 libxcomposite1 libxcursor1 libxinerama1 \
      libpango-1.0-0 libpangocairo-1.0-0 \
      fonts-ipafont gstreamer1.0-libav \
  && rm -rf /var/lib/apt/lists/*

# ---- Python ライブラリ -------------------------------------------------
RUN pip install --no-cache-dir "playwright[all]==1.44.0" flask httpx jsonschema>=4.0 \
  && playwright install --with-deps chromium
  
ENV PYTHONPATH=/opt:${PYTHONPATH}

# ---- アプリ本体 --------------------------------------------------------
WORKDIR /opt
COPY automation_server.py .
COPY scripts/start-chromium.sh /usr/local/bin/start-chromium.sh
COPY supervisor/ /etc/supervisor/conf.d/
RUN chmod +x /usr/local/bin/start-chromium.sh

EXPOSE 6901 9222 7000
CMD ["/usr/bin/supervisord","-n"]
