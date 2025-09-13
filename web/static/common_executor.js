// common_executor.js
function sendCommand(command, pageSource, screenshot, model, errorInfo = null) {
  const signal = window.stopController ? window.stopController.signal : undefined;
  return fetch("/execute", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ command, pageSource, screenshot, model, error: errorInfo }),
    signal,
  }).then(r => r.json());
}
