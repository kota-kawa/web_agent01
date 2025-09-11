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
   Health check and retry utilities
   ====================================== */
async function checkServerHealth() {
  try {
    const response = await fetch("/automation/healthz", {
      method: "GET",
      timeout: 5000
    });
    return response.ok;
  } catch (e) {
    console.warn("Health check failed:", e);
    return false;
  }
}

/* ======================================
   Send DSL to Playwright server with retry logic
   ====================================== */
async function sendDSL(acts) {
  if (!acts.length) return { html: "", error: null, warnings: [] };
  
  if (requiresApproval(acts)) {
    if (!confirm("重要な操作を実行しようとしています。続行しますか?")) {
      showSystemMessage("ユーザーが操作を拒否しました");
      return { html: "", error: "user rejected", warnings: [] };
    }
  }
  
  const maxRetries = 2; // Allow 1 retry attempt
  let lastError = null;
  
  for (let attempt = 1; attempt <= maxRetries; attempt++) {
    // Check server health before critical operations (on retries)
    if (attempt > 1) {
      console.log(`DSL retry attempt ${attempt}/${maxRetries}, checking server health...`);
      const isHealthy = await checkServerHealth();
      if (!isHealthy) {
        console.warn("Server health check failed, proceeding with caution...");
        showSystemMessage("サーバーの状態を確認中です...");
        await sleep(2000); // Wait 2 seconds for server recovery
      }
    }
    
    try {
      const r = await fetch("/automation/execute-dsl", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ actions: acts })
      });
      
      const responseData = await r.json();
      
      if (!r.ok) {
        // Handle error responses that now come as 200 + warnings
        const errorMsg = responseData.message || responseData.error || "Unknown error";
        console.error("execute-dsl failed:", r.status, errorMsg);
        
        // Check if this is a retryable error (500 errors)
        if (r.status === 500 && attempt < maxRetries) {
          lastError = { status: r.status, message: errorMsg, data: responseData };
          console.log(`Server error (${r.status}), will retry after delay...`);
          showSystemMessage(`サーバーエラーが発生しました。${maxRetries - attempt}回再試行します...`);
          await sleep(1000 * attempt); // Exponential backoff: 1s, 2s
          continue;
        }
        
        showSystemMessage(`DSL 実行エラー: ${errorMsg}`);
        
        // Store warnings in conversation history even in error cases
        if (responseData.warnings && responseData.warnings.length > 0) {
          await storeWarningsInHistory(responseData.warnings);
        }
        
        return { 
          html: responseData.html || "", 
          error: errorMsg, 
          warnings: responseData.warnings || [],
          correlation_id: responseData.correlation_id 
        };
      } else {
        // Success - clear any retry messages
        if (attempt > 1) {
          showSystemMessage("再試行が成功しました");
        }
        
        appendHistory(acts);
        
        // Display warnings prominently if present
        if (responseData.warnings && responseData.warnings.length > 0) {
          displayWarnings(responseData.warnings, responseData.correlation_id);
          // Store warnings in conversation history
          await storeWarningsInHistory(responseData.warnings);
        }
        
        const errorText = responseData.warnings && responseData.warnings.length > 0 
          ? responseData.warnings.filter(w => w.startsWith("ERROR:")).join("\n") || null
          : null;
        
        return { 
          html: responseData.html || "", 
          error: errorText,
          warnings: responseData.warnings || [],
          correlation_id: responseData.correlation_id 
        };
      }
    } catch (e) {
      console.error("execute-dsl fetch error:", e);
      lastError = { type: "fetch", message: String(e) };
      
      // Check if this is a retryable network error
      if (attempt < maxRetries && (e.name === 'TypeError' || e.message.includes('Failed to fetch'))) {
        console.log(`Network error, will retry after delay: ${e.message}`);
        showSystemMessage(`通信エラーが発生しました。${maxRetries - attempt}回再試行します...`);
        await sleep(1000 * attempt); // Exponential backoff
        continue;
      }
      
      // Final failure or non-retryable error
      const errorMsg = `通信エラー: ${e.message || e}`;
      showSystemMessage(errorMsg);
      return { html: "", error: String(e), warnings: [] };
    }
  }
  
  // All retries exhausted
  if (lastError) {
    const errorMsg = lastError.status ? 
      `サーバーエラー (${lastError.status}): ${lastError.message}` : 
      `通信エラー: ${lastError.message}`;
    showSystemMessage(`${maxRetries}回の再試行後も失敗しました: ${errorMsg}`);
    return { 
      html: lastError.data?.html || "", 
      error: lastError.message, 
      warnings: lastError.data?.warnings || [] 
    };
  }
  
  return { html: "", error: "Unknown retry failure", warnings: [] };
}


function displayWarnings(warnings, correlationId) {
  if (!warnings || warnings.length === 0) return;
  
  const warningsContainer = document.createElement("div");
  warningsContainer.classList.add("warnings-container");
  warningsContainer.style.cssText = `
    margin: 10px 0;
    padding: 12px;
    background: linear-gradient(135deg, #fff3cd, #ffeaa7);
    border-left: 4px solid #ffc107;
    border-radius: 8px;
    font-family: 'Courier New', monospace;
    font-size: 12px;
    max-height: 200px;
    overflow-y: auto;
  `;
  
  const title = document.createElement("div");
  title.style.cssText = `
    font-weight: bold;
    color: #856404;
    margin-bottom: 8px;
    font-size: 13px;
  `;
  title.textContent = `⚠️ 実行時の注意・警告 ${correlationId ? `(ID: ${correlationId})` : ''}`;
  warningsContainer.appendChild(title);
  
  warnings.forEach(warning => {
    const warningItem = document.createElement("div");
    warningItem.style.cssText = `
      margin: 4px 0;
      padding: 4px 8px;
      background: rgba(255, 255, 255, 0.7);
      border-radius: 4px;
      line-height: 1.4;
    `;
    
    // Color code different warning types
    if (warning.startsWith("ERROR:")) {
      warningItem.style.color = "#dc3545";
      warningItem.style.borderLeft = "3px solid #dc3545";
    } else if (warning.startsWith("WARNING:")) {
      warningItem.style.color = "#fd7e14";  
      warningItem.style.borderLeft = "3px solid #fd7e14";
    } else if (warning.startsWith("DEBUG:")) {
      warningItem.style.color = "#6c757d";
      warningItem.style.borderLeft = "3px solid #6c757d";
    } else {
      warningItem.style.color = "#856404";
    }
    
    warningItem.textContent = warning;
    warningsContainer.appendChild(warningItem);
  });
  
  chatArea.appendChild(warningsContainer);
  chatArea.scrollTop = chatArea.scrollHeight;
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
   Store warnings in conversation history
   ====================================== */
async function storeWarningsInHistory(warnings) {
  if (!warnings || warnings.length === 0) {
    return;
  }
  
  try {
    const response = await fetch("/store-warnings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ warnings: warnings })
    });
    
    const result = await response.json();
    if (result.status === "success") {
      console.log("Warnings stored in conversation history:", warnings.length);
    } else {
      console.warn("Failed to store warnings:", result.message);
    }
  } catch (e) {
    console.error("Error storing warnings in history:", e);
  }
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

  if (res.raw) console.log("LLM raw output:\n", res.raw);

  // Extract actions immediately after getting LLM response
  const acts = normalizeActions(res);

  // Start Playwright execution immediately if there are actions
  let playwrightPromise = null;
  if (acts.length) {
    playwrightPromise = sendDSL(acts);
  }

  // Update UI in parallel with Playwright execution
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

  // Wait for Playwright execution to complete
  let newHtml = html;
  let newShot = screenshot;
  let errInfo = null;
  if (playwrightPromise) {
    const ret = await playwrightPromise;
    if (ret) {
      newHtml = ret.html || newHtml;
      errInfo = ret.error || null;
    }
    newShot = await captureScreenshot();
  }

  return { cont: res.complete === false && acts.length > 0, explanation: res.explanation || "", memory: res.memory || "", html: newHtml, screenshot: newShot, error: errInfo };
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
      const { cont, explanation, memory, html, screenshot: shot, error } = await runTurn(cmd, pageHtml, screenshot, true, model, firstIter ? placeholder : null, lastError);
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
