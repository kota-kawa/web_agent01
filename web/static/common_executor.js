// common_executor.js
function sendCommand(command, pageSource, screenshot, model, errorInfo = null) {
  return fetch("/execute", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ command, pageSource, screenshot, model, error: errorInfo })
  }).then(r => r.json());
}
