import os
import json
import logging
import requests
from flask import (
    Flask,
    request,
    jsonify,
    render_template,
    Response,
    send_from_directory,
)

# --------------- Agent modules -----------------------------------
from agent.llm.client import call_llm
from agent.browser.vnc import (
    get_html as vnc_html,
    execute_dsl,
    get_elements as vnc_elements,
    get_dom_tree as vnc_dom_tree,
)
from agent.controller.prompt import build_prompt
from agent.utils.history import load_hist, save_hist
from agent.utils.html import strip_html

# --------------- Flask & Logger ----------------------------------
app = Flask(__name__)
log = logging.getLogger("agent")
log.setLevel(logging.INFO)

# --------------- VNC / Playwright API ----------------------------
VNC_API = "http://vnc:7000"  # Playwright 側の API
START_URL = os.getenv("START_URL", "https://www.yahoo.co.jp")
MAX_STEPS = int(os.getenv("MAX_STEPS", "30"))

# --------------- Conversation History ----------------------------
LOG_DIR = os.getenv("LOG_DIR", "./")
os.makedirs(LOG_DIR, exist_ok=True)
HIST_FILE = os.path.join(LOG_DIR, "conversation_history.json")


@app.get("/history")
def get_history():
    try:
        return jsonify(load_hist())
    except Exception as e:
        log.error("get_history error: %s", e)
        return jsonify(error=str(e)), 500


@app.get("/history.json")
def download_history():
    if os.path.exists(HIST_FILE):
        return send_from_directory(
            directory=os.path.dirname(HIST_FILE),
            path=os.path.basename(HIST_FILE),
            mimetype="application/json",
        )
    return jsonify(error="history file not found"), 404


# ----- Memory endpoint -----
@app.get("/memory")
def memory():
    try:
        return jsonify(load_hist())
    except Exception as e:
        log.error("memory error: %s", e)
        return jsonify(error=str(e)), 500


# --------------- API ---------------------------------------------
@app.post("/execute")
def execute():
    data = request.get_json(force=True)
    cmd = data.get("command", "").strip()
    if not cmd:
        return jsonify(error="command empty"), 400

    page = data.get("pageSource") or vnc_html()
    shot = data.get("screenshot")
    model = data.get("model", "gemini")
    hist = load_hist()
    elements, dom_err = vnc_dom_tree()
    prompt = build_prompt(cmd, page, hist, bool(shot), elements, dom_err)
    res = call_llm(prompt, model, shot)

    hist.append({"user": cmd, "bot": res})
    save_hist(hist)
    return jsonify(res)


@app.post("/automation/execute-dsl")
def forward_dsl():
    payload = request.get_json(force=True)
    if not payload.get("actions"):
        return Response("", 200, mimetype="text/plain")
    try:
        res_text = execute_dsl(payload, timeout=120)
        return Response(res_text, 200, mimetype="text/plain")
    except requests.Timeout:
        log.error("forward_dsl timeout")
        return jsonify(error="timeout"), 504
    except Exception as e:
        log.error("forward_dsl error: %s", e)
        return jsonify(error=str(e)), 500


@app.get("/vnc-source")
def vhtml():
    return Response(vnc_html(), mimetype="text/plain")


@app.get("/screenshot")
def get_screenshot():
    """VNCサーバーからスクリーンショットを取得してブラウザに返す"""
    try:
        res = requests.get(f"{VNC_API}/screenshot", timeout=300)
        res.raise_for_status()
        return Response(res.text, mimetype="text/plain")
    except Exception as e:
        log.error("get_screenshot error: %s", e)
        return jsonify(error=str(e)), 500


# --------------- UI エントリポイント ------------------------------
@app.route("/")
def outer():
    return render_template(
        "layout.html",
        vnc_url="http://localhost:6901/vnc.html?host=localhost&port=6901",
        start_url=START_URL,
        max_steps=MAX_STEPS,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
