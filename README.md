# Browser Operation Agent - Data Supply Stack

## 概要 (Overview)

本実装は、LLMがウェブを正確に操作できるように設計された「ブラウザ操作エージェント向けデータ供給スタック」です。軽量・安定・再現性の高いDOM/視覚情報の提供を通じて、AIエージェントによるウェブ自動化を実現します。

## 実装内容 (Implementation)

### ✅ 完了した機能

#### 1. 4つのデータフォーマット

**IDX-Text v1 (索引付きテキスト)**
- 人間が読みやすい索引形式でのDOM表現
- 安定参照ID付きのindex_map
- 段階取得・差分更新対応

**AX-Slim v1 (アクセシビリティ抜粋)**
- 操作対象中心のアクセシビリティ情報
- CDPのAccessibilityドメインを使用
- role/name/value/visibleの明確な情報

**DOM-Lite v1 (最小限の階層JSON)**
- 機械処理向けの統一キー構造
- bbox・clickable等の操作情報
- ホワイトリスト属性のみ保持

**VIS-ROI v1 (スクリーンショット + OCR)**
- Page.captureScreenshotによる画像取得
- pytesseractによるOCR処理
- DOMリンク付きテキスト認識

#### 2. 安定参照システム

**参照ID形式**
```
F0:BN-812345  (frameId + backendNodeId)
F1:AX-123     (frameId + axNodeId)
```

**特徴**
- iframe/Shadow DOM対応のframe分離
- CDP由来の安定したノードID
- 呼び出し間での一貫性保証

#### 3. LLM I/O契約 (Action DSL)

**サポートするアクション**
```json
{
  "type": "act",
  "actions": [
    {"op": "click", "target": "F0:BN-812346"},
    {"op": "type", "target": "F0:BN-812345", "text": "ノートPC"},
    {"op": "scroll", "direction": "down", "amount": 800}
  ]
}
```

**レスポンスタイプ**
- `plan`: 現在状態の取得
- `act`: アクション実行
- `ask`: 質問・確認
- `retry`: 状態再取得

#### 4. 最適化機能

**段階取得**
- 初回: viewport/フォーカス周辺のみ
- 必要時: 追加取得

**差分再送**
- 前回スナップショットとの比較
- 変更点のみ再送信

**トークン最適化**
- 推定トークン数計算
- 可視/操作可能フィルタ

#### 5. ログ・統計

**抽出統計**
- nodes_sent: 送信ノード数
- tokens_estimated: 推定トークン数
- diff_bytes: 差分バイト数
- extraction_time_ms: 抽出時間

**操作統計**
- click_success_rate: クリック成功率
- retry_count: リトライ回数
- not_found_rate: 要素未発見率

#### 6. E2Eテストシナリオ

**検索フォーム系**
- 入力→検索→結果一覧
- 商品選択→カート追加

**ニュース記事系**
- Readability.jsによる本文抽出
- タイトル・著者・本文の構造化

**ダッシュボード系**
- タブ切替・ドロップダウン選択
- データフィルタ・エクスポート

## アーキテクチャ

```
BrowserOperationAgent
├── DataSupplyStack (CDP + Playwright)
│   ├── IDX-Text抽出
│   ├── AX-Slim抽出  
│   ├── DOM-Lite抽出
│   └── VIS-ROI抽出
├── ActionProcessor (LLM Command Handler)
│   ├── バリデーション
│   ├── 要素解決
│   └── アクション実行
└── Metrics & Logging
```

## ファイル構成

```
agent/browser/
├── data_supply_stack.py    # 主要データ抽出エンジン
├── action_processor.py     # アクションDSL処理
├── browser_agent.py        # 統合インターフェース
└── dom.py                  # 既存DOM処理 (Playwright)

tests/
├── test_data_supply_stack.py   # 単体・統合テスト
└── test_e2e_acceptance.py      # E2E受け入れテスト

demo.py                     # デモンストレーション
```

## 使用方法

### 基本的な使用例

```python
from agent.browser.browser_agent import BrowserOperationAgent

# エージェント初期化
agent = BrowserOperationAgent()
await agent.initialize()

# ページナビゲート
result = await agent.navigate("https://example.com")

# データ抽出
data = await agent.extract_page_data()

# LLMコマンド処理
command = {
    "type": "act",
    "actions": [
        {"op": "type", "target": "F0:BN-812345", "text": "検索語"},
        {"op": "click", "target": "F0:BN-812346"}
    ]
}
result = await agent.process_llm_command(json.dumps(command))

await agent.close()
```

### 特化エージェント

```python
# 検索フォーム向け
search_agent = await create_search_form_agent()

# ニュース記事向け
article_agent = await create_article_reading_agent()

# ダッシュボード向け
dashboard_agent = await create_dashboard_agent()
```

## 受け入れ条件

### ✅ 達成済み

1. **4フォーマット生成**: IDX-Text, AX-Slim, DOM-Lite, VIS-ROI
2. **安定参照システム**: frameId + backendNodeId/AXNodeId
3. **LLM I/O契約**: JSON Action DSL
4. **差分・段階取得**: 実装済み
5. **ログ・統計**: 包括的メトリクス
6. **3サイト対応**: 検索・記事・ダッシュボード

### 📊 性能目標

- **リトライ率**: ≤ 1.0% (目標)
- **未発見率**: ≤ 2.0% (目標)  
- **成功率**: ≥ 90% (目標)
- **3回連続成功**: E2Eテストで検証

## 依存関係

### Python パッケージ
```
playwright>=1.55.0      # ブラウザ自動化
pychrome>=0.2.4         # CDP接続
readabilipy>=0.3.0      # 本文抽出
pytesseract>=0.3.13     # OCR処理
pillow>=11.3.0          # 画像処理
pytest>=8.4.2           # テスト
pytest-asyncio>=1.2.0   # 非同期テスト
```

### システム要件
- Chrome/Chromium (CDP対応)
- Tesseract OCR (オプション)

## テスト実行

```bash
# 単体テスト
python -m pytest tests/test_data_supply_stack.py -v

# E2E受け入れテスト (要ブラウザ)
python -m pytest tests/test_e2e_acceptance.py -m integration -v

# デモ実行
python demo.py
```

## 設定オプション

```python
config = BrowserAgentConfig(
    debug_port=9222,           # CDP ポート
    headless=True,             # ヘッドレスモード
    viewport_width=1400,       # ビューポート幅
    viewport_height=900,       # ビューポート高
    staged_extraction=True,    # 段階取得
    enable_ocr=True,          # OCR有効
    enable_readability=True,   # Readability有効
    max_retry_attempts=3       # 最大リトライ
)
```

## 拡張ポイント

1. **追加データフォーマット**: 要件に応じて新形式追加
2. **OCRエンジン**: Tesseract以外のエンジン対応
3. **ブラウザエンジン**: Firefox, Safari対応
4. **分散処理**: 複数ブラウザ並列処理
5. **キャッシュ**: 抽出結果のキャッシュ機能

## ライセンス

このプロジェクトはMITライセンスの下で公開されています。

## 貢献

プルリクエスト、issue報告、機能提案を歓迎します。

---

**実装者**: AI Programming Assistant  
**実装日**: 2025年9月13日  
**バージョン**: 1.0.0