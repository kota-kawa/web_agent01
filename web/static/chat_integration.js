// chat_integration.js ― チャット欄とバックエンドをつなぐ
document.addEventListener("DOMContentLoaded", () => {
  const sendButton  = document.querySelector("#input-area button");
  const userInput   = document.getElementById("user-input");
  const chatArea    = document.getElementById("chat-area");
  const resetBtn    = document.getElementById("reset-button");
  const inputStatus = document.getElementById("input-status");

  // Update input status based on execution state
  function updateInputStatus() {
    if (typeof window.isTaskExecuting === "function" && window.isTaskExecuting()) {
      if (typeof window.getQueuedPromptCount === "function") {
        const queueCount = window.getQueuedPromptCount();
        if (queueCount > 0) {
          inputStatus.textContent = `🔄 実行中 - 追加指示 ${queueCount}件 待機中`;
          inputStatus.style.color = "#ff9800";
        } else {
          inputStatus.textContent = "🔄 実行中 - 追加指示を入力できます";
          inputStatus.style.color = "#007bff";
        }
      } else {
        inputStatus.textContent = "🔄 実行中";
        inputStatus.style.color = "#007bff";
      }
      userInput.placeholder = "追加の指示やアドバイスを入力...";
    } else {
      inputStatus.textContent = "";
      userInput.placeholder = "ここに入力...";
    }
  }

  // Monitor execution state and update input status
  setInterval(updateInputStatus, 500);

  if (resetBtn) {
    resetBtn.addEventListener("click", async () => {
      // Confirm before resetting
      if (!confirm("会話履歴をリセットしますか？この操作は元に戻せません。")) {
        return;
      }
      
      // Stop any ongoing LLM operations
      if (typeof window.stopRequested !== 'undefined') {
        window.stopRequested = true;
      }
      
      try {
        const r = await fetch("/reset", {
          method: "POST",
          headers: {
            "Content-Type": "application/json"
          }
        });
        
        if (!r.ok) throw new Error("reset request failed");
        
        const response = await r.json();
        
        // Clear the chat area and show initial message
        chatArea.innerHTML = '<p class="bot-message">こんにちは！ご質問はありますか？</p>';
        
        // Show success message
        const successMsg = document.createElement("p");
        successMsg.classList.add("system-message");
        successMsg.textContent = response.message || "会話履歴がリセットされました";
        successMsg.style.color = "#28a745";
        chatArea.appendChild(successMsg);
        
        chatArea.scrollTop = chatArea.scrollHeight;
      } catch (e) {
        console.error("reset error:", e);
        
        // Show error message
        const errorMsg = document.createElement("p");
        errorMsg.classList.add("system-message");
        errorMsg.textContent = "リセットに失敗しました: " + e.message;
        errorMsg.style.color = "#dc3545";
        chatArea.appendChild(errorMsg);
        chatArea.scrollTop = chatArea.scrollHeight;
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

  /* ----- キーボードショートカット (Ctrl+Enter で送信) ----- */
  userInput.addEventListener("keydown", (evt) => {
    if ((evt.ctrlKey || evt.metaKey) && evt.key === "Enter") {
      evt.preventDefault();
      if (!sendButton.disabled) {
        sendButton.click();
      }
    }
  });

  /* ----- 送信ボタンイベント ----- */
  sendButton.addEventListener("click", (evt) => {
    evt.preventDefault();
    const text = userInput.value.trim();
    if (!text) return;

    // Check if a task is currently executing
    if (typeof window.isTaskExecuting === "function" && window.isTaskExecuting()) {
      // Task is executing, add to queue instead
      if (typeof window.addPromptToQueue === "function") {
        window.addPromptToQueue(text);

        /* ユーザーメッセージを追加 */
        const u = document.createElement("p");
        u.classList.add("user-message");
        u.innerHTML = `<strong>📝 追加指示:</strong> ${text}`;
        u.style.cssText = "background: #fff3e0; border-left: 3px solid #ff9800;";
        chatArea.appendChild(u);
        chatArea.scrollTop = chatArea.scrollHeight;

        userInput.value = "";
        return;
      }
    }

    // Prevent double submission for new tasks
    if (sendButton.disabled) return;

    // Disable briefly to avoid duplicate start
    sendButton.disabled = true;
    sendButton.textContent = "実行中...";

    /* ユーザーメッセージを追加 */
    const u = document.createElement("p");
    u.classList.add("user-message");
    u.textContent = text;
    chatArea.appendChild(u);

    userInput.value = "";

    /* AI 応答プレースホルダー + スピナー */
    const b = document.createElement("p");
    b.classList.add("bot-message");
    b.innerHTML = 'AI が応答中... <span class="spinner" style="display:inline-block;width:12px;height:12px;border:2px solid #f3f3f3;border-top:2px solid #3498db;border-radius:50%;animation:spin 1s linear infinite;"></span>';
    chatArea.appendChild(b);
    chatArea.scrollTop = chatArea.scrollHeight;

    const model = "gemini";  // デフォルトモデルを使用
    if (typeof window.executeTask === "function") {
      window.executeTask(text, model, b).catch(err => {
        console.error(err);
        b.textContent = "AI の応答に失敗しました: " + err.message;
      });
    } else {
      console.error("executeTask function not found.");
      b.textContent = "実行機能が見つかりません。";
    }

    // Re-enable UI immediately to allow additional prompts
    sendButton.disabled = false;
    sendButton.textContent = "送信";
    userInput.focus();
  });
});
