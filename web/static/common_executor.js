// common_executor.js
function sendCommand(command, pageSource, model) {
  return fetch("/execute", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ command, pageSource, model })
  }).then(r=>r.json());
}
