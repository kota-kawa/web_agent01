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
COPY vnc/requirements.txt /tmp/vnc-requirements.txt

RUN pip install --no-cache-dir -r /tmp/vnc-requirements.txt \
  && pip install --no-cache-dir "playwright[all]==1.44.0" \
  && playwright install --with-deps chromium
  


WORKDIR /opt

# ① build-context (= ./vnc) 全体を /opt/vnc へコピー
COPY . /opt/vnc

# 依存関係が正しくインストールされていることをビルド時に検証
ENV PYTHONPATH=/opt
RUN python -m vnc.dependency_check --component vnc

# ② 既存パス互換のためエントリポイントにシンボリックリンクを張る
RUN ln -s /opt/vnc/automation_server.py /opt/automation_server.py

# コンテキストをルート (.) のままにした場合、
# vnc 配下への相対パスを指定する
COPY vnc/automation_server.py ./automation_server.py
COPY vnc/scripts/start-chromium.sh /usr/local/bin/start-chromium.sh
COPY vnc/supervisor/ /etc/supervisor/conf.d/

# 実行ビットを付与
RUN chmod +x /usr/local/bin/start-chromium.sh

EXPOSE 6901 9222 7000
CMD ["/usr/bin/supervisord","-n"]
