"""
Example usage of the browser automation data supply stack.

This example shows how to integrate the new data supply system 
with the existing automation infrastructure.
"""

import sys
from pathlib import Path
import json
import logging
from typing import Dict, Any

sys.path.append(str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def create_sample_test_page() -> str:
    """Create a sample HTML page for testing."""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>商品検索 - Example Store</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body>
        <div id="main" class="container" aria-label="検索フォーム">
            <h1>商品検索</h1>
            <form id="search-form">
                <label for="query">商品を検索:</label>
                <input id="query" type="text" placeholder="キーワードを入力" value="" role="textbox" aria-label="検索キーワード">
                <button id="search-btn" type="submit" role="button">検索</button>
            </form>
            <div class="links">
                <a href="/cart" role="link">カートを見る</a>
                <a href="/account" role="link">アカウント</a>
            </div>
            <div id="results" style="display:none;">
                <h2>検索結果</h2>
                <div class="result-item">
                    <h3>ノートPC - プレミアムモデル</h3>
                    <p>高性能CPU搭載、メモリ16GB</p>
                    <span class="price">¥120,000</span>
                    <button class="add-to-cart">カートに追加</button>
                </div>
                <div class="result-item">
                    <h3>ノートPC - スタンダードモデル</h3>
                    <p>日常使いに最適、メモリ8GB</p>
                    <span class="price">¥80,000</span>
                    <button class="add-to-cart">カートに追加</button>
                </div>
            </div>
        </div>
        
        <script>
        document.getElementById('search-form').addEventListener('submit', function(e) {
            e.preventDefault();
            const query = document.getElementById('query').value;
            if (query.trim()) {
                document.getElementById('results').style.display = 'block';
            }
        });
        </script>
    </body>
    </html>
    """


def demonstrate_data_formats():
    """Demonstrate the 4 data formats with expected outputs."""
    
    print("\n" + "="*80)
    print("BROWSER AUTOMATION DATA SUPPLY STACK - FORMAT EXAMPLES")
    print("="*80)
    
    # IDX-Text v1 format example
    print("\n1. IDX-Text v1 Format:")
    print("-" * 40)
    idx_text_example = {
        "meta": {"viewport": [0, 0, 1400, 900], "ts": "2025-01-15T10:30:00Z"},
        "text": """# viewport: [0,0,1400,900]
[0] <div id="main" class="container" aria-label="検索フォーム">
  [1] <h1 text="商品検索">
  [2] <input id="query" role="textbox" placeholder="キーワードを入力" value="">
  [3] <button id="search-btn" role="button" text="検索">
  [4] <a role="link" text="カートを見る">
  [5] <a role="link" text="アカウント">
</div>""",
        "index_map": {
            "2": {"frameId": "F0", "backendNodeId": 812345, "css": "#query"},
            "3": {"frameId": "F0", "backendNodeId": 812346, "css": "#search-btn"},
            "4": {"frameId": "F0", "backendNodeId": 812347, "css": "a[href='/cart']"},
            "5": {"frameId": "F0", "backendNodeId": 812348, "css": "a[href='/account']"}
        }
    }
    print(json.dumps(idx_text_example, indent=2, ensure_ascii=False))
    
    # AX-Slim v1 format example
    print("\n\n2. AX-Slim v1 Format:")
    print("-" * 40)
    ax_slim_example = {
        "root_name": "商品検索 - Example Store",
        "ax_nodes": [
            {
                "axId": "AX-10",
                "role": "textbox",
                "name": "検索キーワード",
                "value": "",
                "backendNodeId": 812345,
                "visible": True,
                "bbox": [320, 180, 760, 210]
            },
            {
                "axId": "AX-11", 
                "role": "button",
                "name": "検索",
                "value": "",
                "backendNodeId": 812346,
                "visible": True,
                "bbox": [760, 180, 840, 210]
            },
            {
                "axId": "AX-12",
                "role": "link", 
                "name": "カートを見る",
                "value": "",
                "backendNodeId": 812347,
                "visible": True,
                "bbox": [100, 250, 200, 280]
            }
        ]
    }
    print(json.dumps(ax_slim_example, indent=2, ensure_ascii=False))
    
    # DOM-Lite v1 format example
    print("\n\n3. DOM-Lite v1 Format:")
    print("-" * 40)
    dom_lite_example = {
        "ver": "1.0",
        "frame": "F0",
        "nodes": [
            {
                "id": "N2",
                "tag": "input",
                "role": "textbox",
                "attrs": {"id": "query", "placeholder": "キーワードを入力", "aria-label": "検索キーワード"},
                "text": "",
                "bbox": [320, 180, 760, 210],
                "clickable": False,
                "backendNodeId": 812345
            },
            {
                "id": "N3",
                "tag": "button", 
                "role": "button",
                "attrs": {"id": "search-btn", "type": "submit"},
                "text": "検索",
                "bbox": [760, 180, 840, 210],
                "clickable": True,
                "backendNodeId": 812346
            },
            {
                "id": "N4",
                "tag": "a",
                "role": "link",
                "attrs": {"href": "/cart"},
                "text": "カートを見る", 
                "bbox": [100, 250, 200, 280],
                "clickable": True,
                "backendNodeId": 812347
            }
        ]
    }
    print(json.dumps(dom_lite_example, indent=2, ensure_ascii=False))
    
    # VIS-ROI v1 format example
    print("\n\n4. VIS-ROI v1 Format:")
    print("-" * 40)
    vis_roi_example = {
        "image": {"id": "S-20250115-103000", "format": "png", "byte_len": 245678},
        "ocr": [
            {
                "text": "商品検索",
                "bbox": [100, 50, 200, 80],
                "conf": 0.99,
                "link_backend_node_id": None
            },
            {
                "text": "検索",
                "bbox": [760, 180, 820, 210], 
                "conf": 0.98,
                "link_backend_node_id": 812346
            },
            {
                "text": "カートを見る",
                "bbox": [100, 250, 180, 280],
                "conf": 0.97,
                "link_backend_node_id": 812347
            }
        ]
    }
    print(json.dumps(vis_roi_example, indent=2, ensure_ascii=False))


def demonstrate_action_dsl():
    """Demonstrate action DSL examples."""
    
    print("\n\n" + "="*80)
    print("ACTION DSL EXAMPLES")
    print("="*80)
    
    # LLM Plan example
    print("\n1. Plan Action:")
    print("-" * 40)
    plan_example = {
        "type": "plan",
        "plan": [
            "1. 商品検索ページで検索フィールドを特定する",
            "2. 検索キーワード「ノートPC」を入力する", 
            "3. 検索ボタンをクリックして検索を実行する",
            "4. 検索結果が表示されることを確認する"
        ],
        "message": "商品検索タスクの実行計画"
    }
    print(json.dumps(plan_example, indent=2, ensure_ascii=False))
    
    # LLM Action execution example
    print("\n\n2. Action Execution:")
    print("-" * 40)
    action_example = {
        "type": "act",
        "actions": [
            {"op": "click", "target": "F0:BN-812345"},
            {"op": "type", "target": "F0:BN-812345", "text": "ノートPC"},
            {"op": "click", "target": "F0:BN-812346"}
        ]
    }
    print(json.dumps(action_example, indent=2, ensure_ascii=False))
    
    # LLM Ask example  
    print("\n\n3. Ask for Clarification:")
    print("-" * 40)
    ask_example = {
        "type": "ask",
        "question": "複数の検索結果が表示されました。どちらの商品を選択しますか？",
        "context": {
            "options": [
                "ノートPC - プレミアムモデル (¥120,000)",
                "ノートPC - スタンダードモデル (¥80,000)"
            ]
        }
    }
    print(json.dumps(ask_example, indent=2, ensure_ascii=False))
    
    # LLM Retry example
    print("\n\n4. Retry Request:")
    print("-" * 40) 
    retry_example = {
        "type": "retry",
        "message": "検索ボタンが見つかりませんでした。ページの最新状態を取得してください。"
    }
    print(json.dumps(retry_example, indent=2, ensure_ascii=False))


def demonstrate_stable_references():
    """Demonstrate stable reference system."""
    
    print("\n\n" + "="*80)
    print("STABLE REFERENCE SYSTEM")
    print("="*80)
    
    print("\n安定参照形式:")
    print("-" * 40)
    
    examples = [
        {
            "format": "frameId:BN-backendNodeId",
            "example": "F0:BN-812345",
            "description": "CDP backendNodeId による安定参照"
        },
        {
            "format": "frameId:AX-axNodeId", 
            "example": "F0:AX-67890",
            "description": "CDP Accessibility AXNodeId による参照"
        },
        {
            "format": "frameId:CSS-selector",
            "example": "F0:CSS-#search-btn",
            "description": "CSS セレクターによるフォールバック参照"
        }
    ]
    
    for example in examples:
        print(f"• {example['format']}")
        print(f"  例: {example['example']}")
        print(f"  説明: {example['description']}\n")
    
    print("利点:")
    print("- フレーム境界を跨いだ要素参照")
    print("- DOM変更に対する耐性")
    print("- LLMからの明確な要素指定")
    print("- 操作可能性の自動検証")


def demonstrate_metrics():
    """Demonstrate metrics and monitoring."""
    
    print("\n\n" + "="*80)
    print("METRICS AND MONITORING")
    print("="*80)
    
    print("\n抽出メトリクス:")
    print("-" * 40)
    extraction_metrics = {
        "nodes_sent": 25,
        "tokens_estimated": 1250,
        "diff_bytes": 340,
        "roi_hits": 8,
        "extraction_time_ms": 450
    }
    print(json.dumps(extraction_metrics, indent=2))
    
    print("\n\n実行統計:")
    print("-" * 40)
    execution_stats = {
        "click_success_rate": 0.92,
        "retry_count": 3,
        "not_found_rate": 0.08,
        "total_executions": 50,
        "successful_executions": 46
    }
    print(json.dumps(execution_stats, indent=2))
    
    print("\n\nトークン最適化効果:")
    print("-" * 40)
    print("• 段階取得: 初回viewport中心 → 必要時追加取得")
    print("• 差分更新: 変更箇所のみ再送信 (340バイト vs 全体12KB)")
    print("• フィルタリング: 可視・操作可能要素のみ抽出")
    print("• 本文抽出: Readability使用でノイズ除去")


def main():
    """Main demonstration function."""
    print("Browser Automation Data Supply Stack - Implementation Example")
    print("=" * 80)
    print("\nこの実装は以下の要件を満たします:")
    print("✓ 4つのデータフォーマット (IDX-Text, AX-Slim, DOM-Lite, VIS-ROI)")
    print("✓ CDP ベースの安定参照システム")
    print("✓ アクション DSL バリデーション")
    print("✓ OCR とコンテンツ抽出の統合")
    print("✓ 差分更新とトークン最適化")
    print("✓ 包括的なエラーハンドリング")
    print("✓ メトリクスとロギング")
    
    # Demonstrate each component
    demonstrate_data_formats()
    demonstrate_action_dsl()
    demonstrate_stable_references()
    demonstrate_metrics()
    
    print("\n\n" + "="*80)
    print("INTEGRATION WITH EXISTING AUTOMATION SERVER")
    print("="*80)
    
    print("\n新しいAPIエンドポイント:")
    print("-" * 40)
    endpoints = [
        "POST /api/data-supply/initialize - データ供給システム初期化",
        "GET  /api/data-supply/idx-text - IDX-Text v1 フォーマット取得",
        "GET  /api/data-supply/ax-slim - AX-Slim v1 フォーマット取得",
        "GET  /api/data-supply/dom-lite - DOM-Lite v1 フォーマット取得",
        "GET  /api/data-supply/vis-roi - VIS-ROI v1 フォーマット取得",
        "GET  /api/data-supply/all-formats - 全フォーマット一括取得",
        "POST /api/data-supply/action-dsl - アクション DSL 処理",
        "POST /api/data-supply/validate-target - ターゲット検証",
        "GET  /api/data-supply/metrics - メトリクス取得",
        "GET  /api/data-supply/status - システム状態確認"
    ]
    
    for endpoint in endpoints:
        print(f"• {endpoint}")
    
    print("\n\n既存システムとの統合:")
    print("-" * 40)
    print("• 既存の automation_server.py と並行動作")
    print("• playwright ページインスタンスを共有")
    print("• 既存の DSL アクションと互換性維持")
    print("• 段階的な移行が可能")
    
    print("\n\nE2E テスト対応:")
    print("-" * 40)
    print("• 検索フォーム系: 入力→検索→結果確認")
    print("• ニュース記事系: Readability コンテンツ抽出")
    print("• ダッシュボード系: タブ切替・ドロップダウン・スクロール")
    print("• 安定性: 平均リトライ ≤ 1.0, not_found_rate ≤ 2%")
    
    print(f"\n{'='*80}")
    print("実装完了 - ブラウザ操作エージェント向けデータ供給スタック")
    print(f"{'='*80}")
    
    return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)