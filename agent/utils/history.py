import os
import json
import logging

log = logging.getLogger(__name__)

LOG_DIR = os.getenv("LOG_DIR", "./")
os.makedirs(LOG_DIR, exist_ok=True)
HIST_FILE = os.path.join(LOG_DIR, "conversation_history.json")

def load_hist():
    try:
        if not os.path.exists(HIST_FILE):
            return []
        
        # Check if file is empty or has invalid content
        with open(HIST_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                # File is empty, return empty list
                return []
            
            # Parse JSON content
            return json.loads(content)
            
    except (json.JSONDecodeError, ValueError) as e:
        log.error("load_hist JSON parsing error: %s", e)
        # File contains invalid JSON, backup corrupted file and return empty list
        try:
            import shutil
            backup_file = HIST_FILE + ".corrupted.bak"
            shutil.move(HIST_FILE, backup_file)
            log.info("Corrupted history file backed up to: %s", backup_file)
        except Exception as backup_error:
            log.error("Failed to backup corrupted file: %s", backup_error)
        return []
    except Exception as e:
        log.error("load_hist error: %s", e)
        return []

def save_hist(h):
    try:
        # Write to a temporary file first to avoid corruption during writes
        temp_file = HIST_FILE + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(h, f, ensure_ascii=False, indent=2)
        
        # Atomically replace the original file
        import shutil
        shutil.move(temp_file, HIST_FILE)
        
    except Exception as e:
        log.error("save_hist error: %s", e)
        # Clean up temp file if it exists
        try:
            import os
            temp_file = HIST_FILE + ".tmp"
            if os.path.exists(temp_file):
                os.remove(temp_file)
        except Exception:
            pass
