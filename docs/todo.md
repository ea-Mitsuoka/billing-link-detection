# 本番稼働までの TODO

Terraform や GitHub Actions で自動化できない **人が手で行う作業** と、その後の **動作確認まで含めた全タスク**。
完了したらチェックを入れること。

______________________________________________________________________

## 前提確認

このファイルの作業を始める前に [initial_setup.md](./initial_setup.md) が完了していること。

- [x] `initial_setup.md` の Phase 1（GCP プロジェクト・API 有効化）が完了している
- [x] `initial_setup.md` の Phase 2（Terraform 実行基盤・WIF・GitHub Variables 基本セット）が完了している
- [x] `initial_setup.md` の Phase 3（Billing Export 有効化・Slack Bot Token 登録）が完了している
- [x] `terraform apply` が少なくとも 1 回成功し、BigQuery テーブル・Cloud Run Jobs・Cloud Functions が GCP 上に存在する

______________________________________________________________________

## 作業順序マップ

各 Step の依存関係。上から順に実施する。

```
Step 1: 親請求先アカウント ID の確定
    ├── Step 2: Billing Export 専用プロジェクト
    │       ↓（テーブル名が確定）
    │   Step 5: GitHub Variables 更新（BILLING_EXPORT_PROJECT_ID / BILLING_EXPORT_TABLE）
    │       ↓
    ├── Step 3: Slack 設定（alerts.yaml 編集 + push）
    ├── Step 4: Cloud Monitoring 通知チャンネル
    │       ↓（表示名が確定）
    │   Step 5: GitHub Variables 更新（MONITORING_CHANNEL_DISPLAY_NAME）
    └── Step 6: Billing API 権限付与

        ↓（Step 2-6 がすべて完了）

Step 7: git push → CI/CD デプロイ
        ↓
Step 8: Billing Export データ到着確認（最大 24 時間待機）
        ↓
Step 9: 動作確認チェックリスト
        ↓
Step 10: 稼働開始後 初週の確認
```

______________________________________________________________________

## Step 1: 親請求先アカウント ID の確定

- [x] dragon.jp 親請求先アカウントの ID を確認する（形式: `XXXXXX-XXXXXX-XXXXXX`）
- [x] `terraform/terraform.tfvars` に設定する（ローカル開発・手動 apply 用）
  ```hcl
  parent_billing_account = "XXXXXX-XXXXXX-XXXXXX"
  ```
- [x] GitHub Secrets の `PARENT_BILLING_ACCOUNT` が設定済みであることを確認する

______________________________________________________________________

## Step 2: Billing Export 専用プロジェクトのセットアップ

GCP の Billing Export 設定 UI は「dragon.jp 親請求先アカウントに直接リンクされたプロジェクト」しか選択できないため専用プロジェクトが必要。詳細は [decisions.md §11](./decisions.md#11-billing-export-%E3%81%AE%E6%A0%BC%E7%B4%8D%E5%85%88%E3%83%97%E3%83%AD%E3%82%B8%E3%82%A7%E3%82%AF%E3%83%88)。

- [x] dragon.jp 親請求先アカウントに直接リンクした GCP プロジェクトを作成する（`ea-gcsales-billing-export`）
- [x] そのプロジェクトで Billing Export を有効化する
  - GCP コンソール → 「お支払い」→「請求データのエクスポート」→「BigQuery へのエクスポートを編集」
  - **親請求先アカウントを選択した状態**で開くこと（プロジェクトを選択した状態で開くと見つからない）
  - エクスポート先データセット: `billing_data`（リージョン: `asia-northeast1`）
  - エクスポート種類: 標準使用量のコスト
- [x] エクスポートテーブル名（`gcp_billing_export_v1_016F1F_15EFC6_D5CF70`）を控える
- [x] `terraform/terraform.tfvars` を更新する
  ```hcl
  billing_export_project_id = "ea-gcsales-billing-export"
  billing_export_table      = "gcp_billing_export_v1_016F1F_15EFC6_D5CF70"
  ```
- [x] Terraform SA に Export 専用プロジェクトの `roles/bigquery.admin` を付与する
  ```bash
  gcloud projects add-iam-policy-binding <BILLING_EXPORT_PROJECT_ID> \
    --member="serviceAccount:sa-terraform@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/bigquery.admin"
  ```

> **注意**: Billing Export は設定時点以降のデータしか蓄積されない。過去データは遡及されない。
> また、設定後データが反映されるまで最大 24 時間かかる（Step 8 で確認する）。

______________________________________________________________________

## Step 3: Slack 設定

- [x] Slack アプリを作成し、Bot Token（`xoxb-...`）を取得する
  - 必要なスコープ: `chat:write`、`chat:write.public`（パブリックチャンネルへ投稿する場合）
  - 詳細手順: [initial_setup.md §3-2](./initial_setup.md#3-2-slack-app-%E3%81%AE%E4%BD%9C%E6%88%90%E3%81%A8bot-token%E5%8F%96%E5%BE%97)
- [x] Secret Manager に本物のトークンを登録する
  ```bash
  echo -n "xoxb-REAL-TOKEN" | \
    gcloud secrets versions add slack-bot-token \
      --data-file=- \
      --project=${GCP_PROJECT_ID}
  ```
- [x] 各アラートの通知チャンネルを GitHub Variables の `ALERT_CHANNEL_OVERRIDES` で設定する
  ```json
  {"billing_newly_started":"#実際のチャンネル名","zero_cost_projects":"#実際のチャンネル名", ...}
  ```
  > `alerts.yaml` の `channel` を直接編集する代わりに `ALERT_CHANNEL_OVERRIDES` で上書きする方式を採用。
- [x] Slack Bot をそれらのチャンネルに招待する（プライベートチャンネルの場合）
- [x] 変更を git でコミット・push 済み（Step 7 で CI/CD が完了）

______________________________________________________________________

## Step 4: Cloud Monitoring の Slack 通知チャンネル設定（GUI のみ）

Terraform では作成不可（GCP Monitoring 独自の Slack OAuth フローが必要なため）。

- [ ] GCP コンソール → Monitoring → Alerting → Notification Channels を開く
- [ ] 「ADD NEW」→「Slack」→ Slack ワークスペースで認可する
  - 情シス部門に「Google Cloud Monitoring」Slack App のインストール承認を依頼済み（承認待ち）
- [ ] 通知先チャンネル（`terraform.tfvars` の `monitoring_slack_channel` に設定した値）を選択する
- [ ] 表示名を保存する（例: `Slack - alerts-gcp-billing`）
- [ ] GitHub Variables の `MONITORING_CHANNEL_DISPLAY_NAME` に表示名を設定して CI/CD を再実行する

______________________________________________________________________

## Step 5: GitHub Variables / Secrets の追加・更新

- [x] 以下を追加・更新済み

  | 種別 | 名前 | 状態 |
  |---|---|---|
  | Variable | `BILLING_EXPORT_PROJECT_ID` | `ea-gcsales-billing-export` 設定済み |
  | **Secret** | `BILLING_EXPORT_TABLE` | `gcp_billing_export_v1_016F1F_15EFC6_D5CF70` 設定済み（テーブル名に請求先アカウントIDが含まれるため Secret で管理） |
  | Variable | `MONITORING_CHANNEL_DISPLAY_NAME` | Step 4 完了後に設定予定 |

______________________________________________________________________

## Step 6: Billing API 権限付与

`sa-billing-collector` が親請求先アカウントの情報を読み取るための権限。
**請求先アカウント管理者または GCP 組織管理者の権限が必要。**

- [x] dragon.jp 親請求先アカウントに `roles/billing.viewer` を付与する
  ```bash
  gcloud billing accounts add-iam-policy-binding <PARENT_BILLING_ACCOUNT_ID> \
    --member="serviceAccount:sa-billing-collector@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/billing.viewer"
  ```
- [x] 付与後に確認する
  ```bash
  gcloud billing accounts get-iam-policy <PARENT_BILLING_ACCOUNT_ID> \
    --filter="bindings.members:sa-billing-collector@${GCP_PROJECT_ID}.iam.gserviceaccount.com"
  ```

______________________________________________________________________

## Step 7: CI/CD デプロイ（git push → GitHub Actions → terraform apply）

- [x] ローカルの変更をすべてコミットする
- [x] `main` ブランチに push する
- [x] GitHub Actions の `deploy` ワークフローが成功した（✅ グリーン）
- [ ] terraform apply が完了し、Cloud Monitoring の Alerting Policy に通知チャンネルが紐付いたことを確認する
  - Step 4 完了後に再デプロイして確認する

______________________________________________________________________

## Step 8: Billing Export データの到着確認

Billing Export はデータ反映まで最大 24 時間かかる。Step 2 で設定してからこのステップまで時間が空いていない場合は待機する。

- [ ] BigQuery コンソールで以下のクエリを実行し、データが存在することを確認する
  ```sql
  SELECT MAX(export_time) AS latest_export, COUNT(*) AS row_count
  FROM `ea-gcsales-billing-export.billing_data.gcp_billing_export_v1_016F1F_15EFC6_D5CF70`
  ```
  - `row_count > 0` であれば準備完了
  - 0 件の場合は最大 24 時間後に再確認する（設定直後は空でも正常）

______________________________________________________________________

## Step 9: 動作確認チェックリスト

Step 7 の CI/CD デプロイと Step 8 のデータ到着確認が済んでから実施する。

### 9-1. 日次バッチの動作確認

- [x] 日次バッチを手動実行する
  ```bash
  gcloud run jobs execute billing-collector \
    --region=asia-northeast1 \
    --project=${GCP_PROJECT_ID}
  ```
- [x] 実行が成功したことを確認する（ステータスが `SUCCEEDED`）
- [x] BigQuery でデータが取得できていることを確認する
  - 結果: `ACTIVE 255件`（2026-05-27 手動実行）

### 9-2. アラートの Slack 通知確認

- [ ] 各アラートを手動発火してテスト通知を確認する
  ```bash
  gcloud scheduler jobs run alert-billing_newly_started \
    --location=asia-northeast1 --project=${GCP_PROJECT_ID}

  gcloud scheduler jobs run alert-zero_cost_projects \
    --location=asia-northeast1 --project=${GCP_PROJECT_ID}

  gcloud scheduler jobs run alert-never_billed_projects \
    --location=asia-northeast1 --project=${GCP_PROJECT_ID}
  ```
- [ ] 各アラートの通知が正しいチャンネルに届いていることを確認する
- [ ] 対象データが 0 件の場合は Slack 通知が**来ない**ことが正常（`no results` で終了）
  - Cloud Logging で「no results」ログが出ていれば Function 自体は正常動作している

### 9-3. Cloud Monitoring のエラー通知確認

- [ ] GCP コンソール → Cloud Monitoring → Alerting → Policies を開き、3 つのポリシーが有効であることを確認する
  - `billing-collector-error`（日次バッチ ERROR 検知）
  - `billing-cost-updater-error`（月次バッチ ERROR 検知）
  - `alert-handler-error`（Cloud Functions ERROR 検知）
- [ ] 各ポリシーに Slack 通知チャンネルが紐付いていることを確認する（Step 4 完了後）

### 9-4. Cloud Scheduler の確認

- [x] 7 つのスケジューラジョブが `ENABLED` 状態であることを確認する（2026-05-27）
  - `billing-collector-daily`（毎日 02:00 JST）✅
  - `billing-cost-updater-monthly`（毎月 5 日 03:00 JST）✅
  - `alert-billing_newly_started`（毎日 09:00 JST）✅
  - `alert-zero_cost_projects`（毎月 6 日 09:00 JST）✅
  - `alert-never_billed_projects`（毎月 1 日 09:00 JST）✅
  - `alert-cost_surge_projects`（毎月 7 日 09:00 JST）✅
  - `alert-subscription_cost_surge`（毎月 7 日 09:00 JST）✅

______________________________________________________________________

## Step 10: 稼働開始後 初週の確認

自動スケジュール実行が正しく機能しているかを確認する。

- [ ] 翌日の 02:00 JST 以降に日次バッチが自動実行されたことを確認する
  ```bash
  gcloud run jobs executions list \
    --job=billing-collector \
    --region=asia-northeast1 \
    --project=${GCP_PROJECT_ID} \
    --limit=5
  ```
- [ ] 翌朝 09:00 JST 以降に `billing_newly_started` アラートが自動実行されたことを確認する
  - Slack に通知またはログに「no results」があれば正常
- [ ] Cloud Logging でバッチの `batch complete` ログが出ていることを確認する
  ```
  resource.type="cloud_run_job" AND jsonPayload.message="batch complete"
  ```
- [ ] `ever_billed = FALSE` のプロジェクトに課金が始まった場合に `billing_newly_started` アラートが届くことを確認する（翌日以降）

______________________________________________________________________

## 参照ドキュメント

| 目的 | 参照先 |
|---|---|
| 前提作業の詳細 | [initial_setup.md](./initial_setup.md) |
| 障害が起きた場合 | [operations.md](./operations.md) §障害対応フロー |
| アラートの追加・変更 | [alert_design.md](./alert_design.md) §3 |
| システム全体の構成確認 | [architecture.md](./architecture.md) |
