// browser_executor.js

/* ======================================
   Utility
   ====================================== */
const sleep = ms => new Promise(r => setTimeout(r, ms));
const chatArea   = document.getElementById("chat-area");
let stopRequested   = false;
window.stopRequested = false;  // Make it globally accessible
const START_URL = window.START_URL || "https://www.yahoo.co.jp";

// screenshot helper with improved error handling
async function captureScreenshot() {
  const maxRetries = 2;
  let lastError = null;
  
  for (let attempt = 1; attempt <= maxRetries; attempt++) {
    try {
      // Add timeout for screenshot requests
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 8000); // 8s timeout
      
      const response = await fetch("/screenshot", {
        signal: controller.signal
      });
      
      clearTimeout(timeoutId);
      
      if (!response.ok) {
        const errorText = await response.text().catch(() => "Unknown error");
        console.warn(`Screenshot fetch failed (attempt ${attempt}/${maxRetries}):`, response.status, errorText);
        
        // Don't retry on client errors (4xx)
        if (response.status >= 400 && response.status < 500) {
          console.error("Screenshot client error, not retrying:", response.status);
          return null;
        }
        
        lastError = `HTTP ${response.status}: ${errorText}`;
        
        if (attempt < maxRetries) {
          await sleep(1000 * attempt); // 1s, 2s delay
          continue;
        }
      } else {
        const data = await response.text();
        if (attempt > 1) {
          console.log("Screenshot retry succeeded");
        }
        return data;
      }
    } catch (e) {
      console.warn(`Screenshot error (attempt ${attempt}/${maxRetries}):`, e.message);
      lastError = e.message;
      
      // Retry on network errors but not on abort
      if (attempt < maxRetries && e.name !== 'AbortError') {
        await sleep(1000 * attempt);
        continue;
      }
    }
  }
  
  console.error("Screenshot failed after all retries:", lastError);
  return null;
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
   Enhanced health check utilities
   ====================================== */
async function checkServerHealth() {
  try {
    // Use the new health endpoint with retry logic
    const maxRetries = 2;
    let lastError = null;
    
    for (let attempt = 1; attempt <= maxRetries; attempt++) {
      try {
        const controller = new AbortController();
        const timeout = 3000 + (attempt * 1000); // 3s, 4s, 5s
        const timeoutId = setTimeout(() => controller.abort(), timeout);
        
        const response = await fetch("/health", {
          method: "GET",
          signal: controller.signal,
          headers: {
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache'
          }
        });
        
        clearTimeout(timeoutId);
        
        if (response.ok) {
          const healthData = await response.json();
          const isHealthy = healthData.status === "healthy";
          
          if (attempt > 1) {
            console.log(`Health check succeeded on attempt ${attempt}`);
          }
          
          return isHealthy;
        } else {
          lastError = `HTTP ${response.status}`;
          console.warn(`Health check failed (attempt ${attempt}/${maxRetries}): ${response.status}`);
          
          if (attempt < maxRetries) {
            await sleep(500 * attempt); // 500ms, 1s delay
            continue;
          }
        }
      } catch (e) {
        lastError = e.message;
        
        if (e.name === 'AbortError') {
          console.warn(`Health check timeout (attempt ${attempt}/${maxRetries}): ${timeout}ms`);
        } else {
          console.warn(`Health check error (attempt ${attempt}/${maxRetries}):`, e.message);
        }
        
        if (attempt < maxRetries) {
          await sleep(500 * attempt);
          continue;
        }
      }
    }
    
    console.warn(`Health check failed after ${maxRetries} attempts:`, lastError);
    return false;
    
  } catch (e) {
    console.warn("Health check completely failed:", e);
    return false;
  }
}

// Enhanced automation server health check
async function checkAutomationServerHealth() {
  try {
    const maxRetries = 2;
    let lastError = null;
    
    for (let attempt = 1; attempt <= maxRetries; attempt++) {
      try {
        const controller = new AbortController();
        const timeout = 2000 + (attempt * 500); // 2s, 2.5s, 3s
        const timeoutId = setTimeout(() => controller.abort(), timeout);
        
        const response = await fetch("/automation/healthz", {
          method: "GET",
          signal: controller.signal,
          headers: {
            'Cache-Control': 'no-cache'
          }
        });
        
        clearTimeout(timeoutId);
        
        if (response.ok) {
          if (attempt > 1) {
            console.log(`Automation health check succeeded on attempt ${attempt}`);
          }
          return true;
        } else {
          lastError = `HTTP ${response.status}`;
          console.warn(`Automation health check failed (attempt ${attempt}/${maxRetries}): ${response.status}`);
          
          if (attempt < maxRetries) {
            await sleep(300 * attempt); // 300ms, 600ms delay
            continue;
          }
        }
      } catch (e) {
        lastError = e.message;
        
        if (e.name === 'AbortError') {
          console.warn(`Automation health timeout (attempt ${attempt}/${maxRetries}): ${timeout}ms`);
        } else {
          console.warn(`Automation health error (attempt ${attempt}/${maxRetries}):`, e.message);
        }
        
        if (attempt < maxRetries) {
          await sleep(300 * attempt);
          continue;
        }
      }
    }
    
    console.warn(`Automation health check failed after ${maxRetries} attempts:`, lastError);
    return false;
    
  } catch (e) {
    console.warn("Automation health check completely failed:", e);
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
      const isMainServerHealthy = await checkServerHealth();
      const isAutomationHealthy = await checkAutomationServerHealth();
      
      if (!isMainServerHealthy && !isAutomationHealthy) {
        console.warn("Both main server and automation server appear unhealthy");
        showSystemMessage("サーバーの状態を確認中です...");
        await sleep(3000); // Wait 3 seconds for server recovery
      } else if (isMainServerHealthy) {
        console.log("Main server is healthy, proceeding with retry");
      } else if (isAutomationHealthy) {
        console.log("Automation server is healthy, proceeding with retry");
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

  // Handle command errors
  if (res.error) {
    console.warn("Command execution had errors:", res.error);
    if (showInUI && thinkingElement) {
      thinkingElement.textContent = res.explanation || "通信エラーが発生しました。";
      thinkingElement.querySelector(".spinner")?.remove();
    }
    // Return early if there's a communication error
    if (res.error.includes("Command failed") || res.error.includes("Failed to fetch")) {
      return { 
        cont: false, 
        explanation: res.explanation || "通信エラーが発生しました。しばらく待ってから再試行してください。", 
        memory: "", 
        html: html, 
        screenshot: screenshot, 
        error: res.error 
      };
    }
  }

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
    
    // Show execution status
    const statusElement = document.createElement("p");
    statusElement.classList.add("system-message");
    statusElement.textContent = "🔄 ブラウザ操作を実行中...";
    statusElement.style.color = "#007bff";
    chatArea.appendChild(statusElement);
    chatArea.scrollTop = chatArea.scrollHeight;

    // Poll for execution completion
    const executionResult = await pollExecutionStatus(res.task_id);
    
    if (executionResult) {
      // Update status message based on result
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
          }
          
          // Get updated HTML from execution result (improved)
          if (executionResult.result.updated_html) {
            newHtml = executionResult.result.updated_html;
            console.log("Using updated HTML from async execution result");
          } else if (executionResult.result.html) {
            newHtml = executionResult.result.html;
            console.log("Using HTML from async execution result");
          }
        }
      } else if (executionResult.status === "completed_via_fallback") {
        statusElement.textContent = "✅ ブラウザ操作が完了しました（フォールバック経由）";
        statusElement.style.color = "#28a745";
        
        // Handle fallback completion
        if (executionResult.result) {
          newHtml = executionResult.result.html || newHtml;
          errInfo = null; // Clear error since we recovered
          
          // Show fallback warnings
          if (executionResult.result.warnings && executionResult.result.warnings.length > 0) {
            displayWarnings(executionResult.result.warnings, "fallback");
            await storeWarningsInHistory(executionResult.result.warnings);
          }
        }
      } else if (executionResult.status === "failed") {
        statusElement.textContent = "❌ ブラウザ操作に失敗しました";
        statusElement.style.color = "#dc3545";
        errInfo = executionResult.error || "Unknown execution error";
      } else if (executionResult.status === "stopped") {
        statusElement.textContent = "⏹ ブラウザ操作が停止されました";
        statusElement.style.color = "#ffc107";
        errInfo = "Operation was stopped by user";
      } else if (executionResult.status === "timeout") {
        // Enhanced timeout handling with fallback information
        const serverStatus = executionResult.server_status;
        const fallbackReason = executionResult.fallback_reason;
        
        if (executionResult.result && executionResult.result.html) {
          // We got some data via fallback
          statusElement.textContent = "⚠️ 状態確認にエラーがありましたが、ページ状態を取得できました";
          statusElement.style.color = "#fd7e14";
          newHtml = executionResult.result.html;
          
          // Show fallback warnings
          if (executionResult.result.warnings) {
            displayWarnings(executionResult.result.warnings, "fallback");
            await storeWarningsInHistory(executionResult.result.warnings);
          }
          
          errInfo = null; // Clear error since we recovered some data
        } else if (serverStatus && (serverStatus.main_server || serverStatus.automation_server)) {
          // Server is healthy but polling failed
          statusElement.textContent = "⚠️ 状態確認は失敗しましたが、サーバーは正常に動作しています";
          statusElement.style.color = "#fd7e14";
          errInfo = `Status polling failed (${fallbackReason}) but server is responsive`;
        } else {
          // Complete communication failure
          statusElement.textContent = "⚠️ サーバーとの通信に問題があります - 自動的に回復する可能性があります";
          statusElement.style.color = "#ffc107";
          errInfo = `Communication issue (${fallbackReason}) - operation may still be running`;
        }
        
        // Don't treat timeout as a complete failure, just note it
      } else {
        statusElement.textContent = "🔄 ブラウザ操作の状態が不明です";
        statusElement.style.color = "#6c757d";
        errInfo = "Unknown execution status";
      }
    } else {
      // Handle the case where polling completely failed - this should be rare now
      console.warn("Polling returned null for task", res.task_id);
      
      // Try one final fallback using our graceful fallback mechanism
      const fallbackResult = await attemptGracefulFallback(res.task_id, null, "polling_null_result");
      
      if (fallbackResult && fallbackResult.result && fallbackResult.result.html) {
        // We managed to recover some data
        statusElement.textContent = "⚠️ 実行状態の確認には失敗しましたが、現在のページ状態を取得しました";
        statusElement.style.color = "#fd7e14";
        newHtml = fallbackResult.result.html;
        
        if (fallbackResult.result.warnings) {
          displayWarnings(fallbackResult.result.warnings, "final_fallback");
          await storeWarningsInHistory(fallbackResult.result.warnings);
        }
      } else {
        // Complete failure - but be less alarming
        statusElement.textContent = "⚠️ 実行状態を確認できませんでした - ページの手動確認をお勧めします";
        statusElement.style.color = "#ffc107";
        
        // Add a helpful message
        const helpMessage = document.createElement("p");
        helpMessage.classList.add("system-message");
        helpMessage.textContent = "💡 操作は完了している可能性があります。必要に応じてページを手動で確認してください。";
        helpMessage.style.color = "#6c757d";
        helpMessage.style.fontStyle = "italic";
        chatArea.appendChild(helpMessage);
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
    cont: res.complete === false && (res.actions || []).length > 0, 
    explanation: res.explanation || "", 
    memory: res.memory || "", 
    html: newHtml, 
    screenshot: newShot, 
    error: errInfo 
  };
}

/* ======================================
   Poll execution status with enhanced robustness
   ====================================== */
async function pollExecutionStatus(taskId, maxAttempts = 60, initialInterval = 300) {
  const startTime = Date.now();
  const maxDuration = 90000; // Extended to 90 seconds total wait time
  let consecutiveErrors = 0;
  const maxConsecutiveErrors = 3; // Reduced for faster fallback
  let lastKnownStatus = null;
  
  // Pre-check server health before starting polling
  const serverHealthy = await checkServerHealth();
  if (!serverHealthy) {
    console.warn(`Server appears unhealthy before polling task ${taskId}, but will try anyway`);
  }
  
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    // Check stop flags before each poll attempt
    if (stopRequested || window.stopRequested) {
      console.log(`Polling stopped for task ${taskId} due to stop request`);
      return { status: "stopped", error: "Operation was stopped by user" };
    }
    
    // Adaptive interval with exponential backoff capped at 2s
    const baseInterval = Math.min(initialInterval + (attempt * 50), 2000);
    const errorMultiplier = consecutiveErrors > 0 ? Math.min(2 ** consecutiveErrors, 4) : 1;
    const interval = Math.min(baseInterval * errorMultiplier, 3000);
    
    try {
      const controller = new AbortController();
      // Dynamic timeout: longer for later attempts when errors occur
      const requestTimeout = Math.min(3000 + (consecutiveErrors * 1000), 8000);
      const timeoutId = setTimeout(() => controller.abort(), requestTimeout);
      
      const response = await fetch(`/execution-status/${taskId}`, {
        signal: controller.signal,
        headers: {
          'Cache-Control': 'no-cache',
          'Pragma': 'no-cache'
        }
      });
      
      clearTimeout(timeoutId);
      
      if (!response.ok) {
        consecutiveErrors++;
        console.warn(`Failed to get execution status (attempt ${attempt + 1}/${maxAttempts}): ${response.status}`);
        
        // Immediate fallback for client errors (4xx) - these won't improve with retrying
        if (response.status >= 400 && response.status < 500) {
          console.error(`Client error ${response.status} for task ${taskId}, attempting graceful fallback`);
          return await attemptGracefulFallback(taskId, lastKnownStatus, "client_error");
        }
        
        // For server errors, check if we should give up early
        if (consecutiveErrors >= maxConsecutiveErrors) {
          console.error(`Too many consecutive errors (${consecutiveErrors}) for task ${taskId}`);
          return await attemptGracefulFallback(taskId, lastKnownStatus, "server_errors");
        }
        
        // Exponential backoff for server errors
        await sleep(interval);
        continue;
      }
      
      // Reset error counter on successful response
      if (consecutiveErrors > 0) {
        console.log(`Connection recovered for task ${taskId} after ${consecutiveErrors} errors`);
        consecutiveErrors = 0;
      }
      
      const status = await response.json();
      lastKnownStatus = status; // Store the last known good status
      console.log(`Task ${taskId} status (attempt ${attempt + 1}):`, status.status);
      
      // Task completed (successfully or failed)
      if (status.status === "completed" || status.status === "failed") {
        return status;
      }
      
      // Check if we've exceeded the maximum duration
      if (Date.now() - startTime > maxDuration) {
        console.warn(`Polling timeout for task ${taskId} - exceeded ${maxDuration}ms`);
        return await attemptGracefulFallback(taskId, lastKnownStatus, "duration_timeout");
      }
      
      // Check stop flags again before sleeping
      if (stopRequested || window.stopRequested) {
        console.log(`Polling stopped for task ${taskId} during wait`);
        return { status: "stopped", error: "Operation was stopped by user" };
      }
      
      // Wait before next poll
      await sleep(interval);
      
    } catch (e) {
      consecutiveErrors++;
      
      // Log different types of errors differently
      if (e.name === 'AbortError') {
        console.warn(`Request timeout for task ${taskId} (attempt ${attempt + 1}/${maxAttempts}) - ${requestTimeout}ms timeout`);
      } else if (e.message.includes('Failed to fetch') || e.message.includes('NetworkError')) {
        console.warn(`Network error for task ${taskId} (attempt ${attempt + 1}/${maxAttempts}):`, e.message);
      } else {
        console.error(`Unexpected error polling task ${taskId} (attempt ${attempt + 1}/${maxAttempts}):`, e);
      }
      
      // Check stop flags even in error case
      if (stopRequested || window.stopRequested) {
        return { status: "stopped", error: "Operation was stopped by user" };
      }
      
      // Early fallback for persistent network issues
      if (consecutiveErrors >= maxConsecutiveErrors) {
        console.error(`Too many consecutive network errors (${consecutiveErrors}) for task ${taskId}`);
        return await attemptGracefulFallback(taskId, lastKnownStatus, "network_errors");
      }
      
      // Progressive delay for network errors
      await sleep(interval);
    }
  }
  
  console.warn(`Polling reached maximum attempts (${maxAttempts}) for task ${taskId}`);
  return await attemptGracefulFallback(taskId, lastKnownStatus, "max_attempts");
}

/* ======================================
   Graceful fallback when polling fails
   ====================================== */
async function attemptGracefulFallback(taskId, lastKnownStatus, reason) {
  console.log(`Attempting graceful fallback for task ${taskId}, reason: ${reason}`);
  
  // Strategy 1: Try to get current page state regardless of task status
  let fallbackHtml = null;
  try {
    const controller = new AbortController();
    setTimeout(() => controller.abort(), 5000); // 5s timeout for fallback
    
    const htmlResponse = await fetch("/vnc-source", {
      signal: controller.signal,
      headers: { 'Cache-Control': 'no-cache' }
    });
    
    if (htmlResponse.ok) {
      fallbackHtml = await htmlResponse.text();
      console.log(`Successfully retrieved fallback HTML for task ${taskId} (${fallbackHtml.length} chars)`);
    }
  } catch (e) {
    console.warn(`Fallback HTML retrieval failed for task ${taskId}:`, e.message);
  }
  
  // Strategy 2: Check overall server health
  let serverHealthy = false;
  try {
    serverHealthy = await checkServerHealth();
  } catch (e) {
    console.warn(`Health check failed during fallback for task ${taskId}:`, e.message);
  }
  
  // Strategy 3: Try automation server health as backup
  let automationHealthy = false;
  if (!serverHealthy) {
    try {
      automationHealthy = await checkAutomationServerHealth();
    } catch (e) {
      console.warn(`Automation health check failed for task ${taskId}:`, e.message);
    }
  }
  
  // Build result based on what we could determine
  let result = {
    status: "timeout",
    error: `Status polling failed (${reason})`,
    recoverable: true,
    fallback_reason: reason
  };
  
  // If we have recent status, use it
  if (lastKnownStatus) {
    result.last_known_status = lastKnownStatus.status;
    result.last_known_time = lastKnownStatus.started_at || lastKnownStatus.created_at;
  }
  
  // If we got HTML, include it in the result
  if (fallbackHtml) {
    result.result = {
      html: fallbackHtml,
      warnings: [`WARNING: Retrieved page state via fallback method due to polling failure (${reason})`]
    };
    result.status = "completed_via_fallback";
    result.error = null; // Clear error since we recovered some data
  }
  
  // Add health information
  result.server_status = {
    main_server: serverHealthy,
    automation_server: automationHealthy
  };
  
  return result;
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
