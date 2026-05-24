# billing-link-detection

GCP 親請求先アカウント配下のサブアカウント・プロジェクトのリンク状態を日次で収集し、課金開始・0円プロジェクト・未課金プロジェクトを Slack に通知するシステム。

______________________________________________________________________

## アーキテクチャ概要

```
Cloud Scheduler（日次 02:00）
    → Cloud Run Jobs（Billing API 収集 + BigQuery MERGE）

Cloud Scheduler（アラートごと）
    → Cloud Functions（BigQuery クエリ → Slack 通知）

Cloud Logging → Cloud Monitoring
    → バッチ・Functions のエラーを Slack 通知
```

詳細は [`requirements.md`](./docs/requirements.md) を参照。

______________________________________________________________________

## ディレクトリ構成

```
billing-link-detection/
├── batch/                          # Cloud Run Jobs（データ収集バッチ）
│   ├── Dockerfile
│   ├── main.py
│   ├── requirements.in             # 直接依存（人間が編集）
│   └── requirements.txt            # ピン留め済み（uv pip compile で生成）
├── alert/                          # Cloud Functions（汎用アラートハンドラ）
│   ├── main.py
│   ├── requirements.in
│   ├── requirements.txt
│   └── alerts.yaml                 # アラート条件定義（人間が編集する唯一のファイル）
├── terraform/                      # インフラ定義
│   ├── backend.tf                  # GCS backend 設定（初回のみ手動作成）
│   ├── main.tf
│   ├── variables.tf
│   ├── terraform.tfvars            # 変数の実際の値（.gitignore 対象・Git 管理外）
│   └── terraform.tfvars.example
├── .github/
│   └── workflows/
│       └── deploy.yml              # CI/CD（GitHub Actions）
├── README.md                       # このファイル
├── requirements.md                 # 要件定義
├── alert_design.md                 # アラートシステム設計
├── initial_setup.md                # 初回セットアップ手順
├── decisions.md                    # 設計判断の記録
├── merge_sql_prototype.md          # MERGE SQL プロトタイプ
└── data_source_investigation.md    # Billing API / Export 調査結果
```

______________________________________________________________________

## セットアップ手順

### 前提条件

| ツール | バージョン目安 |
|---|---|
| gcloud CLI | 最新 |
| Terraform | 1.6+ |
| Python | 3.12+ |
| uv | 最新 |
| Docker | 最新 |

gcloud は親請求先アカウントの Billing Account Admin 権限を持つアカウントでログイン済みであること。

______________________________________________________________________

## ローカル開発環境のセットアップ

### 1. GCP 認証（Application Default Credentials）

ローカルで batch / Cloud Functions を動かす、または結合テストを GCP に対して実行するには ADC が必要：

```bash
gcloud auth application-default login
```

ブラウザで認可フローを完了すると `~/.config/gcloud/application_default_credentials.json` に保存され、GCP クライアントライブラリが自動的に使用する。

### 2. uv のインストールと依存パッケージのセットアップ

依存パッケージ管理には [uv](https://docs.astral.sh/uv/) を使用する。

```bash
# uv のインストール（未インストールの場合）
curl -LsSf https://astral.sh/uv/install.sh | sh

# batch の仮想環境を作成してパッケージをインストール
cd batch
uv venv
uv pip sync requirements.txt

# alert の仮想環境を作成してパッケージをインストール
cd ../alert
uv venv
uv pip sync requirements.txt
```

### パッケージバージョンを更新する

`requirements.in`（抽象バージョン指定）を編集した後、`uv pip compile` でピン留め済みの `requirements.txt` を再生成する。`requirements.txt` は手動で編集しない。

```bash
# batch のバージョンを更新
cd batch
uv pip compile requirements.in -o requirements.txt

# alert のバージョンを更新
cd ../alert
uv pip compile requirements.in -o requirements.txt
```

### ローカルでテストを実行する

```bash
# batch のユニットテスト
cd batch
uv run pytest

# alert のユニットテスト
cd ../alert
uv run pytest
```

結合テストは GCP に接続するため、Application Default Credentials が必要：

```bash
gcloud auth application-default login
uv run pytest tests/integration
```

### 環境変数の設定

`batch/.env.example` と `alert/.env.example` をコピーして使用する：

```bash
cd batch  # または alert
cp .env.example .env
# .env を編集して実際の値を入れる
source .env
```

> `.env` は `.gitignore` に含めて Git にコミットしない。`.env.example` のテンプレートでは BQ データセットを `billing_data_test` に固定しており、本番データセットの誤更新を防ぐ設計になっている。

### Cloud Functions をローカル起動する

`functions-framework` を使ってローカルで HTTP サーバーとして起動できる：

```bash
cd alert
source .env
uv run functions-framework --target=alert_handler --debug

# 別ターミナルから手動で叩く
curl -X POST http://localhost:8080 \
  -H "Content-Type: application/json" \
  -d '{
    "query": "SELECT sub_account_name, project_id FROM `{project}.{dataset}.billing_project_links` LIMIT 5",
    "channel": "#test-channel",
    "message": "ローカルテスト"
  }'
```

### バッチをローカル実行する

```bash
cd batch
source .env
uv run python main.py
```

______________________________________________________________________

### Step 1: GCP 手動セットアップ（terraform apply 前に必要）

`terraform apply` だけでは完結しない手動作業がある。
**[`initial_setup.md`](./docs/initial_setup.md) の Phase 1〜3 をすべて完了させてから** 次へ進む。

| Phase | 内容 | 目安時間 |
|---|---|---|
| Phase 1 | GCP プロジェクト作成・API 有効化 | 10 分 |
| Phase 2 | Terraform state バケット・SA 作成・WIF 設定 | 20 分 |
| Phase 3 | Cloud Billing Export 有効化・Slack Bot Token 取得・Secret 登録 | 15 分 |

______________________________________________________________________

### Step 2: リポジトリのクローンと変数設定

```bash
git clone <REPO_URL>
cd billing-link-detection

# 変数テンプレートをコピーして実際の値を埋める
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
```

`terraform/terraform.tfvars` を編集する（`.gitignore` に含まれているため Git にコミットされない）：

```hcl
project_id               = "your-project-id"
region                   = "asia-northeast1"
parent_billing_account   = "XXXXXX-YYYYYY-ZZZZZZ"
billing_export_dataset   = "billing_data"
billing_export_table     = "gcp_billing_export_v1_XXXXXX"
monitoring_slack_channel = "#alerts-gcp-billing"
```

> `billing_export_table` の値は Cloud Billing Export を有効化した後に GCP コンソールで確認できる。

______________________________________________________________________

### Step 3: アラート通知先チャンネルの確認

`alert/alerts.yaml` の `channel` フィールドを実際の Slack チャンネル名に変更する：

```yaml
# billing_newly_started（課金開始検知）→ 営業向けチャンネル
channel: "#sales-alerts"   # ← 実際のチャンネル名に変更

# zero_cost_projects / never_billed_projects → CS 向けチャンネル
channel: "#cs-alerts"      # ← 実際のチャンネル名に変更
```

______________________________________________________________________

### Step 4: Terraform でインフラ構築

```bash
cd terraform
terraform init
terraform plan   # 差分を確認
terraform apply
```

______________________________________________________________________

### Step 5: デプロイ後の仕上げ

[`initial_setup.md`](./docs/initial_setup.md) の **Phase 4** を実施する。

```bash
# Terraform が出力した SA のメールアドレスを確認
terraform output billing_collector_sa_email

# 親請求先アカウントに Billing Account Viewer を付与（組織管理者が実施）
gcloud billing accounts add-iam-policy-binding PARENT_BILLING_ACCOUNT_ID \
  --member="serviceAccount:<上記で確認した SA>" \
  --role="roles/billing.viewer"
```

______________________________________________________________________

### Step 6: 動作確認

| 確認項目 | 方法 |
|---|---|
| Cloud Billing Export にデータが入っている | BigQuery コンソールで `data_source_investigation.md` のクエリ (1) を実行 |
| データ収集バッチが正常終了する | Cloud Run Jobs を手動実行してログを確認 |
| `billing_project_links` にデータが入っている | BigQuery コンソールで `SELECT * FROM billing_project_links LIMIT 10` |
| Slack 通知が届く | アラートジョブを手動実行して確認 |
| Cloud Monitoring のアラートが設定されている | GCP コンソール → Monitoring → Alerting Policies を確認 |

```bash
# 日次バッチを手動実行する場合
gcloud run jobs execute billing-collector --region=asia-northeast1

# 月次バッチを手動実行する場合
gcloud run jobs execute billing-cost-updater --region=asia-northeast1

# アラートジョブを手動実行する場合
gcloud scheduler jobs run alert-billing_newly_started --location=asia-northeast1
```

______________________________________________________________________

## 日常の運用

### アラートを追加・変更する

`alert/alerts.yaml` を編集 → `terraform apply`。Function コードは触らない。

### アラートを一時停止・再開する

```bash
gcloud scheduler jobs pause  alert-<name> --location=asia-northeast1
gcloud scheduler jobs resume alert-<name> --location=asia-northeast1
```

### バッチを手動再実行する

バッチが失敗した場合は Cloud Monitoring でアラートが届く。原因調査後に手動で再実行する（冪等性があるため安全）：

```bash
# 日次バッチ
gcloud run jobs execute billing-collector --region=asia-northeast1

# 月次バッチ
gcloud run jobs execute billing-cost-updater --region=asia-northeast1
```

### アラート条件を変更する（SQL 変更）

`alert/alerts.yaml` の `query` フィールドを直接編集 → `terraform apply`。BigQuery コンソールで事前に SQL を動作確認してから変更することを推奨。

______________________________________________________________________

## ドキュメント一覧

| ファイル | 内容 |
|---|---|
| [`docs/requirements.md`](./docs/requirements.md) | 要件定義・テーブルスキーマ・処理フロー・テスト観点 |
| [`docs/alert_design.md`](./docs/alert_design.md) | アラートシステム設計・Function コード・Terraform 定義 |
| [`docs/initial_setup.md`](./docs/initial_setup.md) | 初回セットアップ手順（手動作業まとめ） |
| [`docs/decisions.md`](./docs/decisions.md) | 設計上の選択肢と採用・却下の記録 |
| [`docs/merge_sql_prototype.md`](./docs/merge_sql_prototype.md) | BigQuery MERGE SQL プロトタイプ（実装参照用） |
| [`docs/data_source_investigation.md`](./docs/data_source_investigation.md) | Billing API / Billing Export の調査結果・確認クエリ |
