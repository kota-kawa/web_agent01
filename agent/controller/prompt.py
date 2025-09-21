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


def _trim(text: object, limit: int = 120) -> str:
    s = str(text)
    if len(s) <= limit:
        return s
    return s[: limit - 1] + "…"


def _format_action_trace(
    trace: Iterable[dict] | None,
    warnings: Iterable[str] | None = None,
) -> str:
    if not trace:
        return "(直近の自動操作はありません)"
    lines: list[str] = []
    for idx, entry in enumerate(trace, 1):
        if not isinstance(entry, dict):
            continue
        action = entry.get("action", "")
        status = entry.get("status", "ok")
        params = entry.get("params") or {}
        param_parts: list[str] = []
        if isinstance(params, dict):
            for key, value in params.items():
                if value in (None, ""):
                    continue
                if isinstance(value, (int, float)):
                    param_parts.append(f"{key}={value}")
                elif isinstance(value, bool):
                    if value:
                        param_parts.append(f"{key}=true")
                else:
                    param_parts.append(f"{key}={_trim(value, 40)}")
        details = entry.get("details") or {}
        detail_parts: list[str] = []
        if isinstance(details, dict):
            for key, value in details.items():
                if value in (None, ""):
                    continue
                if isinstance(value, (int, float)):
                    detail_parts.append(f"{key}={value}")
                elif isinstance(value, bool):
                    detail_parts.append(f"{key}={str(value).lower()}")
                else:
                    detail_parts.append(f"{key}={_trim(value, 40)}")
        extra = ""
        if detail_parts:
            extra = " | " + ", ".join(detail_parts[:3])
        if entry.get("error"):
            extra += f" | error={_trim(entry['error'], 60)}"
        lines.append(
            f"{idx}. {action} [{status}] params: {', '.join(param_parts) or '-'}{extra}"
        )
    warn_lines = [str(w).strip() for w in warnings or [] if str(w).strip()]
    if warn_lines:
        lines.append("Warnings: " + " / ".join(_trim(w, 80) for w in warn_lines[-3:]))
    return "\n".join(lines)


def build_prompt(
    cmd: str,
    snapshot: DOMSnapshot | None,
    hist,
    screenshot: bool = False,
    error: str | list | None = None,
    raw_html: str | None = None,
    *,
    action_trace: Iterable[dict] | None = None,
    action_warnings: Iterable[str] | None = None,
    extracted_texts: Iterable | None = None,
    eval_results: Iterable | None = None,
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
あなたは高度なブラウザ自動化エージェントです。ユーザーの最終目的を最小の試行回数で達成するため、直近の操作ログとDOMサマリーから状況を精査し、論理的に次のアクションを決定してください。

### コンテキストの読み方
- DOMサマリー冒頭の `Open Tabs` は開いているタブを示し、`*` が付いたタブが現在アクティブです。
- 要素一覧では `[番号] <tag>` 形式のインデックスを使用します。`*[番号]` は前回以降に新しく検出された要素です。
- インデントは親子関係やフレームを表し、`scrollable` が付いている行は `scroll` アクションで `frame_element_index` を指定すると内部スクロールが可能です。
- 「直近のブラウザ操作ログ」で前ステップの成功/失敗と警告を確認し、同じ失敗を繰り返さないようにしてください。

### 出力フォーマット
1. 日本語で状況・取得情報・次の方針を200〜300文字程度で記述します。
2. 続けて ```json フェンス内に
   ```json
   {{
     "actions": [ ... ],
     "complete": false
   }}
   ```
   を出力します。`actions` が空の場合は `complete` を `true` にし、完了理由を説明文で明記します。
3. JSON以外の余計な文字列は出力しません。

### 利用可能な主なアクション
- `{{"action": "search_google", "query": "検索語", "new_tab": false}}`
- `{{"action": "go_to_url", "target": "https://...", "new_tab": false}}`
- `{{"action": "click_element_by_index", "index": 12, "while_holding_ctrl": false}}`
- `{{"action": "input_text", "index": 15, "text": "入力内容", "clear_existing": true}}` (index 0 でページ全体へのタイプ)
- `{{"action": "wait", "ms": 1000}}`, `{{"action": "wait_for_selector", "target": "css=...", "ms": 3000}}`
- `{{"action": "scroll", "down": true, "num_pages": 0.5, "frame_element_index": 34}}`
- `{{"action": "scroll_to_text", "text": "探したい文字列"}}`
- `{{"action": "send_keys", "keys": "Control+L"}}`, `{{"action": "press_key", "key": "Enter", "target": "css=..."}}`
- `{{"action": "switch_tab", "tab_id": "00a1"}}`, `{{"action": "close_tab", "tab_id": "00a1"}}`
- `{{"action": "get_dropdown_options", "index": 27}}`, `{{"action": "select_dropdown_option", "index": 27, "text": "表示テキスト"}}`
- `{{"action": "upload_file_to_element", "index": 31, "path": "/path/to/file"}}`
- `{{"action": "extract_structured_data", "index": 40}}` または `target` をCSSで指定
- `{{"action": "extract_page_content", "target": "css=article"}}` で要素やページ全文を取得
- `{{"action": "structured_output", "data": {{...}}}}` でユーザー指定の構造化回答を生成
- `{{"action": "done", "text": "結果", "success": true}}` で最終報告を行う
- `{{"action": "eval_js", "script": "..."}}`, `{{"action": "extract_text", "target": "css=..."}}`

### 行動ルール
- インデックスが付与された要素のみを直接操作し、推測セレクタでの操作は避けます。
- ページ遷移やモーダル表示後は必ず新しいDOMを確認し、必要に応じて `wait` や `scroll` で描画を安定化させてから次の操作を決定します。
- ドロップダウンやサジェストは `get_dropdown_options` で内容を把握し、結果を説明文に反映します。
- 構造化された回答やファイル提出が求められる場合は `structured_output` や `done` を適切に使用します。
- 取得済みテキストや `eval_js` の結果は説明文に盛り込み、不要な再取得を避けます。
- 連続操作は最大 {MAX_STEPS} ステップまでです。達成できない場合は残作業と理由を整理して `complete: true` を返してください。

### 思考ガイド
- 直近の操作ログ・会話履歴・DOMサマリー・抽出済み情報を踏まえて計画し、同じ失敗を繰り返さないようにしてください。
- 1ステップで複数アクションを出す場合は、状態確認ができる最小の組み合わせに限定し、長いシーケンスを予測しないでください。
- `done` を呼ぶ際は `success` の真偽を正しく設定し、ユーザーに必要な情報を漏らさず伝えてから終了してください。
"""

    action_log_text = _format_action_trace(action_trace, action_warnings)

    extracted_list = list(extracted_texts or [])
    extracted_lines = [
        f"{idx}. {_trim(item, 160)}"
        for idx, item in enumerate(extracted_list[-3:], 1)
    ]

    eval_list = list(eval_results or [])
    eval_lines = [
        f"{idx}. {_trim(item, 160)}"
        for idx, item in enumerate(eval_list[-3:], 1)
    ]

    additional_lines = [f"スクリーンショット: {add_img}"]
    if error_line:
        additional_lines.append(f"エラー/警告: {error_line}")
    if extracted_lines:
        additional_lines.append("収集済みテキスト(最新3件):")
        additional_lines.extend(f"  {line}" for line in extracted_lines)
    if eval_lines:
        additional_lines.append("eval_js 結果(最新3件):")
        additional_lines.extend(f"  {line}" for line in eval_lines)

    system_prompt = (
        instructions
        + "\n--------------------------------\n"
        + "## 直近のブラウザ操作ログ\n"
        + f"{action_log_text}\n"
        + "--------------------------------\n"
        + "## これまでの会話履歴\n"
        + f"{past_conv}\n"
        + "--------------------------------\n"
        + "## ユーザー命令\n"
        + f"{cmd}\n"
        + "--------------------------------\n"
        + "## 追加情報\n"
        + "\n".join(additional_lines)
        + "\n--------------------------------\n"
        + "## DOMサマリー\n"
        + f"{dom_text}"
    )

    return system_prompt
