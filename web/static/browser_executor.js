// browser_executor.js

/* ======================================
   Utility
   ====================================== */
let stopController = null;
const sleep = ms => new Promise(resolve => {
  const id = setTimeout(resolve, ms);
  stopController?.signal.addEventListener("abort", () => {
    clearTimeout(id);
    resolve();
  }, { once: true });
});
const chatArea   = document.getElementById("chat-area");
let stopRequested   = false;
window.stopRequested = false;  // Make it globally accessible
// Default to a blank page to avoid unexpected navigation
const START_URL = window.START_URL || "about:blank";

// screenshot helper
async function captureScreenshot() {
  //const iframe = document.getElementById("vnc_frame");
  //if (!iframe) return null;
  try {
    //const canvas = await html2canvas(iframe, {useCORS: true});
    //return canvas.toDataURL("image/png");
  
      // バックエンドの Playwright API を直接呼び出してスクリーンショットを取得
    const response = await fetch("/screenshot", { signal: stopController?.signal });
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

// Queue system for additional prompts during execution
let promptQueue = [];
let isExecutingTask = false;

/* ======================================
   Normalize DSL actions
   ====================================== */
function formatIndexValue(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "boolean") return "";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return "";
    if (Number.isInteger(value) && value >= 0) {
      return `index=${value}`;
    }
    return "";
  }

  let text;
  try {
    text = String(value).trim();
  } catch (err) {
    return "";
  }

  if (!text) return "";
  if (!/^[-+]?\d+$/.test(text)) return "";

  const idx = Number.parseInt(text, 10);
  if (Number.isNaN(idx) || idx < 0) return "";
  return `index=${idx}`;
}

function escapeQuotes(value) {
  return String(value).replace(/"/g, '\\"');
}

function stringifySelector(selector) {
  if (selector === null || selector === undefined) {
    return "";
  }

  if (typeof selector === "string") {
    return selector;
  }

  if (Array.isArray(selector)) {
    const parts = [];
    selector.forEach(item => {
      const formatted = stringifySelector(item);
      if (formatted && !parts.includes(formatted)) {
        parts.push(formatted);
      }
    });
    return parts.join(" || ");
  }

  const indexForm = formatIndexValue(selector);
  if (indexForm) {
    return indexForm;
  }

  if (typeof selector === "object") {
    if (selector.selector) {
      const candidate = stringifySelector(selector.selector);
      if (candidate) return candidate;
    }

    if ("index" in selector) {
      const candidate = formatIndexValue(selector.index);
      if (candidate) return candidate;
    }

    if (selector.css) {
      return `css=${selector.css}`;
    }

    if (selector.xpath) {
      return `xpath=${selector.xpath}`;
    }

    if (selector.role) {
      const roleValue = String(selector.role).trim();
      if (roleValue) {
        const nameValue = selector.name ?? selector.text;
        if (nameValue !== undefined && nameValue !== null) {
          const nameText = String(nameValue).trim();
          if (nameText) {
            const escapedName = escapeQuotes(nameText);
            return `role=${roleValue}[name="${escapedName}"]`;
          }
        }
        return `role=${roleValue}`;
      }
    }

    if (selector.text !== undefined && selector.text !== null) {
      return String(selector.text);
    }

    const ariaLabel = selector.aria_label ?? selector["aria-label"];
    if (ariaLabel !== undefined && ariaLabel !== null) {
      const escaped = escapeQuotes(String(ariaLabel).trim());
      if (escaped) {
        return `css=[aria-label="${escaped}"]`;
      }
    }

    const stableId = selector.stable_id ?? selector.stableId;
    if (stableId !== undefined && stableId !== null) {
      const stable = String(stableId).trim();
      if (stable) {
        const escaped = escapeQuotes(stable);
        const candidates = [`css=[data-testid="${escaped}"]`];
        if (/^[A-Za-z_][-A-Za-z0-9_]*$/.test(stable)) {
          candidates.push(`css=#${stable}`);
        }
        candidates.push(`css=[name="${escaped}"]`);
        return candidates.join(" || ");
      }
    }

    for (const key of ["value", "target"]) {
      if (key in selector && selector[key] !== undefined && selector[key] !== null) {
        const candidate = stringifySelector(selector[key]);
        if (candidate) return candidate;
      }
    }

    for (const value of Object.values(selector)) {
      if (typeof value === "string") {
        const trimmed = value.trim();
        if (trimmed) return trimmed;
      }
      if (typeof value === "number" && Number.isFinite(value)) {
        return String(value);
      }
    }
  }

  try {
    return String(selector);
  } catch (err) {
    return "";
  }
}

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
    if ("target" in a) {
      a.target = stringifySelector(a.target);
    }
    if ("value" in a) {
      if (typeof a.value === "string") {
        // keep as-is
      } else if (Array.isArray(a.value) || (a.value && typeof a.value === "object")) {
        a.value = stringifySelector(a.value);
      } else if (a.value !== undefined && a.value !== null) {
        a.value = String(a.value);
      } else {
        a.value = "";
      }
    }
    if ("text" in a && typeof a.text !== "string") a.text = stringifySelector(a.text);
    return a;
  });
}

/* ======================================
   Health check and retry utilities
   ====================================== */
async function checkServerHealth() {
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 5000);
    
    const response = await fetch("/automation/healthz", {
      method: "GET",
      signal: controller.signal
    });
    
    clearTimeout(timeoutId);
    return response.ok;
  } catch (e) {
    if (e.name === 'AbortError') {
      console.warn("Health check timed out");
    } else {
      console.warn("Health check failed:", e);
    }
    return false;
  }
}

function logPollingDiagnostics(taskId, httpErrors, networkErrors, totalAttempts, duration) {
  const diagnostics = {
    taskId,
    httpErrors,
    networkErrors,
    totalAttempts,
    duration,
    timestamp: new Date().toISOString()
  };
  
  console.warn("Polling failed diagnostics:", diagnostics);
  
  // Store diagnostics for potential debugging
  if (typeof window !== 'undefined') {
    if (!window.pollingDiagnostics) {
      window.pollingDiagnostics = [];
    }
    window.pollingDiagnostics.push(diagnostics);
    
    // Keep only last 10 diagnostics to prevent memory leak
    if (window.pollingDiagnostics.length > 10) {
      window.pollingDiagnostics = window.pollingDiagnostics.slice(-10);
    }
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
        body: JSON.stringify({ actions: acts }),
        signal: stopController?.signal,
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
    const raw = stringifySelector(a?.text ?? a?.target ?? "");
    const lowered = raw.toLowerCase();
    return /購入|削除|checkout|pay|支払/.test(lowered);
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
   Handle stop requests and user intervention
   ====================================== */
async function checkForStopRequest() {
  try {
    const response = await fetch("/automation/stop-request", { signal: stopController?.signal });
    if (response.ok) {
      const stopRequest = await response.json();
      return stopRequest;
    }
  } catch (e) {
    console.warn("Failed to check for stop request:", e);
  }
  return null;
}

async function handleUserIntervention(stopRequest) {
  return new Promise((resolve) => {
    // Create intervention UI
    const interventionDiv = document.createElement("div");
    interventionDiv.classList.add("user-intervention");
    interventionDiv.style.cssText = `
      background: #fff3cd;
      border: 1px solid #ffeaa7;
      border-radius: 8px;
      padding: 16px;
      margin: 16px 0;
      box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    `;

    const title = document.createElement("h4");
    title.textContent = "⏸️ 実行が一時停止されました";
    title.style.cssText = "margin: 0 0 12px 0; color: #856404;";
    
    const reasonText = document.createElement("p");
    reasonText.textContent = `理由: ${stopRequest.reason}`;
    reasonText.style.cssText = "margin: 8px 0; font-weight: bold; color: #856404;";
    
    const messageText = document.createElement("p");
    if (stopRequest.message) {
      messageText.textContent = `メッセージ: ${stopRequest.message}`;
      messageText.style.cssText = "margin: 8px 0; color: #856404;";
    }
    
    const inputLabel = document.createElement("label");
    inputLabel.textContent = "指示やアドバイスを入力してください:";
    inputLabel.style.cssText = "display: block; margin: 16px 0 8px 0; font-weight: bold; color: #856404;";
    
    const textArea = document.createElement("textarea");
    textArea.style.cssText = `
      width: 100%;
      height: 80px;
      padding: 8px;
      border: 1px solid #ffeaa7;
      border-radius: 4px;
      font-family: inherit;
      resize: vertical;
    `;
    textArea.placeholder = "例: CAPTCHAを解決しました、「続行」をクリックしてください";
    
    const buttonContainer = document.createElement("div");
    buttonContainer.style.cssText = "margin-top: 16px; text-align: right;";
    
    const resumeButton = document.createElement("button");
    resumeButton.textContent = "続行";
    resumeButton.style.cssText = `
      background: #28a745;
      color: white;
      border: none;
      padding: 8px 16px;
      border-radius: 4px;
      cursor: pointer;
      margin-left: 8px;
    `;
    
    const cancelButton = document.createElement("button");
    cancelButton.textContent = "キャンセル";
    cancelButton.style.cssText = `
      background: #6c757d;
      color: white;
      border: none;
      padding: 8px 16px;
      border-radius: 4px;
      cursor: pointer;
    `;
    
    // Event handlers
    resumeButton.onclick = async () => {
      const userResponse = textArea.value.trim();
      
      try {
        // Send user response to backend
        const response = await fetch("/automation/stop-response", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ response: userResponse }),
          signal: stopController?.signal,
        });
        
        if (response.ok) {
          // Remove intervention UI
          interventionDiv.remove();
          
          // Add user response to chat if provided
          if (userResponse) {
            const userMsg = document.createElement("p");
            userMsg.classList.add("user-message");
            userMsg.innerHTML = `<strong>👤 ユーザー介入:</strong> ${userResponse}`;
            userMsg.style.cssText = "background: #e8f5e8; padding: 8px; border-radius: 4px; margin: 8px 0;";
            chatArea.appendChild(userMsg);
            chatArea.scrollTop = chatArea.scrollHeight;
          }
          
          resolve(userResponse);
        } else {
          throw new Error("Failed to send user response");
        }
      } catch (e) {
        console.error("Error sending user response:", e);
        alert("応答の送信に失敗しました。もう一度お試しください。");
      }
    };
    
    cancelButton.onclick = () => {
      interventionDiv.remove();
      resolve(null); // Cancel intervention
    };
    
    // Build UI
    interventionDiv.appendChild(title);
    interventionDiv.appendChild(reasonText);
    if (stopRequest.message) {
      interventionDiv.appendChild(messageText);
    }
    interventionDiv.appendChild(inputLabel);
    interventionDiv.appendChild(textArea);
    buttonContainer.appendChild(cancelButton);
    buttonContainer.appendChild(resumeButton);
    interventionDiv.appendChild(buttonContainer);
    
    // Add to chat area
    chatArea.appendChild(interventionDiv);
    chatArea.scrollTop = chatArea.scrollHeight;
    
    // Focus on text area
    textArea.focus();
  });
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
      body: JSON.stringify({ warnings: warnings }),
      signal: stopController?.signal,
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
   Store user intervention in conversation history
   ====================================== */
async function storeUserIntervention(userResponse) {
  try {
    // Add the user intervention as a new conversation entry
    const response = await fetch("/execute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        command: `[ユーザー介入] ${userResponse}`,
        pageSource: null,
        screenshot: null,
        model: "intervention"
      }),
      signal: stopController?.signal,
    });
    
    if (response.ok) {
      console.log("User intervention stored in conversation history");
    } else {
      console.warn("Failed to store user intervention:", response.status);
    }
  } catch (e) {
    console.warn("Error storing user intervention:", e);
  }
}

/* ======================================
   Execute one turn
   ====================================== */
async function runTurn(cmd, pageHtml, screenshot, showInUI = true, model = "gemini", placeholder = null, prevError = null) {
  let html = pageHtml;
  if (!html) {
    html = await fetch("/vnc-source", { signal: stopController?.signal })
      .then(r => (r.ok ? r.text() : ""))
      .catch(() => "");
  }
  if (!screenshot) {
    screenshot = await captureScreenshot();
  }

  // Always show thinking message with spinner when showInUI is true
  let thinkingElement = placeholder;
  if (showInUI && !placeholder) {
    thinkingElement = document.createElement("p");
    thinkingElement.classList.add("bot-message");
    thinkingElement.innerHTML = 'AI が応答中... <span class="spinner" style="display:inline-block;width:12px;height:12px;border:2px solid #f3f3f3;border-top:2px solid #3498db;border-radius:50%;animation:spin 1s linear infinite;"></span>';
    chatArea.appendChild(thinkingElement);
    chatArea.scrollTop = chatArea.scrollHeight;
  }

  // Send command to LLM and get immediate response
  const res = await sendCommand(cmd, html, screenshot, model, prevError);

  if (res.raw) console.log("LLM raw output:\n", res.raw);

  // Update UI immediately with LLM response
  if (showInUI && res.explanation && thinkingElement) {
    thinkingElement.textContent = res.explanation;
    thinkingElement.querySelector(".spinner")?.remove();
  }

  let newHtml = html;
  let newShot = screenshot;
  let errInfo = null;

  // Check if we have async execution
  if (res.async_execution && res.task_id) {
    console.log("Async execution started, task ID:", res.task_id);
    
    // Show execution status immediately (no delay)
    const statusElement = document.createElement("p");
    statusElement.classList.add("system-message");
    statusElement.textContent = "🔄 ブラウザ操作を実行中...";
    statusElement.style.color = "#007bff";
    chatArea.appendChild(statusElement);
    chatArea.scrollTop = chatArea.scrollHeight;
    
    // Start polling immediately with optimized timing
    let pollingStartTime = Date.now();
    const updateInterval = setInterval(() => {
      const elapsed = Math.floor((Date.now() - pollingStartTime) / 1000);
      statusElement.textContent = `🔄 ブラウザ操作を実行中... (${elapsed}秒)`;
    }, 1000);

    // Poll for execution completion with improved timing and tolerance
    const executionResult = await pollExecutionStatus(res.task_id, 40, 300); // Increased attempts, reduced initial interval
    
    // Clear the update interval
    clearInterval(updateInterval);
    
    if (executionResult) {
      // Update status message
      if (executionResult.status === "completed") {
        statusElement.textContent = "✅ ブラウザ操作が完了しました";
        statusElement.style.color = "#28a745";
        
        // Get execution results
        if (executionResult.result) {
          newHtml = executionResult.result.html || newHtml;
          errInfo = executionResult.result.error || null;
          
          // Display warnings if any
          if (executionResult.result.warnings && executionResult.result.warnings.length > 0) {
            displayWarnings(executionResult.result.warnings, executionResult.result.correlation_id);
            await storeWarningsInHistory(executionResult.result.warnings);
            
            // Check for stop request in warnings
            const stopWarnings = executionResult.result.warnings.filter(w => w.startsWith("STOP:auto:"));
            if (stopWarnings.length > 0) {
              // Check for actual stop request from automation server
              const stopRequest = await checkForStopRequest();
              if (stopRequest) {
                statusElement.textContent = "⏸️ 実行が一時停止されました";
                statusElement.style.color = "#ffc107";
                
                // Handle user intervention
                const userResponse = await handleUserIntervention(stopRequest);
                if (userResponse !== null) {
                  // Update conversation history with user intervention
                  await storeUserIntervention(userResponse);
                  statusElement.textContent = "▶️ ユーザー介入後、実行を再開";
                  statusElement.style.color = "#17a2b8";
                } else {
                  statusElement.textContent = "⏹️ ユーザーによって実行がキャンセルされました";
                  statusElement.style.color = "#6c757d";
                }
              }
            }
          }
          
          // Get updated HTML from parallel fetch
          if (executionResult.result.updated_html) {
            newHtml = executionResult.result.updated_html;
          }
        }
      } else if (executionResult.status === "failed") {
        statusElement.textContent = "❌ ブラウザ操作に失敗しました";
        statusElement.style.color = "#dc3545";
        errInfo = executionResult.error || "Unknown execution error";
      }
    } else {
      // Polling failed - attempt silent fallback without displaying confusing messages
      console.warn("Execution status polling failed for task:", res.task_id);
      
      // Try to fall back to synchronous execution silently if we have actions
      if (res.actions && res.actions.length > 0) {
        console.log("Attempting silent fallback to synchronous execution after polling failure");
        statusElement.textContent = "🔄 ブラウザ操作を実行中...";
        statusElement.style.color = "#007bff";
        
        const acts = normalizeActions(res);
        if (acts && acts.length > 0) {
          try {
            const ret = await sendDSL(acts);
            if (ret) {
              newHtml = ret.html || newHtml;
              errInfo = ret.error || null;
              statusElement.textContent = "✅ ブラウザ操作が完了しました";
              statusElement.style.color = "#28a745";
            } else {
              statusElement.textContent = "❌ ブラウザ操作に失敗しました";
              statusElement.style.color = "#dc3545";
            }
          } catch (fallbackError) {
            console.error("Fallback execution failed:", fallbackError);
            statusElement.textContent = "❌ ブラウザ操作に失敗しました";
            statusElement.style.color = "#dc3545";
            errInfo = `Execution error: ${fallbackError.message}`;
          }
        }
      } else {
        statusElement.textContent = "⚠️ 実行に失敗しました";
        statusElement.style.color = "#ffc107";
      }
    }
    
    // Get fresh screenshot after execution
    newShot = await captureScreenshot();
    
  } else if (res.actions && res.actions.length > 0) {
    // Fallback to synchronous execution if async is not available
    console.log("Falling back to synchronous execution");
    const acts = normalizeActions(res);
    if (acts && acts.length > 0) {
      const ret = await sendDSL(acts);
      if (ret) {
        newHtml = ret.html || newHtml;
        errInfo = ret.error || null;
      }
    }
    newShot = await captureScreenshot();
  }

  return { 
    cont: res.complete === false, 
    explanation: res.explanation || "", 
    memory: res.memory || "", 
    html: newHtml, 
    screenshot: newShot, 
    error: errInfo,
    actions: normalizeActions(res) // Include normalized actions for loop detection
  };
}

/* ======================================
   Poll execution status
   ====================================== */
async function pollExecutionStatus(taskId, maxAttempts = 40, initialInterval = 300) {
  const startTime = Date.now();
  const maxDuration = maxAttempts * initialInterval * 3; // More generous timeout
  let httpErrorCount = 0;
  let networkErrorCount = 0;
  const maxHttpErrors = 12;    // Further increased tolerance
  const maxNetworkErrors = 15; // Further increased tolerance
  
  console.log(`Starting to poll task ${taskId} (max attempts: ${maxAttempts})`);
  
  // Optional health check before starting polling
  try {
    const healthOk = await checkServerHealth();
    if (!healthOk) {
      console.warn("Server health check failed, but continuing with polling...");
    }
  } catch (e) {
    console.warn("Health check error, but continuing:", e);
  }
  
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    try {
      if (stopRequested || stopController?.signal.aborted) return null;
      const response = await fetch(`/execution-status/${taskId}`, { signal: stopController?.signal });
      
      if (!response.ok) {
        httpErrorCount++;
        console.warn(`HTTP error ${response.status} for task ${taskId} (attempt ${attempt + 1}, http errors: ${httpErrorCount})`);
        
        // For server errors (5xx), be more patient with increased backoff
        if (response.status >= 500 && httpErrorCount < maxHttpErrors) {
          const backoffDelay = Math.min(initialInterval * Math.pow(1.8, httpErrorCount), 8000);
          console.log(`Server error detected, waiting ${backoffDelay}ms before retry...`);
          await sleep(backoffDelay);
          continue;
        }
        
        // For client errors (4xx), give more chances but with delays
        if (response.status >= 400 && response.status < 500 && httpErrorCount < 5) {
          await sleep(initialInterval * 1.5);
          continue;
        }
        
        // Too many HTTP errors
        if (httpErrorCount >= maxHttpErrors) {
          console.error(`Too many HTTP errors (${httpErrorCount}), giving up on task ${taskId}`);
          return null;
        }
        
        await sleep(initialInterval);
        continue;
      }
      
      // Reset error count on successful response
      httpErrorCount = 0;
      
      const status = await response.json();
      if (stopRequested || stopController?.signal.aborted) return null;
      console.log(`Task ${taskId} status: ${status.status} (attempt ${attempt + 1})`);
      
      if (status.status === "completed" || status.status === "failed") {
        const duration = Date.now() - startTime;
        console.log(`Task ${taskId} completed after ${duration}ms`);
        return status;
      }
      
      // Check if we've exceeded the maximum duration
      if (Date.now() - startTime > maxDuration) {
        console.warn(`Polling timeout for task ${taskId} - exceeded ${maxDuration}ms`);
        break;
      }
      
      // Exponential backoff for polling interval, but cap it
      const currentInterval = Math.min(initialInterval * Math.pow(1.2, Math.floor(attempt / 5)), 3000);
      await sleep(currentInterval);
      
    } catch (e) {
      if (stopRequested || stopController?.signal.aborted) return null;
      networkErrorCount++;
      console.error(`Network error polling task ${taskId} (attempt ${attempt + 1}, network errors: ${networkErrorCount}):`, e);

      // Check if this is a retryable network error - be more patient
      if (networkErrorCount < maxNetworkErrors &&
          (e.name === 'TypeError' || e.message.includes('Failed to fetch') || e.message.includes('NetworkError'))) {

        // Exponential backoff for network errors with more generous delays
        const backoffDelay = Math.min(initialInterval * Math.pow(2.2, networkErrorCount), 12000);
        console.log(`Network error detected, waiting ${backoffDelay}ms before retry...`);
        await sleep(backoffDelay);
        continue;
      }

      // Too many network errors or non-retryable error
      if (networkErrorCount >= maxNetworkErrors) {
        console.error(`Too many network errors (${networkErrorCount}), giving up on task ${taskId}`);
        return null;
      }

      await sleep(initialInterval);
    }
  }
  
  console.warn(`Polling timeout for task ${taskId} after ${maxAttempts} attempts (HTTP errors: ${httpErrorCount}, Network errors: ${networkErrorCount})`);
  
  // Log diagnostics for debugging
  const duration = Date.now() - startTime;
  logPollingDiagnostics(taskId, httpErrorCount, networkErrorCount, maxAttempts, duration);
  
  return null;
}

/* ======================================
   Multi-turn executor
   ====================================== */
async function executeTask(cmd, model = "gemini", placeholder = null) {
  stopController = new AbortController();
  window.stopController = stopController;

  const MAX_STEPS = typeof window.MAX_STEPS === "number" ? window.MAX_STEPS : 10;
  let stepCount = 0;
  let keepLoop  = true;
  let firstIter = true;
  let pageHtml  = await fetch("/vnc-source", { signal: stopController.signal })
    .then(r => (r.ok ? r.text() : ""))
    .catch(() => "");
  let screenshot = null;
  let lastMsg   = "";
  let repeatCnt = 0;
  const MAX_REP = 1;
  let lastError = null;
  
  // Enhanced loop detection: track actions, not just explanations
  let actionHistory = [];
  const MAX_ACTION_HISTORY = 5; // Keep track of last 5 actions
  let identicalActionCount = 0;
  const MAX_IDENTICAL_ACTIONS = 2; // Allow max 2 identical actions before stopping
  
  stopRequested   = false;
  window.stopRequested = false;  // Reset both local and global
  pausedRequested = false;  // 毎タスク開始時にリセット
  
  // Set execution state
  isExecutingTask = true;
  promptQueue = []; // Clear any existing queued prompts

  while (keepLoop && stepCount < MAX_STEPS) {
    if (stopRequested || window.stopRequested) break;

    // Check for queued prompts and process them
    if (promptQueue.length > 0) {
      const queuedPrompt = promptQueue.shift();

      // Process the queued prompt by updating the current command
      cmd = queuedPrompt;
      
      // Reset loop detection counters since we have new instructions
      actionHistory = [];
      identicalActionCount = 0;
      repeatCnt = 0;
      lastMsg = "";
    }
   
    if (pausedRequested) {
      showSystemMessage("⏸ タスクを一時停止中。ブラウザを手動操作できます。");
      await new Promise(res => { resumeResolver = res; });  // Resume を待つ
      if (stopRequested || window.stopRequested) break;   // 再開前に停止された場合
      showSystemMessage("▶ タスクを再開します。");
    }

    try {
      const { cont, explanation, memory, html, screenshot: shot, error, actions } = await runTurn(cmd, pageHtml, screenshot, true, model, firstIter ? placeholder : null, lastError);
      if (shot) screenshot = shot;
      if (html) pageHtml = html;
      lastError = error;

      // Enhanced loop detection: check for identical actions
      if (actions && actions.length > 0) {
        // Create a signature for the actions to detect duplicates
        const actionSignature = actions.map(a => `${a.action}:${a.target}:${a.value || ''}`).join('|');
        
        // Check if this exact sequence of actions was recently executed
        const isIdenticalAction = actionHistory.some(histAction => histAction === actionSignature);
        
        if (isIdenticalAction) {
          identicalActionCount += 1;
          console.warn(`Detected identical action sequence (${identicalActionCount}/${MAX_IDENTICAL_ACTIONS}): ${actionSignature}`);
          
          if (identicalActionCount >= MAX_IDENTICAL_ACTIONS) {
            console.warn("同一アクションが繰り返されたためループを終了します。");
            showSystemMessage("⚠️ 同じ操作の繰り返しを検出したため、タスクを終了します。");
            break;
          }
        } else {
          identicalActionCount = 0; // Reset count if actions are different
        }
        
        // Add to action history
        actionHistory.push(actionSignature);
        if (actionHistory.length > MAX_ACTION_HISTORY) {
          actionHistory.shift(); // Keep only the most recent actions
        }
      }

      // Original explanation-based loop detection (kept as secondary check)
      if (explanation === lastMsg) {
        repeatCnt += 1;
        if (repeatCnt > MAX_REP) {
          console.warn("同一説明が繰り返されたためループを終了します。");
          showSystemMessage("⚠️ 同じ説明の繰り返しを検出したため、タスクを終了します。");
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

  // Clear execution state
  isExecutingTask = false;

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
    stopController?.abort();
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

// Global functions for prompt queue management
window.addPromptToQueue = function(prompt) {
  if (isExecutingTask) {
    promptQueue.push(prompt);
    return true;
  }
  return false;
};

window.isTaskExecuting = function() {
  return isExecutingTask;
};

window.getQueuedPromptCount = function() {
  return promptQueue.length;
};
