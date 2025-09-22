FROM python:3.12-slim

# ---- 依存ライブラリ ---------------------------------------------------
# Debian では Ubuntu フォントが non-free コンポーネントに配置されているため、
# 事前に non-free を有効化してから必要パッケージを導入する。
RUN set -eux; \
    python - <<'PY'
from pathlib import Path
import re

extras = ["contrib", "non-free", "non-free-firmware"]


def update_deb_list(path: Path) -> None:
    if not path.exists():
        return
    lines = path.read_text().splitlines()
    changed = False
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = line.split()
        if not parts or not (parts[0] == "deb" or parts[0] == "deb-src"):
            continue
        start = 3
        if len(parts) > 1 and parts[1].startswith("["):
            for offset in range(1, len(parts)):
                if parts[offset].endswith("]"):
                    start = offset + 2
                    break
        if start >= len(parts):
            continue
        components = parts[start:]
        original = components[:]
        for extra in extras:
            if extra not in components:
                components.append(extra)
        if components != original:
            lines[idx] = " ".join(parts[:start] + components)
            changed = True
    if changed:
        path.write_text("\n".join(lines) + "\n")


def update_deb822(path: Path) -> None:
    if not path.exists():
        return
    text = path.read_text()

    def repl(match: re.Match) -> str:
        components = match.group(1).split()
        original = components[:]
        for extra in extras:
            if extra not in components:
                components.append(extra)
        if components == original:
            return match.group(0)
        return "Components: " + " ".join(components)

    new_text = re.sub(r"^Components:\s*(.*)$", repl, text, flags=re.MULTILINE)
    if new_text != text:
        path.write_text(new_text)


update_deb_list(Path("/etc/apt/sources.list"))
sources_dir = Path("/etc/apt/sources.list.d")
if sources_dir.exists():
    for list_file in sources_dir.glob("*.list"):
        update_deb_list(list_file)
    for sources_file in sources_dir.glob("*.sources"):
        update_deb822(sources_file)
PY
    apt-get update && apt-get install -y --no-install-recommends \
      chromium xvfb x11vnc websockify novnc supervisor \
      wget curl ca-certificates gnupg \
      libnss3 libatk-bridge2.0-0 libu2f-udev libdrm2 \
      libgbm1 libgtk-3-0 libasound2 libxdamage1 libxfixes3 \
      libxrandr2 libxcomposite1 libxcursor1 libxinerama1 \
      libpango-1.0-0 libpangocairo-1.0-0 \
      fonts-ipafont fonts-ubuntu fonts-unifont gstreamer1.0-libav \
  && rm -rf /var/lib/apt/lists/*

# ---- Python ライブラリ -------------------------------------------------
RUN pip install --no-cache-dir \
      "playwright[all]==1.44.0" \
      flask \
      httpx \
      "jsonschema>=4.0" \
      "pydantic>=2.5" \
  && playwright install chromium
  


WORKDIR /opt

# ① build-context (= ./vnc) 全体を /opt/vnc へコピー
COPY . /opt/vnc

# ② 既存パス互換のためエントリポイントにシンボリックリンクを張る
RUN ln -s /opt/vnc/automation_server.py /opt/automation_server.py

# コンテキストをルート (.) のままにした場合、
# vnc 配下への相対パスを指定する
COPY vnc/automation_server.py ./automation_server.py
COPY vnc/scripts/start-chromium.sh /usr/local/bin/start-chromium.sh
COPY vnc/supervisor/ /etc/supervisor/conf.d/

# 実行ビットを付与
RUN chmod +x /usr/local/bin/start-chromium.sh

# ④ Python から /opt 配下をパッケージ検索できるように
ENV PYTHONPATH=/opt

EXPOSE 6901 9222 7000
CMD ["/usr/bin/supervisord","-n"]
