// common_executor.js
function sendCommand(command, pageSource, screenshot, model) {
  return fetch("/execute", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ command, pageSource, screenshot, model })
  }).then(r => r.json());
}
