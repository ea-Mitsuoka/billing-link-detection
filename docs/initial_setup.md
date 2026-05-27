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

> **`actAs` 権限について**: Terraform SA には上記ロールに加えて、デプロイ対象リソースが使用する各 SA への `iam.serviceaccounts.actAs` が必要。これは Terraform の `google_service_account_iam_member` リソースとして `main.tf` で管理されており、手動付与は不要。ただし、対象 SA が存在しない・削除された場合は `terraform apply` 自体が失敗する点に注意。
>
> 必要な `actAs` 対象 SA（`main.tf` の `terraform_acts_as_*` リソースで管理）：
>
> | SA | 用途 |
> |---|---|
> | `sa-billing-collector` | Cloud Run Job の runtime SA |
> | `sa-alert-handler` | Cloud Functions Gen2 の runtime SA |
> | `{PROJECT_NUMBER}-compute@developer.gserviceaccount.com` | Cloud Functions Gen2 ビルド時に Cloud Build が使用する Compute Engine デフォルト SA |
> | `sa-scheduler` | Cloud Scheduler ジョブの OIDC トークン SA |

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
| `GCP_PROJECT_ID` | `your-project-id` | 分析システム側のプロジェクト ID。Terraform / Docker 認証先 |
| `BILLING_EXPORT_PROJECT_ID` | `billing-export-project-id` | Billing Export 専用プロジェクト ID。**構成 A（単一プロジェクト）の場合は空文字** |
| `BILLING_EXPORT_DATASET` | `billing_data` | Billing Export のデータセット名（terraform.tfvars と同じ値） |
| `MONITORING_SLACK_CHANNEL` | `#alerts-gcp-billing` | Cloud Monitoring のシステムエラー通知先 |
| `MONITORING_CHANNEL_DISPLAY_NAME` | `Slack - alerts-gcp-billing` | Phase 4-2 で手動作成した Notification Channel の display_name。**初回 apply 時は空文字でも可（アラートポリシーは notification なしで作られる）** |

**Secrets（外部に漏らしたくない値）**

| 名前 | 値の例 | 用途 |
|---|---|---|
| `PARENT_BILLING_ACCOUNT` | `XXXXXX-YYYYYY-ZZZZZZ` | 親請求先アカウントID |
| `BILLING_EXPORT_TABLE` | `gcp_billing_export_v1_XXXXXX` | Billing Export のテーブル名。**テーブル名に親請求先アカウントIDが含まれるため Secret で管理** |
| `WIF_PROVIDER` | `projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github-pool/providers/github-provider` | 2-5 で作成した WIF Provider のフルパス |
| `TERRAFORM_SA_EMAIL` | `sa-terraform@PROJECT_ID.iam.gserviceaccount.com` | 2-2 で作成した Terraform 実行用 SA |

> GitHub Variables と Secrets の使い分け：Slack チャンネル名・データセット名などはログ・PR コメントに表示されても問題ないので Variables、親請求先アカウントIDや WIF Provider パスのように漏洩リスクがあるものは Secrets を使う。\
> `BILLING_EXPORT_TABLE` のテーブル名は `gcp_billing_export_v1_XXXXXX-YYYYYY-ZZZZZZ` 形式で親請求先アカウントIDが埋め込まれるため、同様に Secret で管理する。

______________________________________________________________________

## Phase 3: 外部連携の準備

### 3-1. Cloud Billing Export の有効化（GUIのみ・API不可）

#### 構成パターンの選択

エクスポート先プロジェクトは **GCP の制約により、親請求先アカウント直下のプロジェクトのみ** 選択可能。
分析システムをデプロイするプロジェクトが「親請求先アカウント直下にない」場合は、**Billing Export 専用のプロジェクトを別に作成** する必要がある。

| 構成 | 推奨される場合 | 設定 |
|---|---|---|
| A: 単一プロジェクト | 分析システムが既に親請求先アカウント直下にある | `billing_export_project_id = ""`（未設定） |
| B: 2 プロジェクト | 分析システムが別の請求先アカウントにリンク済み | `billing_export_project_id = "<export 専用プロジェクト ID>"` |

構成 B の場合、Export 専用プロジェクトを先に作成して dragon.jp 等の親請求先アカウントにリンクしてから設定すること。詳細は [architecture.md §7 プロジェクト分離](./architecture.md#7-%E3%83%97%E3%83%AD%E3%82%B8%E3%82%A7%E3%82%AF%E3%83%88%E5%88%86%E9%9B%A22-%E3%83%97%E3%83%AD%E3%82%B8%E3%82%A7%E3%82%AF%E3%83%88%E6%A7%8B%E6%88%90)。

#### 設定手順

1. GCPコンソール → 「お支払い」→「請求データのエクスポート」を開く
1. **親請求先アカウント**を選択した状態で「BigQueryへのエクスポートを編集」をクリック
1. 以下を設定して保存する

| 項目 | 設定値 |
|---|---|
| プロジェクト | 構成 A: 分析システムのプロジェクト ID / 構成 B: Billing Export 専用プロジェクト ID |
| データセット | `billing_data`（terraform.tfvars の `billing_export_dataset` の値と一致させる） |
| エクスポート種類 | 標準使用量のコスト |

4. エクスポートテーブル名（`gcp_billing_export_v1_XXXXXX`）を控えておく\
   → `terraform.tfvars` の `billing_export_table` と GitHub Secrets の `BILLING_EXPORT_TABLE` に設定する

1. 構成 B の場合: Billing Export 専用プロジェクト側で **Terraform 実行 SA（sa-terraform）に `roles/bigquery.admin` を付与** する（クロスプロジェクトのデータセット IAM 操作のため）

   ```bash
   gcloud projects add-iam-policy-binding EXPORT_PROJECT_ID \
     --member="serviceAccount:sa-terraform@ANALYSIS_PROJECT_ID.iam.gserviceaccount.com" \
     --role="roles/bigquery.admin"
   ```

> **注意**: エクスポートは設定した時点以降のデータしか蓄積されない。過去データは遡及されない。\
> また、データ反映まで最大24時間かかる場合がある。

### 3-2. Slack App の作成とBot Token取得

1. [api.slack.com/apps](https://api.slack.com/apps) → 「Create New App」→「From scratch」
1. App Name（例: `billing-link-detection`）・ワークスペースを設定
1. 「OAuth & Permissions」→「Bot Token Scopes」に以下を追加（**スコープを追加しないとインストールボタンが有効にならない**）
   - `chat:write` … チャンネルへのメッセージ投稿に必須
   - `chat:write.public` … Bot を招待せずパブリックチャンネルへ投稿する場合に必要
1. 「Install to Workspace」→ Bot User OAuth Token（`xoxb-...`）を取得

> **「Install to Workspace」の権限がない場合**: Slack ワークスペースの管理者権限が必要。\
> App の設定画面左メニュー「**Collaborators**」からシステム管理者を追加すると、管理者側で Install 作業を行うことができる。\
> 追加後、管理者に Install を依頼し、発行された Bot Token を共有してもらう。

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

データ収集バッチの SA（Terraform が作成）に、親請求先アカウントの閲覧権限を付与する。\
**請求先アカウント管理者または GCP 組織管理者の権限が必要。**

```bash
# 親請求先アカウントに Billing Account Viewer を付与
gcloud billing accounts add-iam-policy-binding PARENT_BILLING_ACCOUNT_ID \
  --member="serviceAccount:sa-billing-collector@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/billing.viewer"

# 付与後の確認
gcloud billing accounts get-iam-policy PARENT_BILLING_ACCOUNT_ID \
  --filter="bindings.members:sa-billing-collector@PROJECT_ID.iam.gserviceaccount.com"
```

> SA のメールアドレスは `terraform output billing_collector_sa_email` でも確認できる。

### 4-2. Cloud Monitoring の Slack 通知チャンネル作成（手動）

> **⚠️ 3-2 で作成した Slack App とは別物**
>
> | | 3-2 で作成した App | ここで使う App |
> |---|---|---|
> | 何か | 自分で作成したカスタム App | Google 公式の「Google Cloud Monitoring」App |
> | トークン | `xoxb-...`（Bot Token）を手動取得 | OAuth で GCP が自動管理（不要） |
> | 通知経路 | Cloud Functions → Slack API | Cloud Monitoring → Slack |
> | 用途 | ビジネスアラート（課金開始検知等） | システムエラー通知（バッチ失敗等） |
>
> この手順では Google 公式 App をワークスペースに認可するだけでよい。

Cloud Monitoring から Slack に通知するには、**Google Cloud Monitoring の Slack App** をワークスペースにインストールする必要がある。

> **Terraform 化できない理由**: GCP Monitoring の Slack 連携は独自の OAuth フロー（GCP が管理する Slack App の認可）を使用しており、通常の Bot Token とは異なる。Terraform リソース（`google_monitoring_notification_channel`）で作成するには Slack OAuth トークンの手動取得が必要で、GUI 経由の方が現実的。

1. GCP コンソール → Monitoring → Alerting → Notification Channels
1. 「ADD NEW」→ Slack → Slack ワークスペースで認可
1. 通知先チャンネル（`monitoring_slack_channel` で指定したもの。例: `#alerts-gcp-billing`）を選択
1. 表示名を保存（例: `Slack - alerts-gcp-billing`）→ この名前を Terraform の `data "google_monitoring_notification_channel"` で参照する
1. GitHub Variables の `MONITORING_CHANNEL_DISPLAY_NAME` に表示名を設定して CI/CD を再実行する

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
└── docs/
    ├── INDEX.md                    # ドキュメント逆引き
    ├── requirements.md             # 要件定義
    ├── architecture.md             # システム全体構成（図中心）
    ├── alert_design.md             # アラートシステム設計
    ├── initial_setup.md            # 初回セットアップ手順（このファイル）
    ├── todo.md                     # 本番稼働までの TODO
    ├── operations.md               # 運用ドキュメント
    ├── decisions.md                # 設計判断の記録
    ├── constraints_and_flexibility.md  # 外部制約と柔軟性整理
    ├── glossary.md                 # 用語集
    ├── merge_sql_prototype.md      # MERGE SQL プロトタイプ
    └── data_source_investigation.md  # Billing API / Export 調査結果
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
monitoring_auto_close               = "86400s"  # エラー解消後の自動クローズ時間（24時間）
batch_image                         = "asia-northeast1-docker.pkg.dev/YOUR_PROJECT_ID/billing-link-detection/billing-collector:latest"

# Billing Export 専用プロジェクト（分析システムと別プロジェクトの場合のみ設定。同一プロジェクトなら空文字）
billing_export_project_id           = ""

# Cloud Monitoring Slack 通知チャンネルの display_name（Phase 4-2 で手動作成後に設定）
monitoring_channel_display_name     = ""
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
