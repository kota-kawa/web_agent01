// browser_executor.js

/* ======================================
   Utility
   ====================================== */
const sleep = ms => new Promise(r => setTimeout(r, ms));
const chatArea   = document.getElementById("chat-area");
const opHistory  = document.getElementById("operation-history");
let stopRequested   = false;

// ★★★ 追加/変更: Pause/Resume 状態管理 ---------------------------
let pausedRequested = false;   // 一時停止フラグ
let resumeResolver  = null;    // 再開時に resolve するコールバック
// ★★★ ここまで ---------------------------------------------------

/* ======================================
   Normalize DSL actions
   ====================================== */
function normalizeActions(instr) {
  if (!instr) return [];
  const acts = Array.isArray(instr.actions) ? instr.actions
             : Array.isArray(instr)          ? instr
             : instr.action                  ? [instr] : [];
  return acts.map(o => {
    const a = {...o};
    if (a.action) a.action = String(a.action).toLowerCase();
    if (a.selector && !a.target) a.target = a.selector;
    if (a.text && a.action === "click_text" && !a.target) a.target = a.text;
    return a;
  });
}

/* ======================================
   Send DSL to Playwright server
   ====================================== */
async function sendDSL(acts) {
  if (!acts.length) return "";
  if (requiresApproval(acts)) {
    if (!confirm("重要な操作を実行しようとしています。続行しますか?")) {
      showSystemMessage("ユーザーが操作を拒否しました");
      return "";
    }
  }
  try {
    const r = await fetch("/automation/execute-dsl", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actions: acts })
    });
    if (!r.ok) {
      console.error("execute-dsl failed:", r.status, await r.text());
      showSystemMessage(`DSL 実行エラー: ${r.status}`);
      return "";
    } else {
      appendHistory(acts);
      return await r.text();
    }
  } catch (e) {
    console.error("execute-dsl fetch error:", e);
    showSystemMessage(`通信エラー: ${e}`);
    return "";
  }
}

function requiresApproval(acts) {
  return acts.some(a => {
    const t = (a.text || a.target || "").toLowerCase();
    return /購入|削除|checkout|pay|支払/.test(t);
  });
}

function appendHistory(acts) {
  if (!opHistory) return;
  acts.forEach(a => {
    const div = document.createElement("div");
    div.textContent = JSON.stringify(a);
    opHistory.appendChild(div);
    opHistory.scrollTop = opHistory.scrollHeight;
  });
}

function showSystemMessage(msg) {
  const p = document.createElement("p");
  p.classList.add("system-message");
  p.textContent = msg;
  chatArea.appendChild(p);
  chatArea.scrollTop = chatArea.scrollHeight;
}

/* ======================================
   Execute one turn
   ====================================== */
async function runTurn(cmd, pageHtml, showInUI = true, model = "gemini", placeholder = null) {
  let html = pageHtml;
  if (!html) {
    html = await fetch("/vnc-source")
      .then(r => (r.ok ? r.text() : ""))
      .catch(() => "");
  }

  const res = await sendCommand(cmd, html, model);

  if (showInUI && res.explanation) {
    if (placeholder) {
      placeholder.textContent = res.explanation;
      placeholder.querySelector(".spinner")?.remove();
    } else {
      const p = document.createElement("p");
      p.classList.add("bot-message");
      p.textContent = res.explanation;
      chatArea.appendChild(p);
      chatArea.scrollTop = chatArea.scrollHeight;
    }
  }

  if (res.raw) console.log("LLM raw output:\n", res.raw);

  const acts = normalizeActions(res);

  let newHtml = html;
  if (acts.length) {
    const ret = await sendDSL([acts[0]]);
    if (ret) newHtml = ret;
  }

  return { cont: res.complete === false && acts.length > 0, explanation: res.explanation || "", html: newHtml };
}

/* ======================================
   Multi-turn executor
   ====================================== */
async function executeTask(cmd, model = "gemini", placeholder = null) {
  let keepLoop  = true;
  let firstIter = true;
  let pageHtml  = await fetch("/vnc-source")
    .then(r => (r.ok ? r.text() : ""))
    .catch(() => "");
  let lastMsg   = "";
  let repeatCnt = 0;
  const MAX_REP = 1;
  stopRequested   = false;
  pausedRequested = false;  // 毎タスク開始時にリセット

  while (keepLoop) {
    if (stopRequested) break;

    // ★★★ 追加/変更: 一時停止処理 -------------------------------
    if (pausedRequested) {
      showSystemMessage("⏸ タスクを一時停止中。ブラウザを手動操作できます。");
      await new Promise(res => { resumeResolver = res; });  // Resume を待つ
      if (stopRequested) break;   // 再開前に停止された場合
      showSystemMessage("▶ タスクを再開します。");
    }
    // ★★★ ここまで ------------------------------------------

    try {
      const { cont, explanation, html } = await runTurn(cmd, pageHtml, true, model, firstIter ? placeholder : null);
      if (html) pageHtml = html;

      if (explanation === lastMsg) {
        repeatCnt += 1;
        if (repeatCnt > MAX_REP) {
          console.warn("同一説明が繰り返されたためループを終了します。");
          break;
        }
      } else {
        lastMsg = explanation;
        repeatCnt = 0;
      }

      keepLoop  = cont;
      firstIter = false;
      if (keepLoop) await sleep(1000);
    } catch (e) {
      console.error("runTurn error:", e);
      await sleep(1000);
    }
  }

  const done = document.createElement("p");
  done.classList.add("system-message");
  done.textContent = stopRequested ? "⏹ タスクを中断しました" : "✅ タスクを終了しました";
  chatArea.appendChild(done);
  chatArea.scrollTop = chatArea.scrollHeight;
}

/* ======================================
   Debug buttons & UI wiring
   ====================================== */
document.getElementById("executeButton")?.addEventListener("click", () => {
  const cmd   = document.getElementById("nlCommand").value.trim();
  const model = document.getElementById("model-select")?.value || "gemini";
  if (cmd) executeTask(cmd, model);
});

const stopBtn = document.getElementById("stop-button");
if (stopBtn) {
  stopBtn.addEventListener("click", () => { stopRequested = true; });
}

// ★★★ 追加/変更: Pause / Resume ボタン -------------------------
const pauseBtn  = document.getElementById("pause-button");
const resumeBtn = document.getElementById("resume-button");

if (pauseBtn) {
  pauseBtn.addEventListener("click", () => {
    if (pausedRequested) return;
    pausedRequested = true;
    pauseBtn.style.display  = "none";
    if (resumeBtn) resumeBtn.style.display = "inline-block";
  });
}
if (resumeBtn) {
  resumeBtn.addEventListener("click", () => {
    if (!pausedRequested) return;
    pausedRequested = false;
    resumeBtn.style.display = "none";
    if (pauseBtn) pauseBtn.style.display  = "inline-block";
    if (typeof resumeResolver === "function") {
      resumeResolver();     // 待機している executeTask を再開
      resumeResolver = null;
    }
  });
}
// ★★★ ここまで --------------------------------------------------

window.executeTask = executeTask;
