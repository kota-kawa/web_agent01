// browser_executor.js

/* ======================================
   汎用ユーティリティ
   ====================================== */
const sleep = ms => new Promise(r => setTimeout(r, ms));
const chatArea = document.getElementById("chat-area");       // 追加: チャット欄参照

/* ======================================
   DSL 正規化
   ====================================== */
function normalizeActions(instr) {
  if (!instr) return [];
  let acts = Array.isArray(instr.actions) ? instr.actions :
             Array.isArray(instr)          ? instr :
             instr.action                  ? [instr] : [];
  return acts.map(o => {
    const a = {...o};
    if (a.action) a.action = String(a.action).toLowerCase();
    if (a.selector && !a.target) a.target = a.selector;
    if (a.text && a.action === "click_text" && !a.target) a.target = a.text;
    return a;
  });
}

/* ======================================
   DSL を Playwright へ送信
   ====================================== */
async function sendDSL(acts) {
  if (!acts.length) return;           // 空なら送らない → 500 防止
  try {
    const r = await fetch("/automation/execute-dsl", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actions: acts })
    });
    if (!r.ok) {
      console.error("execute-dsl failed:", r.status, await r.text());
    }
  } catch (e) {
    console.error("execute-dsl fetch error:", e);
  }
}

/* ======================================
   1ターン実行
   showInUI === true ならチャット欄に説明を追加
   戻り値: { cont:Boolean, explanation:String }
   ====================================== */
async function runTurn(cmd, showInUI = true) {
  // 最新ページ HTML を取得 (失敗しても '' になる)
  const html = await fetch("/vnc-source")
    .then(r => (r.ok ? r.text() : ""))
    .catch(() => "");

  // Gemini＋解析結果取得
  const res = await sendCommand(cmd, html);

  /* -- UI へ進行状況を追加表示 -- */
  if (showInUI && res.explanation) {
    const p = document.createElement("p");
    p.classList.add("bot-message");
    p.textContent = res.explanation;
    chatArea.appendChild(p);
    chatArea.scrollTop = chatArea.scrollHeight;
  }

  /* -- DevTools Console に raw を 1 回だけ出力 -- */
  if (res.raw) console.log("Gemini raw output:\n", res.raw);

  /* -- DSL 実行 -- */
  await sendDSL(normalizeActions(res));

  /* 次のループを継続するかどうか */
  return { cont: res.complete === false, explanation: res.explanation || "" };
}

/* ======================================
   マルチターン実行
   skipFirst === true なら 1 ターン目は UI に二重表示しない
   ====================================== */
async function executeTask(cmd, skipFirst = false) {
  let keepLoop   = true;
  let firstIter  = true;
  let lastMsg    = "";       // ★ 追加: 前ターンの説明
  let repeatCnt  = 0;        // ★ 追加: 同一説明の連続回数
  const MAX_REP  = 1;        // ★ 追加: ここを超えたら強制終了

  while (keepLoop) {
    try {
      const show = !(skipFirst && firstIter);
      const { cont, explanation } = await runTurn(cmd, show);

      /* ----- ★ 追加: 重複説明チェック ----- */
      if (explanation === lastMsg) {
        repeatCnt += 1;
        if (repeatCnt > MAX_REP) {
          console.warn("同一説明が繰り返されたためループを終了します。");
          break;   // 強制終了
        }
      } else {
        lastMsg   = explanation;
        repeatCnt = 0;       // リセット
      }
      /* ----------------------------------- */

      keepLoop  = cont;
      firstIter = false;

      if (keepLoop) await sleep(1000);
    } catch (e) {
      console.error("runTurn error:", e);
      await sleep(1000);
    }
  }

  /* 完了 or 強制終了メッセージ */
  const done = document.createElement("p");
  done.classList.add("system-message");
  done.textContent = "✅ タスクを終了しました";
  chatArea.appendChild(done);
  chatArea.scrollTop = chatArea.scrollHeight;
}

/* ======================================
   デバッグ用: 手動実行ボタン
   ====================================== */
document.getElementById("executeButton")
  .addEventListener("click", () => {
    const cmd = document.getElementById("nlCommand").value.trim();
    if (cmd) executeTask(cmd, false);
  });
