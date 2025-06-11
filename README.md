# Web Agent Playground

このリポジトリは、AI エージェントによるブラウザ操作を実験するためのシンプルな環境です。`docker-compose` を利用してブラウザ(VNC) と Web アプリケーションを構築します。

## 構成

```
./docker-compose.yml          # サービス定義
./vnc/                        # Playwright + VNC 用コンテナ
./web/                        # Flask 製 Web アプリ
```

- **vnc**: Chromium を Playwright で制御し、noVNC 経由で画面操作を行うコンテナです。`automation_server.py` がブラウザ操作 API (ポート 7000) を提供します。
- **web**: チャット UI と自動化エージェントを提供する Flask アプリです。Gemini または Groq API と連携し、指示に応じてブラウザ操作 DSL を生成します。

## 事前準備

Gemini/Groq の API キーを環境変数として指定します。`docker-compose.yml` では例として下記の変数が定義されています。

```
GEMINI_API_KEY=<Your Gemini API Key>
GROQ_API_KEY=<Your Groq API Key>
```

任意で `GEMINI_MODEL` や `GROQ_MODEL` を指定することもできます。

## 実行方法

1. Docker と docker-compose が利用可能な環境で以下を実行します。

   ```bash
   docker-compose build
   docker-compose up
   ```

2. ブラウザで `http://localhost:5000` を開くとチャット UI が表示されます。noVNC 画面は `http://localhost:6901` からも参照可能です。

## 参考

- `vnc/automation_server.py` — Playwright 経由でブラウザ操作を実行するサーバー
- `web/app.py` — LLM からの指示を受けて DSL 生成や履歴管理を行う Flask アプリ

Playwright の API を直接呼び出すのではなく、LLM で生成した JSON DSL を `automation_server.py` に転送することでブラウザ操作を行う設計になっています。詳細なディレクトリ構成は `Structure.txt` も参照してください。

## 変更点

vnc 側での操作失敗により処理が止まることが多かったため、UI 側の実行ロジックを
改善しました。各アクション実行後に Playwright サーバーから返された最新 HTML を
次の LLM 呼び出しに利用することで、ページ変化を逐次反映しながらループ処理を継続
します。これによりエラー発生時もブラウザを立て直しつつ動作を続けやすくなりました。

さらにクリック動作で要素が覆い隠されている場合に備え、数回失敗したら`force`オプション
を使って強制クリックを試みるよう `automation_server.py` を改良しています。

