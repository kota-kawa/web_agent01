// common_executor.js
function sendCommand(command, pageSource) {
  return fetch("/execute", {
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({command, pageSource})
  }).then(r=>r.json());
}
