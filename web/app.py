import os
import json
import re
import logging
import requests
from flask import Flask, request, jsonify, render_template, Response, send_from_directory

# --------------- Gemini SDK --------------------------------------
from llm import call_llm

# --------------- Flask & Logger ----------------------------------
app = Flask(__name__)
log = logging.getLogger("agent")
log.setLevel(logging.INFO)

# --------------- VNC / Playwright API ----------------------------
VNC_API = "http://vnc:7000"          # Playwright 側の API

def vnc_html() -> str:
    """Playwright 側から現在ページの HTML ソースを取得。失敗時は空文字"""
    try:
        res = requests.get(f"{VNC_API}/source", timeout=30)
        res.raise_for_status()
        return res.text
    except Exception as e:
        log.error("vnc_html error: %s", e)
        return ""

# --------------- Conversation History ----------------------------
LOG_DIR   = os.getenv("LOG_DIR", "./")
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
        json.dump(h, open(HIST_FILE, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
    except Exception as e:
        log.error("save_hist error: %s", e)

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
            filename=os.path.basename(HIST_FILE),
            mimetype="application/json"
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

# --------------- Helper ------------------------------------------
def strip_html(ht: str) -> str:
    """style/script を削除してテキスト量を圧縮"""
    ht = re.sub(r"<style.*?>.*?</style>", "", ht, flags=re.S | re.I)
    ht = re.sub(r"<script.*?>.*?</script>", "", ht, flags=re.S | re.I)
    return ht.strip()

# --------------- システムプロンプト ------------------------------
def build_prompt(cmd: str, page: str, hist):
    """
    LLM に与える完全なシステムプロンプトを返す
    """
    past_conv = "\n".join(f"U:{h['user']}\nA:{h['bot']['explanation']}" for h in hist)
    
    system_prompt = (
        # --- before -------------------------------------------------
        # "あなたは高性能な Web 自動操作エージェントです。\n"
        # "### 目的\n"
        # "ユーザーの自然言語命令を受け取り、Playwright 互換の DSL(JSON) でブラウザ操作手順を生成します。\n"
        # "まず **現在表示されているページ(HTML ソースを渡します)** を必ず確認し、"
        # "そこに命令を満たす情報や要素が存在する場合は **無駄なページ遷移(`navigate`)やクリックを行わず** に完了してください。\n"
        # --- after  -------------------------------------------------
        "あなたは高性能な Web 自動操作エージェントです。\n"
        "### 目的\n"
        "ユーザーの自然言語命令を受け取り、Playwright 互換の DSL(JSON) でブラウザ操作手順を生成します。\n"
        "まず **現在表示されているページ(HTML ソースを渡します)** を必ず確認し、"
        "そこに命令を満たす情報や要素が存在する場合は **無駄なページ遷移(`navigate`)やクリックを行わず** に完了してください。\n"
        "さらに **ユーザーがページ内の具体的なテキスト情報を求めている場合は、その情報を抽出して説明に含めて返す** こと。\n"
        "（例: 『開催概要を教えて』→ ページにある開催概要を説明文に貼り付ける）\n"
        "\n"
        # -------------------------------------------------------------
        "### 出力フォーマット\n"
        # --- before -------------------------------------------------
        # "1 行目 : 日本語で 80 文字以内の簡潔な説明 (何をするか / 何を確認したか)\n"
        # "2 行目以降 : ```json フェンス内に DSL を出力\n"
        # --- after  -------------------------------------------------
        "1 行目〜複数行 : 取得した情報や操作意図を日本語で説明。\n"
        "         ユーザーが求めたページ内情報があれば **ここに要約または全文を含める**。\n"
        "         80 文字制限は撤廃して良いが、最長 300 文字程度に収める。\n"
        "その後に ```json フェンス内で DSL を出力。\n"
        # -------------------------------------------------------------
        "\n"
        "```json の中身は以下のフォーマット:\n"
        "{\n"
        '  "actions": [ <action_object> , ... ],\n'
        '  "complete": true | false               # true ならタスク完了, false なら未完了で続行\n'
        "}\n"
        "\n"
        "<action_object> は次のいずれか:\n"
        "  { \"action\": \"navigate\",   \"target\": \"https://example.com\" }\n"
        "  { \"action\": \"click\",      \"target\": \"css=button.submit\" }\n"
        "  { \"action\": \"click_text\", \"text\":   \"次へ\" }\n"
        "  { \"action\": \"type\",       \"target\": \"css=input[name=q]\", \"value\": \"検索ワード\" }\n"
        "  { \"action\": \"wait\",       \"ms\": 1000 }\n"
        "  { \"action\": \"scroll\",     \"target\": \"css=div.list\", \"direction\": \"down\", \"amount\": 400 }\n"
        "\n"
        "#### ルール\n"
        "1. 現ページで目的達成できる場合は `actions` を **空配列** で返し、`complete:true`。\n"
        "2. `click` は CSS セレクタ、`click_text` は可視テキストで指定。\n"
        "3. 失敗しやすい操作には `wait` を挿入し、安定化を図ること。\n"
        "4. 類似要素が複数ある場合は `:nth-of-type()` などで特定性を高める。\n"
        "5. 一度に大量の操作を出さず、状況確認が必要な場合は `complete:false` とし段階的に進める。\n"
        # --- after  新ルール追加 -----------------------------------
        "6. **ユーザーがページ内テキストを要求している場合**:\n"
        "   - `navigate` や `click` を行わずとも情報が取れるなら `actions` は空。\n"
        "   - 説明部にページから抽出したテキストを含める（長文は冒頭 200 文字＋\"...\"）。\n"
        "   - 例: 『開催概要を教えて』→ 説明行に開催概要の本文を貼り付け、その後 DSL。\n"
        # ------------------------------------------------------------
        "\n"
        "### Few-shot 例\n"
        # --- before 例1 コメントアウト -------------------------------
        # "#### 例1: 現ページに\"ご挨拶\"ボタンがありクリックして完了\n"
        # "```json\n"
        # "{\n"
        # '  \"actions\": [ { \"action\": \"click_text\", \"text\": \"ご挨拶\" } ],\n'
        # "  \"complete\": true\n"
        # "}\n"
        # "```\n"
        # --- after  改訂例1 -----------------------------------------
        "#### 例1: ページに『開催概要』テキストあり→ 抽出して完了\n"
        "開催概要: 2025 年 7 月 15 日〜17 日、東京ビッグサイトで開催...（以下略）\n"
        "```json\n"
        "{\n"
        "  \"actions\": [],\n"
        "  \"complete\": true\n"
        "}\n"
        "```\n"
        # ------------------------------------------------------------
        "#### 例2: 検索ページでキーワードを入力し結果ページに遷移、まだ完了しない\n"
        "```json\n"
        "{\n"
        '  \"actions\": [\n'
        '    { \"action\": \"type\",     \"target\": \"css=input[name=q]\", \"value\": \"Python\" },\n'
        '    { \"action\": \"click\",    \"target\": \"css=button.search\" },\n'
        '    { \"action\": \"wait\",     \"ms\": 1500 }\n'
        '  ],\n'
        "  \"complete\": false\n"
        "}\n"
        "```\n"
        "\n"
        "---- 現在のページ HTML(一部) ----\n"
        f"{page}\n"
        "--------------------------------\n"
        f"## これまでの会話履歴\n{past_conv}\n"
        "--------------------------------\n"
        f"## ユーザー命令\n{cmd}\n"
    )
    return system_prompt


# --------------- API ---------------------------------------------
@app.post("/execute")
def execute():
    data = request.get_json(force=True)
    cmd  = data.get("command", "").strip()
    if not cmd:
        return jsonify(error="command empty"), 400

    page  = data.get("pageSource") or vnc_html()
    model = data.get("model", "gemini")
    hist  = load_hist()
    prompt = build_prompt(cmd, strip_html(page), hist)
    res   = call_llm(prompt, model)

    hist.append({"user": cmd, "bot": res})
    save_hist(hist)
    return jsonify(res)

@app.post("/automation/execute-dsl")
def forward_dsl():
    payload = request.get_json(force=True)
    if not payload.get("actions"):
        return Response("", 200, mimetype="text/plain")
    try:
        r = requests.post(
            f"{VNC_API}/execute-dsl",
            json=payload,
            timeout=60,
        )
        return Response(r.text, r.status_code, mimetype="text/plain")
    except requests.Timeout:
        log.error("forward_dsl timeout")
        return jsonify(error="timeout"), 504
    except Exception as e:
        log.error("forward_dsl error: %s", e)
        return jsonify(error=str(e)), 500

@app.get("/vnc-source")
def vhtml():
    return Response(vnc_html(), mimetype="text/plain")

# --------------- UI エントリポイント ------------------------------
@app.route("/")
def outer():
    return render_template("layout.html", vnc_url="http://localhost:6901/vnc.html?host=localhost&port=6901")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
