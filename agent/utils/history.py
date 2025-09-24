import os
import json
import logging
from typing import Any, Sequence

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


def append_history_entry(user, bot, url=None):
    """Append a single conversation interaction ensuring URL is stored.

    Parameters
    ----------
    user : str
        The user command or message.
    bot : Any
        The bot response to persist.
    url : str, optional
        The page URL at the time of interaction. If ``None``, an attempt is
        made to retrieve the current URL from the VNC browser module.
    """

    try:
        history = load_hist()

        if url is None:
            try:
                from agent.browser.vnc import get_url
                url = get_url()
            except Exception:
                url = None

        history.append({"user": user, "bot": bot, "url": url})
        save_hist(history)
    except Exception as e:
        log.error("append_history_entry error: %s", e)


def _normalise_text(value: Any, *, max_length: int = 160) -> str:
    """Return a compact, human-readable string representation."""

    if value is None:
        return ""

    if isinstance(value, str):
        text = value.strip()
    else:
        text = str(value).strip()

    if not text:
        return ""

    # Collapse whitespace to keep the prompt concise
    compact = " ".join(text.split())
    if not compact:
        return ""

    if len(compact) > max_length:
        return compact[: max_length - 1] + "…"
    return compact


def _first_non_empty(items: Sequence[Any]) -> str:
    for item in items:
        text = _normalise_text(item)
        if text:
            return text
    return ""


def format_history_for_prompt(
    history: Sequence[dict[str, Any]] | None, *, limit: int = 5
) -> str:
    """Format recent conversation history for use in system prompts."""

    if not history:
        return ""

    if limit > 0:
        recent = history[-limit:]
    else:
        recent = history

    start_index = len(history) - len(recent) + 1
    blocks: list[str] = []

    for offset, entry in enumerate(recent):
        if not isinstance(entry, dict):
            continue

        user_text = _normalise_text(entry.get("user"))
        if not user_text:
            continue

        lines = [f"[{start_index + offset}] ユーザー指示: {user_text}"]
        details: list[str] = []

        bot = entry.get("bot")
        if isinstance(bot, dict):
            status_text = _normalise_text(bot.get("status"))
            if status_text:
                details.append(f"ステータス: {status_text}")

            result = bot.get("result")
            if isinstance(result, dict):
                success = result.get("success")
                if success is True:
                    details.append("完了状態: 成功")
                elif success is False:
                    details.append("完了状態: 失敗")

                final_result = _normalise_text(result.get("final_result"))
                if final_result:
                    details.append(f"要約: {final_result}")

                error_text = ""
                errors = result.get("errors")
                if isinstance(errors, Sequence):
                    error_text = _first_non_empty(errors)
                if error_text:
                    details.append(f"エラー: {error_text}")

                warning_text = ""
                warnings = result.get("warnings")
                if isinstance(warnings, Sequence):
                    warning_text = _first_non_empty(warnings)
                if warning_text:
                    details.append(f"警告: {warning_text}")

            top_error = _normalise_text(bot.get("error"))
            if top_error:
                details.append(f"エラー: {top_error}")

            steps = bot.get("steps")
            if isinstance(steps, Sequence) and steps:
                last_step = steps[-1]
                if isinstance(last_step, dict):
                    title_text = _normalise_text(last_step.get("title"))
                    if title_text:
                        details.append(f"最終ページタイトル: {title_text}")

        url_text = _normalise_text(entry.get("url"))
        if url_text:
            details.append(f"最終URL: {url_text}")

        if details:
            lines.extend(f"    - {detail}" for detail in details)
        blocks.append("\n".join(lines))

    return "\n".join(blocks).strip()

