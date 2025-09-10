// browser_executor.js

/* ======================================
   Utility
   ====================================== */
const sleep = ms => new Promise(r => setTimeout(r, ms));
const chatArea   = document.getElementById("chat-area");
let stopRequested   = false;
window.stopRequested = false;  // Make it globally accessible
const START_URL = window.START_URL || "https://www.yahoo.co.jp";

// screenshot helper
async function captureScreenshot() {
  //const iframe = document.getElementById("vnc_frame");
  //if (!iframe) return null;
  try {
    //const canvas = await html2canvas(iframe, {useCORS: true});
    //return canvas.toDataURL("image/png");
  
      // バックエンドの Playwright API を直接呼び出してスクリーンショットを取得
    const response = await fetch("/screenshot");
    if (!response.ok) {
        console.error("screenshot fetch failed:", response.status, await response.text());
        return null;
    }
    return await response.text(); // base64エンコードされたデータURIを返す

  } catch (e) {
    console.error("screenshot error:", e);
    return null;
  }
}


let pausedRequested = false;   // 一時停止フラグ
let resumeResolver  = null;    // 再開時に resolve するコールバック

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
let isExecutingDSL = false;  // 実行中フラグ

async function sendDSL(acts) {
  if (!acts.length) return { html: "", error: null };
  
  // 二重送信防止
  if (isExecutingDSL) {
    showSystemMessage("⚠ 操作実行中です。しばらくお待ちください。");
    return { html: "", error: "execution in progress" };
  }
  
  if (requiresApproval(acts)) {
    if (!confirm("重要な操作を実行しようとしています。続行しますか?")) {
      showSystemMessage("ユーザーが操作を拒否しました");
      return { html: "", error: "user rejected" };
    }
  }
  
  isExecutingDSL = true;  // 実行開始
  showSystemMessage("🔄 操作を実行中...");
  
  try {
    const r = await fetch("/automation/execute-dsl", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actions: acts })
    });
    
    // HTTP 200 で warnings 方式への対応（500 エラーは発生しないはず）
    if (r.ok) {
      appendHistory(acts);
      const j = await r.json();
      let err = null;
      
      if (j.warnings && j.warnings.length) {
        // warnings を用途別に分類して表示
        const errors = j.warnings.filter(w => w.startsWith("ERROR:"));
        const warnings = j.warnings.filter(w => w.startsWith("WARNING:"));
        
        if (errors.length) {
          // エラーはユーザー向けメッセージに変換
          const userFriendlyErrors = errors.map(e => convertToUserFriendlyMessage(e));
          err = userFriendlyErrors.join("\n");
          showSystemMessage(`❌ 操作エラー: ${userFriendlyErrors.join("; ")}`);
        }
        
        if (warnings.length) {
          // 警告は詳細表示
          const userFriendlyWarnings = warnings.map(w => convertToUserFriendlyMessage(w));
          showSystemMessage(`⚠ 操作上の注意: ${userFriendlyWarnings.join("; ")}`);
          if (!err) err = userFriendlyWarnings.join("\n");
        }
      } else {
        showSystemMessage("✅ 操作が正常に完了しました");
      }
      
      return { html: j.html || "", error: err };
    } else {
      // 旧来の 400/500 エラーハンドリング（後方互換性のため残す）
      let msg = "";
      try {
        const j = await r.json();
        msg = j.message || j.error || "";
      } catch (e) {
        msg = await r.text();
      }
      console.error("execute-dsl failed:", r.status, msg);
      showSystemMessage(`❌ 通信エラー: ${convertToUserFriendlyMessage(msg) || r.status}`);
      return { html: "", error: msg || `status ${r.status}` };
    }
  } catch (e) {
    console.error("execute-dsl fetch error:", e);
    showSystemMessage(`❌ 通信エラー: ${e}`);
    return { html: "", error: String(e) };
  } finally {
    isExecutingDSL = false;  // 実行終了
  }
}

// 技術的なエラーメッセージをユーザー向けに変換
function convertToUserFriendlyMessage(message) {
  if (!message) return message;
  
  // ERROR: や WARNING: プレフィクスを除去
  let msg = message.replace(/^(ERROR|WARNING):[^:]*:\s*/, "");
  
  // 技術的文言をユーザー向けに変換
  const conversions = {
    "Timeout": "応答時間切れ",
    "locator not found": "要素が見つかりませんでした",
    "element not enabled": "要素が操作できない状態です",
    "element not found": "要素が見つかりませんでした",
    "Navigation failed": "ページの移動に失敗しました",
    "invalid or empty URL": "URLが無効または空です",
    "selector.*not found": "指定された要素が見つかりませんでした",
    "Click failed": "クリック操作が失敗しました",
    "Fill failed": "テキスト入力が失敗しました",
    "Network error": "ネットワークエラーが発生しました",
    "Server execution failed": "サーバー処理でエラーが発生しました",
    "Large text input": "大きなテキストの入力は時間がかかる場合があります",
    "Large DSL": "多数の操作が含まれているため分割実行されました"
  };
  
  for (const [pattern, replacement] of Object.entries(conversions)) {
    msg = msg.replace(new RegExp(pattern, 'gi'), replacement);
  }
  
  return msg;
}

function requiresApproval(acts) {
  return acts.some(a => {
    const t = (a.text || a.target || "").toLowerCase();
    return /購入|削除|checkout|pay|支払/.test(t);
  });
}

function appendHistory(acts) {
  // Operation history display removed - this function is now a no-op
  return;
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
async function runTurn(cmd, pageHtml, screenshot, showInUI = true, model = "gemini", placeholder = null, prevError = null) {
  let html = pageHtml;
  if (!html) {
    html = await fetch("/vnc-source")
      .then(r => (r.ok ? r.text() : ""))
      .catch(() => "");
  }
  if (!screenshot) {
    screenshot = await captureScreenshot();
  }

  const res = await sendCommand(cmd, html, screenshot, model, prevError);

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
  let newShot = screenshot;
  let errInfo = null;
  if (acts.length) {
    const ret = await sendDSL(acts);
    if (ret) {
      newHtml = ret.html || newHtml;
      errInfo = ret.error || null;
    }
    newShot = await captureScreenshot();
  }

  return { cont: res.complete === false && acts.length > 0, explanation: res.explanation || "", html: newHtml, screenshot: newShot, error: errInfo };
}

/* ======================================
   Multi-turn executor
   ====================================== */
async function executeTask(cmd, model = "gemini", placeholder = null) {
  const MAX_STEPS = typeof window.MAX_STEPS === "number" ? window.MAX_STEPS : 10;
  let stepCount = 0;
  let keepLoop  = true;
  let firstIter = true;
  let pageHtml  = await fetch("/vnc-source")
    .then(r => (r.ok ? r.text() : ""))
    .catch(() => "");
  let screenshot = null;
  let lastMsg   = "";
  let repeatCnt = 0;
  const MAX_REP = 1;
  let lastError = null;
  stopRequested   = false;
  window.stopRequested = false;  // Reset both local and global
  pausedRequested = false;  // 毎タスク開始時にリセット

  while (keepLoop && stepCount < MAX_STEPS) {
    if (stopRequested || window.stopRequested) break;

   
    if (pausedRequested) {
      showSystemMessage("⏸ タスクを一時停止中。ブラウザを手動操作できます。");
      await new Promise(res => { resumeResolver = res; });  // Resume を待つ
      if (stopRequested || window.stopRequested) break;   // 再開前に停止された場合
      showSystemMessage("▶ タスクを再開します。");
    }

    try {
      const { cont, explanation, html, screenshot: shot, error } = await runTurn(cmd, pageHtml, screenshot, true, model, firstIter ? placeholder : null, lastError);
      if (shot) screenshot = shot;
      if (html) pageHtml = html;
      lastError = error;

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
      if (keepLoop) await sleep(200);
    } catch (e) {
      console.error("runTurn error:", e);
      await sleep(200);
    }
    stepCount += 1;
  }

  const done = document.createElement("p");
  done.classList.add("system-message");
  if (stopRequested || window.stopRequested) {
    done.textContent = "⏹ タスクを中断しました";
  } else if (stepCount >= MAX_STEPS && keepLoop) {
    done.textContent = `⏹ ステップ上限(${MAX_STEPS})に達したため終了しました`;
  } else {
    done.textContent = "✅ タスクを終了しました";
  }
  chatArea.appendChild(done);
  chatArea.scrollTop = chatArea.scrollHeight;
}

/* ======================================
   Debug buttons & UI wiring
   ====================================== */
document.getElementById("executeButton")?.addEventListener("click", () => {
  const cmd   = document.getElementById("nlCommand").value.trim();
  const model = "gemini";  // デフォルトモデルを使用
  if (cmd) executeTask(cmd, model);
});

const stopBtn = document.getElementById("stop-button");
if (stopBtn) {
  stopBtn.addEventListener("click", () => { 
    stopRequested = true; 
    window.stopRequested = true; 
  });
}




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


window.executeTask = executeTask;
