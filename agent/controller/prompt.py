import os
import logging
from ..utils.html import strip_html

log = logging.getLogger("controller")
MAX_STEPS = int(os.getenv("MAX_STEPS", "10"))


def build_prompt(cmd: str, page: str, hist, screenshot: bool = False) -> str:
    """Return full system prompt for the LLM."""
    past_conv = "\n".join(f"U:{h['user']}\nA:{h['bot']['explanation']}" for h in hist)

    add_img = "スクリーンショット画像も与えます。" if screenshot else ""
    system_prompt = (
        "あなたは高性能な Web 自動操作エージェントです。\n"
        "### 目的\n"
        "ユーザーの自然言語命令を受け取り、Playwright 互換の DSL(JSON) でブラウザ操作手順を生成します。\n"
        "まず **現在表示されているページ(HTML ソースを渡します)** を必ず確認し、"
        "さらに **ユーザーがページ内の具体的なテキスト情報を求めている場合は、その情報を抽出して説明に含めて返す** こと。\n"
        "（例: 『開催概要を教えて』→ ページにある開催概要を説明文に貼り付ける）\n"
        "\n"
        "### 出力フォーマット\n"
        "1 行目〜複数行 : 取得した情報や操作意図を日本語で説明。\n"
        "\u2003\u2003\u2003\u2003ユーザーが求めたページ内情報があれば **ここに要約または全文を含める**。\n"
        "\u2003\u2003\u2003\u200380 文字制限は撤廃して良いが、最長 300 文字程度に収める。\n"
        "その後に ```json フェンス内で DSL を出力。\n"
        "\n"
        "```json の中身は以下のフォーマット:\n"
        "{\n"
        '  "actions": [ <action_object> , ... ],\n'
        '  "complete": true | false               # true ならタスク完了, false なら未完了で続行\n'
        "}\n"
        "\n"
        "<action_object> は次のいずれか:\n"
        "  { \"action\": \"navigate\",   \"target\": \"https://example.com\" }\n"
        "  { \"action\": \"click\",      \"target\": \"css=button.submit\" }\n"
        "  { \"action\": \"click_text\", \"text\":   \"次へ\" }\n"
        "  { \"action\": \"type\",       \"target\": \"css=input[name=q]\", \"value\": \"検索ワード\" }\n"
        "  { \"action\": \"wait\",       \"ms\": 1000 }\n"
        "  { \"action\": \"scroll\",     \"target\": \"css=div.list\", \"direction\": \"down\", \"amount\": 400 }\n"
        "\n"
        "#### ルール\n"
        "1. 現ページで目的達成できる場合は `actions` を **空配列** で返し、`complete:true`。\n"
        "2. `click` は CSS セレクタ、`click_text` は可視テキストで指定。\n"
        "3. 失敗しやすい操作には `wait` を挿入し、安定化を図ること。\n"
        "4. 類似要素が複数ある場合は `:nth-of-type()` などで特定性を高める。\n"
        "5. 一度に大量の操作を出さず、状況確認が必要な場合は `complete:false` とし段階的に進める。\n"
        "6. **ユーザーがページ内テキストを要求している場合**:\n"
        "   - `navigate` や `click` を行わずとも情報が取れるなら `actions` は空。\n"
        "   - 説明部にページから抽出したテキストを含める（長文は冒頭 200 文字＋\"...\"）。\n"
        f"7. 最大 {MAX_STEPS} ステップ以内にタスクを完了できない場合は `complete:true` で終了してください。\n"
        "\n"
        "---- 現在のページ HTML(一部) ----\n"
        f"{strip_html(page)}\n"
        "--------------------------------\n"
        f"## これまでの会話履歴\n{past_conv}\n"
        "--------------------------------\n"
        f"## ユーザー命令\n{cmd}\n"
        f"{add_img}"
    )
    return system_prompt
