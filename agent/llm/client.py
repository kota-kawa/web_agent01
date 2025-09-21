import os
import json
import re
import logging
from typing import Dict

import google.generativeai as genai
from groq import Groq
import datetime
import base64
import time

log = logging.getLogger("llm")


LOG_DIR = os.getenv("LOG_DIR", "./")
SCREENSHOT_DIR = os.path.join(LOG_DIR, "screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

#gemini-2.5-flash-lite
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

_groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None


def extract_json(txt: str) -> Dict:
    txt = re.sub(r"```(?:json)?|```", "", txt, flags=re.I)
    dec = json.JSONDecoder()
    idx = 0
    while idx < len(txt):
        if txt[idx] == "{":
            try:
                obj, _ = dec.raw_decode(txt[idx:])
                return obj
            except json.JSONDecodeError:
                pass
        idx += 1
    raise ValueError("no JSON found")


def _normalize_action(a: Dict) -> Dict:
    act = {k.lower(): v for k, v in a.items()}
    act["action"] = act.get("action", "").lower()

    if act["action"] == "click_text" and "target" not in act and "text" in act:
        act["target"] = act["text"]

    if act["action"] == "click" and "target" not in act and "text" in act:
        act["action"] = "click_text"
        act["target"] = act["text"]

    if act["action"] == "wait" and "ms" not in act:
        act["ms"] = 500

    if act["action"] == "wait_for_selector" and "ms" not in act:
        act["ms"] = 3000

    if act["action"] == "press_key" and "key" not in act:
        act["key"] = "Enter"

    if act["action"] == "go_to_url" and "target" not in act and "url" in act:
        act["target"] = act["url"]

    if act["action"] == "click_element_by_index" and "index" in act:
        try:
            act["index"] = int(act["index"])
        except Exception:
            pass

    if act["action"] == "input_text":
        if "clear_existing" not in act:
            act["clear_existing"] = True
        if "index" in act:
            try:
                act["index"] = int(act["index"])
            except Exception:
                pass

    if act["action"] == "scroll":
        if "down" not in act and "direction" in act:
            act["down"] = act["direction"].lower() != "up"
        if "num_pages" in act:
            try:
                act["num_pages"] = float(act["num_pages"])
            except Exception:
                pass
        if "frame_element_index" in act:
            try:
                act["frame_element_index"] = int(act["frame_element_index"])
            except Exception:
                pass

    return act


def _post_process(raw: str) -> Dict:
    expl = re.split(r"```json", raw, 1)[0].strip()
    try:
        js = extract_json(raw)
    except Exception as e:
        log.error("JSON parse error: %s", e)
        #return {"explanation": expl or "JSON 抽出失敗", "actions": [], "raw": raw, "complete": True}
        return {"explanation": expl or "JSON 抽出失敗", "actions": [], "complete": True} 
    
    acts = []
    for act in js.get("actions", []):
        if isinstance(act, dict) and "commands" in act:
            for c in act["commands"]:
                acts.append(_normalize_action({"action": c.get("command"), **c}))
        else:
            acts.append(_normalize_action(act))

    return {
        "explanation": expl,
        "actions": acts,
        #"raw": raw,
        "complete": js.get("complete", True),
    }


def call_gemini(prompt: str, screenshot: str | None = None) -> Dict:
    model_name = GEMINI_MODEL if not screenshot else "models/gemini-2.5-flash"

    img_bytes = None
    if screenshot:
        img_b64 = screenshot.split(",", 1)[-1]
        img_bytes = base64.b64decode(img_b64)

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        ss_path = os.path.join(SCREENSHOT_DIR, f"ss_{timestamp}.png")
        with open(ss_path, "wb") as f:
            f.write(img_bytes)
        log.info(f"Screenshot saved to {ss_path}")

    for attempt in range(2):
        try:
            model = genai.GenerativeModel(model_name)
            if screenshot:
                raw = model.generate_content([prompt, {"mime_type": "image/png", "data": img_bytes}]).text
            else:
                raw = model.start_chat(history=[]).send_message(prompt).text
            log.info("◆ GEMINI RAW ◆\n%s\n◆ END RAW ◆", raw)
            return _post_process(raw)
        except Exception as e:
            is_rate_limit = (
                getattr(getattr(e, "response", None), "status_code", None) == 429
                or "429" in str(e)
            )
            if is_rate_limit and attempt == 0:
                log.warning("Gemini rate limit exceeded: %s. Retrying in 60 seconds...", e)
                time.sleep(60)
                continue
            log.error("Gemini call failed: %s", e)
            return {"explanation": "Gemini 呼び出し失敗", "actions": [], "raw": "", "complete": True}

    return {"explanation": "Gemini 呼び出し失敗", "actions": [], "raw": "", "complete": True}


def call_groq(prompt: str, screenshot: str | None = None) -> Dict:
    if not _groq_client:
        return {"explanation": "Groq API key 未設定", "actions": [], "raw": "", "complete": True}

    try:
        content = [{"type": "text", "text": prompt}]
        if screenshot:
            
            img_b64 = screenshot.split(",", 1)[-1]
            img_bytes = base64.b64decode(img_b64)
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            ss_path = os.path.join(SCREENSHOT_DIR, f"ss_{timestamp}.png")
            with open(ss_path, "wb") as f:
                f.write(img_bytes)
            log.info(f"Screenshot saved to {ss_path}")
            
            content.append({"type": "image_url", "image_url": {"url": screenshot}})
        res = _groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": content}],
        )
        raw = res.choices[0].message.content
    except Exception as e:
        log.error("Groq call failed: %s", e)
        return {"explanation": "Groq 呼び出し失敗", "actions": [], "raw": "", "complete": True}

    log.info("◆ GROQ RAW ◆\n%s\n◆ END RAW ◆", raw)
    return _post_process(raw)


def call_llm(prompt: str, model: str = "gemini", screenshot: str | None = None) -> Dict:
    if model == "groq":
        return call_groq(prompt, screenshot)
    return call_gemini(prompt, screenshot)
