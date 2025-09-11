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
from agent.browser.dom import DOMElementNode
from agent.controller.prompt import build_prompt
from agent.utils.history import load_hist, save_hist
from agent.utils.html import strip_html

# --------------- Flask & Logger ----------------------------------
app = Flask(__name__)
log = logging.getLogger("agent")
log.setLevel(logging.INFO)


@app.errorhandler(500)
def internal_server_error(error):
    """Global error handler to convert 500 errors to JSON warnings."""
    import uuid
    correlation_id = str(uuid.uuid4())[:8]
    error_msg = f"Internal server error - {str(error)}"
    log.exception("[%s] Unhandled exception: %s", correlation_id, error_msg)
    
    return jsonify({
        "error": f"Internal failure - An unexpected error occurred",
        "correlation_id": correlation_id
    }), 200  # Return 200 instead of 500


@app.errorhandler(Exception)
def handle_exception(error):
    """Global exception handler to catch all uncaught exceptions."""
    import uuid
    correlation_id = str(uuid.uuid4())[:8]
    log.exception("[%s] Uncaught exception: %s", correlation_id, str(error))
    
    return jsonify({
        "error": f"Internal failure - {str(error)}",
        "correlation_id": correlation_id
    }), 200  # Return 200 instead of 500

# --------------- VNC / Playwright API ----------------------------
VNC_API = "http://vnc:7000"  # Playwright 側の API
START_URL = os.getenv("START_URL", "https://www.yahoo.co.jp")
MAX_STEPS = int(os.getenv("MAX_STEPS", "30"))

# --------------- Conversation History ----------------------------
LOG_DIR = os.getenv("LOG_DIR", "./")
os.makedirs(LOG_DIR, exist_ok=True)
HIST_FILE = os.path.join(LOG_DIR, "conversation_history.json")


@app.route("/history", methods=["GET"])
def get_history():
    try:
        history_data = load_hist()
        return jsonify(history_data)
    except Exception as e:
        import uuid
        correlation_id = str(uuid.uuid4())[:8]
        log.error("[%s] get_history error: %s", correlation_id, e)
        # Return structured error response instead of 500
        return jsonify({
            "error": f"Failed to load history - {str(e)}",
            "correlation_id": correlation_id,
            "data": []  # Provide empty data as fallback
        }), 200


@app.route("/history.json", methods=["GET"])
def download_history():
    if os.path.exists(HIST_FILE):
        return send_from_directory(
            directory=os.path.dirname(HIST_FILE),
            path=os.path.basename(HIST_FILE),
            mimetype="application/json",
        )
    return jsonify(error="history file not found"), 404


# ----- Memory endpoint -----
@app.route("/memory", methods=["GET"])
def memory():
    try:
        history_data = load_hist()
        return jsonify(history_data)
    except Exception as e:
        import uuid
        correlation_id = str(uuid.uuid4())[:8]
        log.error("[%s] memory error: %s", correlation_id, e)
        # Return structured error response instead of 500
        return jsonify({
            "error": f"Failed to load memory - {str(e)}",
            "correlation_id": correlation_id,
            "data": []  # Provide empty data as fallback
        }), 200


# ----- Reset endpoint -----
@app.post("/reset")
def reset():
    """Reset conversation history by clearing the history file"""
    try:
        # Clear the history by saving an empty list
        save_hist([])
        log.info("Conversation history reset successfully")
        return jsonify({"status": "success", "message": "会話履歴がリセットされました"})
    except Exception as e:
        log.error("reset error: %s", e)
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
    prev_error = data.get("error")
    hist = load_hist()
    elements, dom_err = vnc_dom_tree()
    if elements is None:
        try:
            fallback = vnc_elements()
            elements = [
                DOMElementNode(
                    tagName=e.get("tag", ""),
                    attributes={
                        k: v
                        for k, v in {
                            "id": e.get("id"),
                            "class": e.get("class"),
                        }.items()
                        if v
                    },
                    text=e.get("text"),
                    xpath=e.get("xpath", ""),
                    highlightIndex=e.get("index"),
                    isVisible=True,
                    isInteractive=True,
                )
                for e in fallback
            ]
        except Exception as fbe:
            log.error("fallback elements error: %s", fbe)
    err_msg = "\n".join(filter(None, [prev_error, dom_err])) or None
    prompt = build_prompt(cmd, page, hist, bool(shot), elements, err_msg)
    res = call_llm(prompt, model, shot)

    hist.append({"user": cmd, "bot": res})
    save_hist(hist)
    return jsonify(res)


@app.post("/store-warnings")
def store_warnings():
    """Store warnings in the last conversation history item."""
    try:
        data = request.get_json(force=True)
        warnings = data.get("warnings", [])
        
        if not warnings:
            return jsonify({"status": "success", "message": "No warnings to store"})
        
        # Load current history
        hist = load_hist()
        
        if not hist:
            log.warning("No conversation history found to update with warnings")
            return jsonify({"status": "error", "message": "No conversation history found"})
        
        # Get the last conversation item
        last_item = hist[-1]
        
        # Add warnings to the bot response, above the "complete" field
        if "bot" in last_item and isinstance(last_item["bot"], dict):
            # Make a copy of bot response to preserve order
            bot_response = last_item["bot"].copy()
            
            # Remove complete field temporarily
            complete_value = bot_response.pop("complete", True)
            
            # Add warnings
            bot_response["warnings"] = warnings
            
            # Re-add complete field at the end
            bot_response["complete"] = complete_value
            
            # Update the history item
            last_item["bot"] = bot_response
            
            # Save updated history
            save_hist(hist)
            
            log.info("Added %d warnings to conversation history", len(warnings))
            return jsonify({"status": "success", "message": f"Stored {len(warnings)} warnings"})
        else:
            log.warning("Invalid conversation history format - cannot add warnings")
            return jsonify({"status": "error", "message": "Invalid conversation history format"})
            
    except Exception as e:
        log.error("store_warnings error: %s", e)
        return jsonify({"status": "error", "message": f"Failed to store warnings: {str(e)}"})


@app.post("/automation/execute-dsl")
def forward_dsl():
    payload = request.get_json(force=True)
    if not payload.get("actions"):
        return jsonify({"html": "", "warnings": []})
    try:
        res_obj = execute_dsl(payload, timeout=120)
        return jsonify(res_obj)
    except requests.Timeout:
        log.error("forward_dsl timeout")
        return jsonify({"html": "", "warnings": ["ERROR:auto:Request timeout - The operation took too long to complete"]})
    except Exception as e:
        log.error("forward_dsl error: %s", e)
        return jsonify({"html": "", "warnings": [f"ERROR:auto:Communication error - {str(e)}"]})


@app.route("/vnc-source", methods=["GET"])
def vhtml():
    return Response(vnc_html(), mimetype="text/plain")


@app.route("/screenshot", methods=["GET"])
def get_screenshot():
    """VNCサーバーからスクリーンショットを取得してブラウザに返す"""
    try:
        res = requests.get(f"{VNC_API}/screenshot", timeout=300)
        res.raise_for_status()
        return Response(res.text, mimetype="text/plain")
    except Exception as e:
        log.error("get_screenshot error: %s", e)
        return Response("", mimetype="text/plain")


# --------------- UI エントリポイント ------------------------------
@app.route("/")
def outer():
    return render_template(
        "layout.html",
        vnc_url="http://localhost:6901/vnc.html?host=localhost&port=6901&resize=scale",
        start_url=START_URL,
        max_steps=MAX_STEPS,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
