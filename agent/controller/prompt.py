import os
import logging
from ..utils.html import strip_html
from ..browser.dom import DOMElementNode

log = logging.getLogger("controller")
MAX_STEPS = int(os.getenv("MAX_STEPS", "10"))

def _collect_interactive(node: DOMElementNode, lst: list):
    if node.highlightIndex is not None:
        lst.append(node)
    for ch in getattr(node, "children", []):
        _collect_interactive(ch, lst)

def build_prompt(
    cmd: str,
    page: str,
    hist,
    screenshot: bool = False,
    elements: DOMElementNode | list | None = None,
    error: str | None = None,
) -> str:
    """Return optimized system prompt for smooth browser operations."""
    def _hist_item(h):
        txt = f"U:{h['user']}\nA:{h['bot']['explanation']}"
        mem = h["bot"].get("memory") if isinstance(h.get("bot"), dict) else None
        if mem:
            txt += f"\nM:{mem}"
        return txt
    past_conv = "\n".join(_hist_item(h) for h in hist)

    add_img = (
        "現在の状況を把握するために、スクリーンショット画像も与えます。"
        if screenshot
        else ""
    )
    elem_lines = ""
    error_line = ""
    if error:
        if isinstance(error, list):
            err_lines: list[str] = []
            for e in error:
                err_lines.extend(str(e).splitlines())
        else:
            err_lines = str(error).splitlines()

        # Extract only meaningful error lines. Previously the keyword list
        # contained "visible" which caused verbose Playwright logs such as
        # "waiting for locator ... to be visible" to be captured and drown out
        # actual error messages (e.g. timeouts).  Narrowing the keywords to
        # genuine error indicators ensures `error_line` reflects real
        # failures like "Timeout exceeded" or "Element is not visible".
        keywords = (
            "error",
            "timeout",
            "not found",
            "traceback",
            "exception",
            "warning",
            "not visible",
        )

        # Collect lines containing the above keywords and also retain a few
        # subsequent lines to capture Playwright call logs that often follow
        # the initial error line (e.g. locator details).  This provides the
        # LLM with richer context about the failure.
        lines: list[str] = []
        i = 0
        while i < len(err_lines):
            line = err_lines[i]
            if any(k in line.lower() for k in keywords):
                lines.append(line)
                # include up to five following lines for additional context
                lines.extend(err_lines[i + 1 : i + 6])
                i += 6
            else:
                i += 1

        if lines:
            error_line = "\n".join(lines[-10:]) + "\n--------------------------------\n"
    dom_text = strip_html(page)
    if elements:
        nodes: list[DOMElementNode] = []
        if isinstance(elements, DOMElementNode):
            _collect_interactive(elements, nodes)
            dom_text = elements.to_text(max_lines=None)
        elif isinstance(elements, list):
            for n in elements:
                if isinstance(n, DOMElementNode):
                    _collect_interactive(n, nodes)
        nodes.sort(key=lambda x: x.highlightIndex or 0)
        elem_lines = "\n".join(
            f"[{n.highlightIndex}] <{n.tagName}> {n.text or ''} id={n.attributes.get('id')} class={n.attributes.get('class')}"
            for n in nodes
        )

    system_prompt = (
        "あなたは、ブラウザタスクを効率的に自動化するAIエージェントです。\n"
        "ユーザーの命令を安定かつ迅速に達成することが目標です。\n"
        "ブラウザタスクを自動化するための効率的なエージェントとして動作します。\n\n"
        
        "**効率的な操作指針:**\n"
        "1. **状況把握**: ユーザー命令と履歴から進捗確認、DOMから操作要素特定、エラー分析\n"
        "2. **安定実行**: エラー時は代替手段使用、ループ検知時は戦略変更、適切な待機使用\n"
        "3. **要素選択**: id, data-testid, class, textを優先し、安定した選択方法を使用\n\n"
        
        "**重要な操作ルール:**\n"
        "- エラー時は同じ操作を繰り返さず、別手段（セレクタ変更、待機、スクロール）を使用\n"
        "- ループ検知時は戦略変更（go_back、別要素選択等）\n"
        "- 入力候補表示時は候補選択またはフォーム送信、同じ入力フィールド再クリック禁止\n"
        "- ページ遷移後は適切な待機（wait/wait_for_selector）使用\n"
        "- 目的要素未発見時はスクロールまたはページ戻りを試行\n"
        "- 数値インデックス付き要素を優先操作\n"
        "- complete前にユーザー要求の完了を必ず確認\n\n"
        
        "**出力フォーマット:**\n"
        "操作意図の説明（日本語、最長300文字）\n"
        "その後にJSON形式でDSL出力\n\n"
        
        "**初回応答**: タスクのプランニングを行い、actions=[], complete=false\n"
        "**継続**: 計画に沿ったアクション実行\n"
        "**完了**: actions=[], complete=true（要求完全達成時のみ）\n\n"
        
        "```json\n"
        "{\n"
        '  "memory": "記録すべき情報（任意）",\n'
        '  "actions": [アクション配列],\n'
        '  "complete": true|false\n'
        "}\n"
        "```\n\n"
        
        "**アクション種類（14種のみ）:**\n"
        "- navigate: URL遷移\n"
        "- click: 要素クリック（CSS/XPath）\n"
        "- click_text: テキストクリック\n"
        "- type: テキスト入力\n"
        "- wait: 待機（ms指定）\n"
        "- scroll: スクロール（direction, amount）\n"
        "- go_back/go_forward: ナビゲーション\n"
        "- hover: ホバー\n"
        "- select_option: ドロップダウン選択\n"
        "- press_key: キー押下\n"
        "- wait_for_selector: 要素待機\n"
        "- extract_text: テキスト抽出\n"
        "- eval_js: JavaScript実行\n\n"
        
        "**安定実行のガイドライン:**\n"
        "- ページ遷移後は必ずwait(≥1000ms)挿入\n"
        "- クリック後の動的要素生成時は適切なwait使用\n"
        "- スクロールは400px以下で分割実行\n"
        "- 同一要素への連続click最大2回、変化なしで方針転換\n"
        "- 最大30アクション、ループ検知でcomplete:true\n"
        "- pointer-eventsエラー時: scroll→wait(300ms)→再click（1回のみ）\n\n"
        
        f"**制限事項:**\n"
        f"- 最大{MAX_STEPS}ステップ以内に完了\n"
        "- JSON以外の出力禁止（説明文は例外）\n"
        "- 定義外アクション・プロパティ禁止\n"
        "- 空文字列target、null値禁止\n\n"
        
        "--------------------------------\n"
        "## 現在のページのDOMツリー\n"
        f"{dom_text}\n"
        "--------------------------------\n"
        "## これまでの会話履歴\n"
        f"{past_conv}\n"
        "--------------------------------\n"
        "## ユーザー命令\n"
        f"{cmd}\n"
        "--------------------------------\n"
        "## 現在のブラウザの状況の画像\n"
        f"{add_img}\n"
        "## 現在のエラー状況\n"
        f"{error_line}"
    )

    #"---- 操作候補要素一覧 (操作対象は番号で指定 & この一覧にない要素の操作も可能 あくまで参考) ----\n"
    #f"{elem_lines}\n"
    #print(f"DOMツリー:{dom_text}")
    
    print(f"エラー:{error_line}")

    return system_prompt