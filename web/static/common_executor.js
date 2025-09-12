// common_executor.js
function sendCommand(command, pageSource, screenshot, model, errorInfo = null) {
  return fetch("/execute", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ command, pageSource, screenshot, model, error: errorInfo }),
    signal: AbortSignal.timeout(30000) // 30 second timeout for command sending
  }).then(async r => {
    if (!r.ok) {
      // Handle error responses
      const errorText = await r.text().catch(() => "Unknown error");
      console.error("Command execution failed:", r.status, errorText);
      throw new Error(`Command failed: ${r.status} ${errorText}`);
    }
    return r.json();
  }).catch(e => {
    console.error("sendCommand network error:", e);
    // Return a structured error response instead of letting it bubble up
    return {
      explanation: "通信エラーが発生しました。しばらく待ってから再試行してください。",
      complete: true,
      actions: [],
      async_execution: false,
      error: e.message
    };
  });
}
