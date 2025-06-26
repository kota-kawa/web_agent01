import os
import json
import logging

log = logging.getLogger(__name__)

LOG_DIR = os.getenv("LOG_DIR", "./")
os.makedirs(LOG_DIR, exist_ok=True)
HIST_FILE = os.path.join(LOG_DIR, "conversation_history.json")

def load_hist():
    try:
        return json.load(open(HIST_FILE, encoding="utf-8")) if os.path.exists(HIST_FILE) else []
    except Exception as e:
        log.error("load_hist error: %s", e)
        return []

def save_hist(h):
    try:
        json.dump(h, open(HIST_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except Exception as e:
        log.error("save_hist error: %s", e)
