import os
import json
import re
import logging
from typing import Dict

import google.generativeai as genai
from groq import Groq

log = logging.getLogger("llm")

# Configure clients
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "models/gemini-2.0-flash")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

_groq_client = None
if GROQ_API_KEY:
    _groq_client = Groq(api_key=GROQ_API_KEY)

def extract_json(txt: str) -> Dict:
    """Extract the first JSON object from text."""
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

    return act

def _post_process(raw: str) -> Dict:
    """Parse raw LLM output and return normalized result."""
    expl = re.split(r"```json", raw, 1)[0].strip()
    try:
        js = extract_json(raw)
    except Exception as e:
        log.error("JSON parse error: %s", e)
        return {"explanation": expl or "JSON 抽出失敗", "actions": [], "raw": raw, "complete": True}

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
        "raw": raw,
        "complete": js.get("complete", True)
    }

import base64

def call_gemini(prompt: str, screenshot: str | None = None) -> Dict:
    try:
        model_name = GEMINI_MODEL if not screenshot else "models/gemini-pro-vision"
        model = genai.GenerativeModel(model_name)
        if screenshot:
            img_b64 = screenshot.split(",", 1)[-1]
            img_bytes = base64.b64decode(img_b64)
            raw = model.generate_content([prompt, {"mime_type": "image/png", "data": img_bytes}]).text
        else:
            raw = model.start_chat(history=[]).send_message(prompt).text
    except Exception as e:
        log.error("Gemini call failed: %s", e)
        return {"explanation": "Gemini 呼び出し失敗", "actions": [], "raw": "", "complete": True}

    log.info("◆ GEMINI RAW ◆\n%s\n◆ END RAW ◆", raw)
    return _post_process(raw)

def call_groq(prompt: str, screenshot: str | None = None) -> Dict:
    if not _groq_client:
        return {"explanation": "Groq API key 未設定", "actions": [], "raw": "", "complete": True}

    try:
        content = [{"type": "text", "text": prompt}]
        if screenshot:
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
