// chat_integration.js ― チャット欄とバックエンドをつなぐ
document.addEventListener("DOMContentLoaded", () => {
  const sendButton  = document.querySelector("#input-area button");
  const userInput   = document.getElementById("user-input");
  const chatArea    = document.getElementById("chat-area");
  const modelSelect = document.getElementById("model-select");

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
          /* 変更: skipFirst=false → 最初の説明も UI に表示 */
          const model = modelSelect ? modelSelect.value : "gemini";
          await executeTask(cmd, false, model);    // 変更
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
    const spin = document.createElement("span");   // 追加
    spin.classList.add("spinner");                 // 追加
    b.appendChild(spin);                           // 追加
    chatArea.appendChild(b);
    chatArea.scrollTop = chatArea.scrollHeight;

    try {
      /* プレビュー用に LLM 呼び出し（/execute） */
      const pageSrc = await fetchVncHtml();
      const model   = modelSelect ? modelSelect.value : "gemini";
      const preview = await sendCommand(text, pageSrc, model);

      /* 変更: チャット欄には explanation のみを表示（JSONプレビューを削除） */
      b.textContent = preview.explanation || "(説明がありません)";

      /* DevTools Console に raw を 1 回だけ表示 */
      if (preview.raw) console.log("LLM raw output:\n", preview.raw);

      /* ----- マルチターン実行開始 -----
         skipFirst = true: 1st ターンは既に UI に表示済みなので
         2 ターン目以降だけ追加表示する */
      if (typeof executeTask === "function") {
        await executeTask(text, true, model);             // 変更
      } else {
        console.error("executeTask function not found.");
      }
    } catch (err) {
      console.error(err);
      b.textContent = "AI の応答に失敗しました。";
    }
  });
});
