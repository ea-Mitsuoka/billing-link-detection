# 初回セットアップ手順

`terraform apply` だけでは構築できない手動作業を整理する。\
以下の Phase 1〜3 を完了してから Terraform を実行する。Phase 4 はデプロイ後でよい。

______________________________________________________________________

## 作業フロー概要

```plaintext
Phase 1: GCPプロジェクトの作成・基盤整備
    ↓
Phase 2: Terraform実行基盤の整備（stateバケット・SAの作成）
    ↓
Phase 3: 外部連携の準備（Billing Export・Slack）
    ↓
terraform init / plan / apply
    ↓
Phase 4: デプロイ後の仕上げ（親アカウント権限付与・動作確認）
```

______________________________________________________________________

## Phase 1: GCPプロジェクトの作成・基盤整備

### 1-1. プロジェクトの作成と請求先リンク

```bash
# プロジェクト作成
gcloud projects create PROJECT_ID --name="billing-link-detection"
gcloud config set project PROJECT_ID

# このシステム自体の運用費用を負担するサブアカウントとリンク
gcloud billing projects link PROJECT_ID \
  --billing-account=BILLING_ACCOUNT_ID
```

> `BILLING_ACCOUNT_ID` はこのシステムの運用費用を払うサブアカウントのID（監視対象の顧客アカウントとは別）。

### 1-2. 必要なAPIの有効化

```bash
gcloud services enable \
  cloudbilling.googleapis.com \
  cloudresourcemanager.googleapis.com \
  run.googleapis.com \
  cloudfunctions.googleapis.com \
  cloudscheduler.googleapis.com \
  bigquery.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com \
  logging.googleapis.com \
  monitoring.googleapis.com \
  iam.googleapis.com \
  storage.googleapis.com
```

______________________________________________________________________

## Phase 2: Terraform実行基盤の整備

Terraform自身をTerraformで管理できないため、state管理バケットとTerraform実行用SAは手動で作成する。

### 2-1. Terraform state用GCSバケットの作成

```bash
BUCKET_NAME="PROJECT_ID-tfstate"

gsutil mb -p PROJECT_ID -l asia-northeast1 gs://${BUCKET_NAME}

# 誤削除防止・state履歴管理のためバージョニングを有効化
gsutil versioning set on gs://${BUCKET_NAME}
```

### 2-2. Terraform実行用サービスアカウントの作成

```bash
gcloud iam service-accounts create sa-terraform \
  --display-name="Terraform Executor" \
  --project=PROJECT_ID
```

### 2-3. 必要なロールの付与

```bash
SA_EMAIL="sa-terraform@PROJECT_ID.iam.gserviceaccount.com"

for ROLE in \
  roles/iam.serviceAccountAdmin \
  roles/resourcemanager.projectIamAdmin \
  roles/run.admin \
  roles/cloudfunctions.admin \
  roles/cloudscheduler.admin \
  roles/bigquery.admin \
  roles/artifactregistry.admin \
  roles/secretmanager.admin \
  roles/storage.admin \
  roles/logging.admin \
  roles/monitoring.admin \
  roles/cloudbuild.builds.editor \
  roles/iam.serviceAccountTokenCreator; do
  gcloud projects add-iam-policy-binding PROJECT_ID \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="${ROLE}"
done
```

### 2-4. Terraform backend設定

`terraform/backend.tf` に以下を記載する（`terraform init` 前に必要）。

```hcl
terraform {
  backend "gcs" {
    # bucket は terraform init 時に -backend-config フラグで指定する（部分的初期化）
    # 理由: backend ブロックは変数展開できないため、プロジェクトIDをハードコードしたくない
    prefix = "terraform/state"
  }
}
```

`terraform init` 実行時にバケット名を指定：

```bash
terraform init -backend-config="bucket=${PROJECT_ID}-tfstate"
```

> CI/CD（GitHub Actions）でも同様に `-backend-config` 経由で渡す。`deploy.yml` の `terraform init` ステップが該当。

### 2-5. GitHub Actions からの Terraform 実行認証

CI/CD は **GitHub Actions** を採用する。Workload Identity Federation（WIF）を設定し、GitHub Actions が `sa-terraform` をImpersonateして GCP リソースを操作できるようにする。

```bash
# Workload Identity Pool の作成
gcloud iam workload-identity-pools create "github-pool" \
  --project=PROJECT_ID \
  --location="global" \
  --display-name="GitHub Actions Pool"

# Provider の作成
gcloud iam workload-identity-pools providers create-oidc "github-provider" \
  --project=PROJECT_ID \
  --location="global" \
  --workload-identity-pool="github-pool" \
  --display-name="GitHub Actions Provider" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --issuer-uri="https://token.actions.githubusercontent.com"

# PROJECT_NUMBER（数値ID。PROJECT_ID とは別物）を取得
PROJECT_NUMBER=$(gcloud projects describe PROJECT_ID --format="value(projectNumber)")

# SA への Impersonation 権限付与（ORG/REPO は実際のリポジトリ名に変更）
gcloud iam service-accounts add-iam-policy-binding ${SA_EMAIL} \
  --project=PROJECT_ID \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github-pool/attribute.repository/ORG/REPO"
```

> WIF はキーファイルの発行・管理が不要なため、サービスアカウントキーの漏洩リスクを排除できる。設定は初回のみ手動で行い、以降は Terraform で管理可能。

### 2-6. GitHub Variables / Secrets の登録

`.github/workflows/deploy.yml` から参照する値を GitHub リポジトリの Settings → Secrets and variables → Actions で登録する。

**Variables（非機密・コード上に出てよい値）**

| 名前 | 値の例 | 用途 |
|---|---|---|
| `GCP_PROJECT_ID` | `your-project-id` | Terraform / Docker 認証先 |
| `BILLING_EXPORT_DATASET` | `billing_data` | Billing Export のデータセット名（terraform.tfvars と同じ値） |
| `BILLING_EXPORT_TABLE` | `gcp_billing_export_v1_XXXXXX` | Billing Export のテーブル名 |
| `MONITORING_SLACK_CHANNEL` | `#alerts-gcp-billing` | Cloud Monitoring のシステムエラー通知先 |

**Secrets（外部に漏らしたくない値）**

| 名前 | 値の例 | 用途 |
|---|---|---|
| `PARENT_BILLING_ACCOUNT` | `XXXXXX-YYYYYY-ZZZZZZ` | 親請求先アカウントID |
| `WIF_PROVIDER` | `projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github-pool/providers/github-provider` | 2-5 で作成した WIF Provider のフルパス |
| `TERRAFORM_SA_EMAIL` | `sa-terraform@PROJECT_ID.iam.gserviceaccount.com` | 2-2 で作成した Terraform 実行用 SA |

> GitHub Variables と Secrets の使い分け：Slack チャンネル名・データセット名などはログ・PR コメントに表示されても問題ないので Variables、親請求先アカウントIDや WIF Provider パスのように漏洩リスクがあるものは Secrets を使う。

______________________________________________________________________

## Phase 3: 外部連携の準備

### 3-1. Cloud Billing Export の有効化（GUIのみ・API不可）

1. GCPコンソール → 「お支払い」→「請求データのエクスポート」を開く
1. **親請求先アカウント**を選択した状態で「BigQueryへのエクスポートを編集」をクリック
1. 以下を設定して保存する

| 項目 | 設定値 |
|---|---|
| プロジェクト | PROJECT_ID（このシステムのプロジェクト） |
| データセット | `billing_data`（`billing_project_links` テーブルと同じデータセット。terraform.tfvars の `billing_export_dataset` の値と一致させる） |
| エクスポート種類 | 標準使用量のコスト |

4. エクスポートテーブル名（`gcp_billing_export_v1_XXXXXX`）を控えておく\
   → Terraform変数・バッチコードに設定が必要

> **注意**: エクスポートは設定した時点以降のデータしか蓄積されない。過去データは遡及されない。\
> また、データ反映まで最大24時間かかる場合がある。

### 3-2. Slack App の作成とBot Token取得

1. [api.slack.com/apps](https://api.slack.com/apps) → 「Create New App」→「From scratch」
1. App Name・ワークスペースを設定
1. 「OAuth & Permissions」→「Bot Token Scopes」に以下を追加
   - `chat:write`
   - `chat:write.public`（パブリックチャンネルにBotを招待せず投稿する場合）
1. 「Install to Workspace」→ Bot User OAuth Token（`xoxb-...`）を取得

### 3-3. Bot Token を Secret Manager に登録

```bash
# Secret の作成とトークンの登録
echo -n "xoxb-YOUR-BOT-TOKEN" | \
  gcloud secrets create slack-bot-token \
    --data-file=- \
    --project=PROJECT_ID

# 登録確認
gcloud secrets versions access latest --secret=slack-bot-token --project=PROJECT_ID
```

______________________________________________________________________

## Terraform 実行

Phase 1〜3 が完了したら Terraform を実行する。

```bash
terraform init
terraform plan
terraform apply
```

______________________________________________________________________

## Phase 4: デプロイ後の仕上げ

### 4-1. 親請求先アカウントへのアクセス権限付与

データ収集バッチのSA（Terraformが作成）に、親請求先アカウントの閲覧権限を付与する。\
**これはGCP組織管理者が行う作業であり、プロジェクト管理者では設定できない場合がある。**

```bash
# Terraformが出力するSAのメールアドレスを確認
terraform output billing_collector_sa_email

# 親請求先アカウントに Billing Account Viewer を付与
gcloud billing accounts add-iam-policy-binding PARENT_BILLING_ACCOUNT_ID \
  --member="serviceAccount:sa-billing-collector@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/billing.viewer"
```

### 4-2. Cloud Monitoring の Slack 通知チャンネル作成（手動）

Cloud Monitoring から Slack に通知するには、別途 **Google Cloud Monitoring の Slack App** をワークスペースにインストールする必要がある（API では提供されないため手動セットアップ必須）。

1. GCP コンソール → Monitoring → Alerting → Notification Channels
1. 「ADD NEW」→ Slack → Slack ワークスペースで認可
1. 通知先チャンネル（`monitoring_slack_channel` で指定したもの。例: `#alerts-gcp-billing`）を選択
1. 表示名を保存（例: `Slack - alerts-gcp-billing`）→ この名前を Terraform の `data "google_monitoring_notification_channel"` で参照する

詳細は `alert_design.md` Section 9「Slack 通知チャンネルの設定」を参照。

### 4-3. 動作確認チェックリスト

| 確認項目 | 確認方法 |
|---|---|
| Cloud Billing Export にデータが入っている | BQコンソールで `data_source_investigation.md` のクエリ(1)を実行 |
| データ収集バッチが正常終了する | Cloud Run Jobs を手動実行してログを確認 |
| `billing_project_links` にデータが入っている | BQコンソールでSELECTして確認 |
| Slack通知が届く | アラートジョブを手動実行して確認 |
| Cloud Monitoringのアラートが設定されている | コンソールで Alerting Policies を確認 |

______________________________________________________________________

## リポジトリ構成

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
│   ├── backend.tf                  # GCS backend 設定（2-4 で手動作成）
│   ├── main.tf
│   ├── variables.tf
│   ├── terraform.tfvars            # 変数の実際の値（Git管理対象外）
│   └── terraform.tfvars.example    # 変数テンプレート（Git管理対象）
├── .github/
│   └── workflows/
│       └── deploy.yml              # CI/CD（GitHub Actions）
├── README.md                       # 開発者向けの最初の入口
├── requirements.md                 # 要件定義
├── alert_design.md                 # アラートシステム設計
├── initial_setup.md                # 初回セットアップ手順（このファイル）
├── decisions.md                    # 設計判断の記録
├── merge_sql_prototype.md          # MERGE SQL プロトタイプ
└── data_source_investigation.md    # Billing API / Export 調査結果
```

______________________________________________________________________

## Terraform 変数の管理

`terraform/variables.tf` で変数を宣言し、実際の値は `terraform.tfvars` に記載する。

### terraform.tfvars.example（リポジトリに含める）

```hcl
project_id                          = "YOUR_PROJECT_ID"
region                              = "asia-northeast1"
parent_billing_account              = "XXXXXX-YYYYYY-ZZZZZZ"
billing_export_dataset              = "billing_data"
billing_export_table                = "gcp_billing_export_v1_XXXXXX"
monitoring_slack_channel            = "#alerts-gcp-billing"  # Cloud Monitoring のシステムエラー通知先
monitoring_notification_rate_limit  = "86400s"  # 同一インシデント内の最小再通知間隔（24時間）
monitoring_auto_close               = "86400s"  # エラー解消後の自動クローズ時間（24時間）
batch_image                         = "asia-northeast1-docker.pkg.dev/YOUR_PROJECT_ID/billing-link-detection/billing-collector:latest"
```

> `batch_image` は CI/CD で `TF_VAR_batch_image` として動的に上書きされる（GitHub Actions が Docker push 後のフル URI を渡す）。ローカル実行時のみ tfvars の値が使われる。

> **ビジネスアラートの通知チャンネルは `alert/alerts.yaml` の `channel` フィールドで管理**する（アラートごとに通知先を変えるため）。Terraform 変数で管理するのは Cloud Monitoring のシステムエラー通知先のみ。

### terraform.tfvars（リポジトリに含めない・.gitignore に追加）

```
terraform.tfvars
```

### CI/CD（GitHub Actions）での変数管理

GitHub Actions から Terraform を実行する場合は、`terraform.tfvars` の代わりに **GitHub Secrets / Variables** を使用し、ワークフロー内で `TF_VAR_*` 形式の環境変数として渡す。

```yaml
env:
  TF_VAR_project_id: ${{ vars.GCP_PROJECT_ID }}
  TF_VAR_parent_billing_account: ${{ secrets.PARENT_BILLING_ACCOUNT }}
```

Slack チャンネル名などの非機密情報は GitHub Variables（`vars.*`）、親請求先アカウントIDなど外部に漏らしたくない値は GitHub Secrets（`secrets.*`）を使い分ける。

______________________________________________________________________

## 作業の Terraform 管理可否まとめ

| 作業 | Terraform管理 | 理由 |
|---|---|---|
| GCPプロジェクト作成 | △（可能だが推奨しない） | 組織ポリシーや課金設定が絡むため手動が安全 |
| 請求先リンク | ❌ 手動 | プロジェクト作成直後に必要。Terraformのstate作成前 |
| APIの有効化 | ✅ Terraformで管理可能 | ただし初回はstateバケット作成前に手動で最低限有効化が必要 |
| state用GCSバケット | ❌ 手動 | TerraformのstateをTerraformで管理できない |
| Terraform実行用SA | ❌ 手動 | Terraformを実行する主体をTerraformで作れない |
| Cloud Billing Exportの有効化 | ❌ 手動（GUIのみ） | GCPにTerraformリソースが存在しない |
| Slack App作成・Token取得 | ❌ 手動 | GCPの外部サービス |
| Secret Managerへのtoken登録 | △（secret自体はTerraformで作成可能、値は手動登録） | シークレットの値をTFに書くとstateに平文で残る |
| Workload Identity Federation | ✅ Terraformで管理可能 | 初回のみ手動でTerraform SAを使って実行 |
| その他GCPリソース | ✅ Terraformで管理 | Cloud Run / Functions / Scheduler / BQ / 監視 など |
