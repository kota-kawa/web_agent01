#!/usr/bin/env bash
set -e
export DISPLAY=:0
export LANG=ja_JP.UTF-8

for i in {1..30}; do
  xset q >/dev/null 2>&1 && break
  echo "[chromium] waiting for X..." ; sleep 1
done

# ★ 起動 URL を /client に変更（チャットなし）
URL="http://web:5000/client"

if ! pgrep -f "--remote-debugging-port=9222" >/dev/null; then
  echo "[chromium] launching chromium..."
  chromium --no-sandbox --disable-dev-shm-usage \
           --remote-debugging-address=0.0.0.0 --remote-debugging-port=9222 \
           --window-size=1280,800 "$URL" &
fi

tail -f /dev/null
