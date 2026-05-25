# billing-link-detection

GCP 親請求先アカウント配下のサブアカウント・プロジェクトのリンク状態を **日次で収集** し、課金開始・0 円プロジェクト・未課金プロジェクトを **Slack に通知** するシステム。

> **目的別ナビゲーション** → [docs/INDEX.md](./docs/INDEX.md)
> 5 分で全体像を掴むのが目的なら、このページだけ読めば足りる。

______________________________________________________________________

## 1 分で分かるアーキテクチャ

```
[Cloud Scheduler 02:00] ─▶ [Cloud Run Job: billing-collector]
                                  │
                                  ├─▶ Cloud Billing API（リンク情報）
                                  └─▶ BigQuery MERGE
                                       └─ billing_project_links

[Cloud Scheduler 月次] ──▶ [Cloud Run Job: billing-cost-updater]
                                  ├─▶ Billing Export（前月の請求額）
                                  └─▶ BigQuery MERGE

[Cloud Scheduler アラート] ─▶ [Cloud Functions: alert-handler]
                                  ├─▶ BigQuery クエリ
                                  └─▶ Slack chat.postMessage

[Cloud Logging] ──▶ [Cloud Monitoring] ──▶ Slack（システムエラー）
```

詳細な Mermaid 図とデータフローは [docs/architecture.md](./docs/architecture.md)。

______________________________________________________________________

## 何を解決するシステムか

| 観点 | 内容 |
|---|---|
| **誰のため** | GCP 請求代行事業者の営業・CS |
| **何を可視化** | 顧客のプロジェクト追加・解除・課金開始など顧客行動シグナル |
| **どう届ける** | 役割別 Slack チャンネルへ自動通知（営業向け／CS 向け／システム向け） |
| **なぜ作る** | これまで手動把握だったため、変化検知のリードタイムが長かった |

事業背景の詳細: [docs/business_context.md](./docs/business_context.md)

______________________________________________________________________

## ディレクトリ構成

```
billing-link-detection/
├── batch/                   # Cloud Run Jobs（日次/月次バッチ）
│   ├── main.py              # BATCH_TYPE env で日次/月次を分岐
│   └── tests/               # pytest
├── alert/                   # Cloud Functions（汎用アラートハンドラ）
│   ├── main.py              # HTTP 受信 → BigQuery → Slack
│   ├── alerts.yaml          # 全アラート定義（編集する唯一のファイル）
│   └── tests/
├── terraform/               # インフラ定義（Cloud Run/Functions/Scheduler/BQ/IAM/Monitoring）
│   ├── main.tf
│   └── variables.tf
├── .github/workflows/
│   └── deploy.yml           # GitHub Actions（lint/test/plan/apply）
├── Makefile                 # 開発コマンド集約（make help で一覧）
├── pyproject.toml           # pytest 設定
└── docs/                    # 詳細ドキュメント（下記）
```

______________________________________________________________________

## ドキュメント一覧

| ドキュメント | こういう時に読む |
|---|---|
| [docs/INDEX.md](./docs/INDEX.md) | **「どの文書を読めばいいか分からない」** ときの逆引き |
| [docs/architecture.md](./docs/architecture.md) | システム構成・データフロー・状態遷移を **図で** 理解したい |
| [docs/glossary.md](./docs/glossary.md) | 「サブアカウント」「ever_billed」など独自用語の意味 |
| [docs/operations.md](./docs/operations.md) | 障害対応・バッチ再実行・アラート停止 |
| [docs/testing.md](./docs/testing.md) | テストを書く・実行する・追加する |
| [docs/initial_setup.md](./docs/initial_setup.md) | 本番環境を初めて構築する（Phase 1–4） |
| [docs/requirements.md](./docs/requirements.md) | 要件定義の詳細・テーブルスキーマ |
| [docs/alert_design.md](./docs/alert_design.md) | アラートシステム設計の詳細 |
| [docs/decisions.md](./docs/decisions.md) | 「なぜこの設計なのか」の選択肢比較 |
| [docs/constraints_and_flexibility.md](./docs/constraints_and_flexibility.md) | 自由度と制約（変更可能なもの／変えられないもの） |
| [docs/business_context.md](./docs/business_context.md) | ビジネス背景・データ活用目的 |
| [docs/data_source_investigation.md](./docs/data_source_investigation.md) | Billing API / Export の調査結果 |
| [docs/merge_sql_prototype.md](./docs/merge_sql_prototype.md) | MERGE SQL のプロトタイプ |

______________________________________________________________________

## クイックスタート

### ローカル開発（コードを動かしたい）

```bash
# 1. GCP 認証
gcloud auth application-default login

# 2. 依存パッケージ（batch + alert + dev を一括）
make install

# 3. テスト実行（GCP 接続不要）
make test

# 4. push 前のチェック（CI と同等の terraform fmt-check + validate）
make lint

# 5. バッチをローカル実行
cd batch
cp .env.example .env  # 値を埋める
source .env && uv run python main.py

# 6. Cloud Functions をローカル起動
cd alert
source .env
uv run functions-framework --target=alert_handler --debug
```

`make help` で全コマンド一覧。詳細とテスト設計は [docs/testing.md](./docs/testing.md)。

### 本番環境を初めて作る

**[docs/initial_setup.md](./docs/initial_setup.md) の Phase 1–4** を順に実施。

| Phase | 内容 | 目安 |
|---|---|---|
| 1 | GCP プロジェクト作成・API 有効化 | 10 分 |
| 2 | Terraform state バケット・SA・WIF | 20 分 |
| 3 | Billing Export 有効化・Slack Bot Token・Secret | 15 分 |
| 4 | デプロイ後の親アカウント権限付与・Cloud Monitoring 通知設定 | 10 分 |

### 日常運用

[docs/operations.md](./docs/operations.md) にまとまっている：

- バッチが失敗したときの調査フロー
- 通知が来ないときの切り分け
- アラートの一時停止・再開
- Slack チャンネル切り替え
- Docker イメージのロールバック

______________________________________________________________________

## 前提ツール

| ツール | バージョン |
|---|---|
| gcloud CLI | 最新 |
| Terraform | 1.6+ |
| Python | 3.12+ |
| uv | 最新 |
| Docker | 最新 |

gcloud は **親請求先アカウントの Billing Account Admin 権限** を持つアカウントでログイン済みであること。

______________________________________________________________________

## 何か変えたい時

- **アラート追加** → `alert/alerts.yaml` 編集 → `terraform apply`
- **通知先 Slack 変更** → `alert/alerts.yaml` の `channel` 編集 → `terraform apply`
- **バッチ実行頻度変更** → `terraform/main.tf` の Cloud Scheduler `schedule` 変更
- **アラート一時停止** → `gcloud scheduler jobs pause alert-<name>`
- **新しいテーブルカラム追加** → `terraform/main.tf` のスキーマ + `batch/main.py` の MERGE SQL を編集

詳しいプロシージャは [docs/operations.md](./docs/operations.md)。

______________________________________________________________________

## ライセンス / コントリビュート

社内システムのため一般公開なし。設計判断の経緯は [docs/decisions.md](./docs/decisions.md) を参照。
