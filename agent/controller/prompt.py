import logging
import os
from typing import Iterable

from ..browser.dom import DOMSnapshot
from ..utils.html import strip_html

log = logging.getLogger("controller")
MAX_STEPS = int(os.getenv("MAX_STEPS", "10"))


def _format_history(hist: Iterable[dict]) -> str:
    lines: list[str] = []
    for entry in hist:
        user = entry.get("user", "")
        bot = entry.get("bot", {}) or {}
        explanation = bot.get("explanation", "")
        if user or explanation:
            lines.append(f"U:{user}")
            lines.append(f"A:{explanation}")
    return "\n".join(lines) if lines else "(履歴なし)"


def _format_error(error: str | list | None) -> str:
    if not error:
        return ""
    if isinstance(error, list):
        lines: list[str] = []
        for item in error:
            lines.extend(str(item).splitlines())
    else:
        lines = str(error).splitlines()
    keywords = (
        "error",
        "timeout",
        "not found",
        "exception",
        "traceback",
        "fail",
    )
    filtered = [line for line in lines if any(k in line.lower() for k in keywords)]
    selected = filtered[-10:] if filtered else lines[-10:]
    return "\n".join(selected)


def build_prompt(
    cmd: str,
    snapshot: DOMSnapshot | None,
    hist,
    screenshot: bool = False,
    error: str | list | None = None,
    raw_html: str | None = None,
) -> str:
    """Return the full system prompt for the LLM."""

    past_conv = _format_history(hist)
    add_img = (
        "スクリーンショット画像が添付されています。"
        if screenshot
        else "スクリーンショットは今回提供されません。"
    )

    dom_text: str
    if snapshot:
        dom_text = snapshot.to_text(limit=160)
    else:
        fallback = strip_html(raw_html or "")
        dom_text = fallback[:4000] if fallback else "(DOMサマリーの取得に失敗しました)"

    if snapshot and snapshot.error:
        if error:
            error = f"{error}\n{snapshot.error}"
        else:
            error = snapshot.error
    error_line = _format_error(error)

    instructions = f"""
あなたはブラウザタスクを自動化するAIエージェントです。ユーザーの最終目的を満たすために、慎重に観察し、論理的に行動を選択してください。

### 思考プロセス
1. **観察**: DOMサマリーと会話履歴から現在の状況を正確に把握してください。
2. **思考**: 目的達成までの最短経路を計画し、同じ失敗を繰り返さないようにします。
3. **行動決定**: 必要な操作を Playwright 互換の DSL(JSON) で出力してください。

### DOMサマリーの読み方
- 各行は `[番号] <tag> ...` 形式で、番号がインデックスです。操作する場合は必ずこの番号を `click_element_by_index` などのアクションに指定してください。
- `selector hint` は補助的なロケータ候補です。インデックスが最優先で、ヒントは代替案を考える際にのみ参照してください。
- `scrollable` が付いた要素はスクロール可能なコンテナを示します。`scroll` アクションで `frame_element_index` にその番号を指定することで内部スクロールが可能です。

### 出力フォーマット
1. 最初に日本語で、状況の説明・取得した情報・次の方針を200〜300文字程度で記述してください。
2. 続けて ```json フェンス内に以下形式でDSLを出力してください。
   ```json
   {{
     "actions": [ ... ],
     "complete": false
   }}
   ```
- `actions` が空の場合は `complete` を必ず `true` にし、タスク完了理由を説明文で述べます。
- JSON以外の不要な文字列は出力しないでください。

### 利用可能なアクション
- `{{"action": "search_google", "query": "検索語", "new_tab": false}}`
- `{{"action": "go_to_url", "target": "https://...", "new_tab": false}}`
- `{{"action": "click_element_by_index", "index": 12, "while_holding_ctrl": false}}`
- `{{"action": "input_text", "index": 15, "text": "入力内容", "clear_existing": true}}` (index 0 でページ全体へのタイプ)
- `{{"action": "wait", "ms": 1000}}`
- `{{"action": "scroll", "down": true, "num_pages": 0.5, "frame_element_index": 34}}`
- `{{"action": "scroll_to_text", "text": "探したい文字列"}}`
- `{{"action": "send_keys", "keys": "Control+L"}}`
- `{{"action": "switch_tab", "tab_id": "00a1"}}`
- `{{"action": "close_tab", "tab_id": "00a1"}}`
- `{{"action": "get_dropdown_options", "index": 27}}`
- `{{"action": "select_dropdown_option", "index": 27, "text": "表示テキスト"}}`
- `{{"action": "upload_file_to_element", "index": 31, "path": "/path/to/file"}}`
- `{{"action": "extract_structured_data", "index": 40}}` または `target` をCSSで指定
- `{{"action": "wait_for_selector", "target": "css=...", "ms": 3000}}`
- `{{"action": "go_back"}}`, `{{"action": "go_forward"}}`, `{{"action": "hover", "target": "css=..."}}`, `{{"action": "press_key", "key": "Enter", "target": "css=..."}}`, `{{"action": "extract_text", "target": "css=..."}}`, `{{"action": "eval_js", "script": "..."}}`

### 重要なルール
- インデックスが付与された要素のみを直接操作してください。推測したセレクタで操作することは禁止です。
- ページ遷移後は新たに表示されたDOMを確認してから次の操作を計画してください。
- 同じ失敗を繰り返さず、必要に応じて `wait` や `scroll` を挿入して安定性を確保します。
- ドロップダウンの内容が不明なときは `get_dropdown_options` を先に呼び出し、結果を説明文に反映させてから `select_dropdown_option` を実行してください。
- タブ操作が必要な場合は `switch_tab` / `close_tab` を明示的に使用します。新しいタブで開く必要があるときは `click_element_by_index` の `while_holding_ctrl` か `go_to_url` / `search_google` の `new_tab` を活用してください。
- `extract_structured_data` や `extract_text` で取得した情報は説明文に必ず反映します。
- 最大 {MAX_STEPS} ステップ以内に目的を達成できない場合、残作業をまとめた上で `complete: true` を返してください。

### 参考: DOMサマリー
{dom_text}
"""

    system_prompt = (
        instructions
        + "\n--------------------------------\n"
        + "## これまでの会話履歴\n"
        + f"{past_conv}\n"
        + "--------------------------------\n"
        + "## ユーザー命令\n"
        + f"{cmd}\n"
        + "--------------------------------\n"
        + "## 追加情報\n"
        + f"スクリーンショット: {add_img}\n"
    )

    if error_line:
        system_prompt += f"エラー: {error_line}\n"

    return system_prompt
