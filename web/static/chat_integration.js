// chat_integration.js ― チャット欄とバックエンドをつなぐ
document.addEventListener("DOMContentLoaded", () => {
  const sendButton  = document.querySelector("#input-area button");
  const userInput   = document.getElementById("user-input");
  const chatArea    = document.getElementById("chat-area");
  const memoryBtn   = document.getElementById("memory-button");

  if (memoryBtn) {
    memoryBtn.addEventListener("click", async () => {
      try {
        const r = await fetch("/memory");
        if (!r.ok) throw new Error("memory fetch failed");
        const hist = await r.json();
        const pre = document.createElement("pre");
        pre.classList.add("system-message");
        pre.textContent = JSON.stringify(hist, null, 2);
        chatArea.appendChild(pre);
        chatArea.scrollTop = chatArea.scrollHeight;
      } catch (e) {
        console.error("memory fetch error:", e);
      }
    });
  }

  /* ----- ページ読み込み時に未完了タスクがあれば再開 ----- */
  (async function resumeIfNeeded() {
    try {
      const resp = await fetch("/history");
      if (!resp.ok) throw new Error("履歴取得失敗");
      const hist = await resp.json();
      if (Array.isArray(hist) && hist.length > 0) {
        const last = hist[hist.length - 1];
        if (last.bot.complete === false) {
          const cmd = last.user;
          const notice = document.createElement("p");
          notice.classList.add("system-message");
          notice.textContent = `未完了タスクを再開します: 「${cmd}」`;
          chatArea.appendChild(notice);
          /* モデル選択がないのでgeminiを使用 */
          const model = "gemini";
          if (typeof window.executeTask === "function") {
            await window.executeTask(cmd, model);
          } else {
            console.error("executeTask function not found.");
          }
        }
      }
    } catch (err) {
      console.error("タスク再開エラー:", err);
    }
  })();

  /* ----- VNC ページ HTML を取得 ----- */
  async function fetchVncHtml() {
    try {
      const res = await fetch("/vnc-source");
      return await res.text();
    } catch (err) {
      console.error("Failed to fetch /vnc-source:", err);
      return "";
    }
  }

  /* ----- 送信ボタンイベント ----- */
  sendButton.addEventListener("click", async (evt) => {
    evt.preventDefault();
    const text = userInput.value.trim();
    if (!text) return;

    /* ユーザーメッセージを追加 */
    const u = document.createElement("p");
    u.classList.add("user-message");
    u.textContent = text;
    chatArea.appendChild(u);

    userInput.value = "";

    /* AI 応答プレースホルダー + スピナー */
    const b = document.createElement("p");
    b.classList.add("bot-message");
    b.textContent = "AI が応答中...";
    const spin = document.createElement("span");
    spin.classList.add("spinner");
    b.appendChild(spin);
    chatArea.appendChild(b);
    chatArea.scrollTop = chatArea.scrollHeight;

    try {
      const model = "gemini";  // デフォルトモデルを使用
      /* ----- マルチターン実行開始 ----- */
      if (typeof window.executeTask === "function") {
        await window.executeTask(text, model, b);
      } else {
        console.error("executeTask function not found.");
      }
    } catch (err) {
      console.error(err);
      b.textContent = "AI の応答に失敗しました。";
    }
  });
});
