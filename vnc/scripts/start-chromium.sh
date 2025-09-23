#!/usr/bin/env bash
set -e
export DISPLAY=:0
export LANG=ja_JP.UTF-8

ready_for_xset=false
for i in {1..30}; do
  if xset q >/dev/null 2>&1; then
    ready_for_xset=true
    break
  fi
  echo "[chromium] waiting for X..." ; sleep 1
done

if [ "$ready_for_xset" = true ]; then
  # Disable screen blanking and power management so the browser view
  # stays visible even during long-running executions.
  xset s off          || true
  xset s noblank      || true
  xset -dpms          || true
else
  echo "[chromium] warning: could not configure xset (X server not ready)"
fi

# 既定の起動 URL を Yahoo! JAPAN にして、すぐに利用できる状態にする
URL="${START_URL:-https://www.yahoo.co.jp/}"

if ! pgrep -f "--remote-debugging-port=9222" >/dev/null; then
  echo "[chromium] launching chromium..."
  chromium --no-sandbox --disable-dev-shm-usage \
           --remote-debugging-address=0.0.0.0 --remote-debugging-port=9222 \
           --window-size=1280,800 "$URL" &
fi

tail -f /dev/null
