# 本番稼働までの TODO

Terraform や GitHub Actions で自動化できない **人が手で行う作業** と、その後の **動作確認まで含めた全タスク**。
完了したらチェックを入れること。

______________________________________________________________________

## 前提確認

このファイルの作業を始める前に [initial_setup.md](./initial_setup.md) が完了していること。

- [ ] `initial_setup.md` の Phase 1（GCP プロジェクト・API 有効化）が完了している
- [ ] `initial_setup.md` の Phase 2（Terraform 実行基盤・WIF・GitHub Variables 基本セット）が完了している
- [ ] `initial_setup.md` の Phase 3（Billing Export 有効化・Slack Bot Token 登録）が完了している
- [ ] `terraform apply` が少なくとも 1 回成功し、BigQuery テーブル・Cloud Run Jobs・Cloud Functions が GCP 上に存在する

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

- [ ] dragon.jp 親請求先アカウントの ID を確認する（形式: `XXXXXX-XXXXXX-XXXXXX`）
- [ ] `terraform/terraform.tfvars` に設定する（ローカル開発・手動 apply 用）
  ```hcl
  parent_billing_account = "XXXXXX-XXXXXX-XXXXXX"
  ```
- [ ] GitHub Secrets の `PARENT_BILLING_ACCOUNT` が設定済みであることを確認する
  - （`initial_setup.md` Phase 2-6 で登録済みのはずだが、プレースホルダーのままなら更新する）

______________________________________________________________________

## Step 2: Billing Export 専用プロジェクトのセットアップ

GCP の Billing Export 設定 UI は「dragon.jp 親請求先アカウントに直接リンクされたプロジェクト」しか選択できないため専用プロジェクトが必要。詳細は [decisions.md §11](./decisions.md#11-billing-export-%E3%81%AE%E6%A0%BC%E7%B4%8D%E5%85%88%E3%83%97%E3%83%AD%E3%82%B8%E3%82%A7%E3%82%AF%E3%83%88)。

- [ ] dragon.jp 親請求先アカウントに直接リンクした GCP プロジェクトを作成する（または既存を確認する）
- [ ] そのプロジェクトで Billing Export を有効化する
  - GCP コンソール → 「お支払い」→「請求データのエクスポート」→「BigQuery へのエクスポートを編集」
  - **親請求先アカウントを選択した状態**で開くこと（プロジェクトを選択した状態で開くと見つからない）
  - エクスポート先データセット: `billing_data`（リージョン: `asia-northeast1`）
  - エクスポート種類: 標準使用量のコスト
- [ ] エクスポートテーブル名（`gcp_billing_export_v1_XXXXXX_XXXXXX_XXXXXX`）を控える
  - → この値が Step 5 で必要になる
- [ ] `terraform/terraform.tfvars` を更新する
  ```hcl
  billing_export_project_id = "<Export 専用プロジェクト ID>"
  billing_export_table      = "gcp_billing_export_v1_XXXXXX_XXXXXX_XXXXXX"
  ```
- [ ] Terraform SA に Export 専用プロジェクトの `roles/bigquery.admin` を付与する
  ```bash
  gcloud projects add-iam-policy-binding <BILLING_EXPORT_PROJECT_ID> \
    --member="serviceAccount:sa-terraform@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/bigquery.admin"
  ```

> **注意**: Billing Export は設定時点以降のデータしか蓄積されない。過去データは遡及されない。
> また、設定後データが反映されるまで最大 24 時間かかる（Step 8 で確認する）。

______________________________________________________________________

## Step 3: Slack 設定

- [ ] Slack アプリを作成し、Bot Token（`xoxb-...`）を取得する
  - 必要なスコープ: `chat:write`、`chat:write.public`（パブリックチャンネルへ投稿する場合）
  - 詳細手順: [initial_setup.md §3-2](./initial_setup.md#3-2-slack-app-%E3%81%AE%E4%BD%9C%E6%88%90%E3%81%A8bot-token%E5%8F%96%E5%BE%97)
- [ ] Secret Manager に本物のトークンを登録する（現在は "placeholder"）
  ```bash
  echo -n "xoxb-REAL-TOKEN" | \
    gcloud secrets versions add slack-bot-token \
      --data-file=- \
      --project=${GCP_PROJECT_ID}
  ```
- [ ] `alert/alerts.yaml` の各アラートの `channel` を実際の Slack チャンネル名に変更する
  ```yaml
  # billing_newly_started アラート
  channel: "#actual-sales-channel"   # "#sales-alerts" から変更

  # zero_cost_projects アラート
  channel: "#actual-cs-channel"      # "#cs-alerts" から変更

  # never_billed_projects アラート
  channel: "#actual-cs-channel"      # "#cs-alerts" から変更
  ```
- [ ] Slack Bot をそれらのチャンネルに招待する（プライベートチャンネルの場合）
- [ ] `alerts.yaml` の変更を git でコミットする（Step 7 の push で CI/CD が拾う）
  ```bash
  git add alert/alerts.yaml
  git commit -m "chore: update Slack alert channel names"
  ```

______________________________________________________________________

## Step 4: Cloud Monitoring の Slack 通知チャンネル設定（GUI のみ）

Terraform では Slack 通知チャンネルの作成に対応していないため手動設定が必要。

- [ ] GCP コンソール → Monitoring → Alerting → Notification Channels を開く
- [ ] 「ADD NEW」→「Slack」→ Slack ワークスペースで認可する
- [ ] 通知先チャンネル（`terraform.tfvars` の `monitoring_slack_channel` に設定した値）を選択する
- [ ] 表示名を保存する（例: `Slack - alerts-gcp-billing`）
- [ ] 表示名を `terraform/terraform.tfvars` に設定する
  ```hcl
  monitoring_channel_display_name = "Slack - alerts-gcp-billing"
  ```

______________________________________________________________________

## Step 5: GitHub Variables / Secrets の追加・更新

CI/CD（GitHub Actions）が Terraform に正しい値を渡せるよう、リポジトリの設定を更新する。

- [ ] GitHub リポジトリ → Settings → Secrets and variables → Actions で以下を追加・更新する

  | 種別 | 名前 | 値 | 備考 |
  |---|---|---|---|
  | Variable | `BILLING_EXPORT_PROJECT_ID` | Step 2 で作成した Export 専用プロジェクト ID | 新規追加 |
  | Variable | `BILLING_EXPORT_TABLE` | Step 2 で控えたテーブル名（`gcp_billing_export_v1_...`） | 既存を更新（空またはプレースホルダーの場合） |
  | Variable | `MONITORING_CHANNEL_DISPLAY_NAME` | Step 4 で設定した表示名 | 新規追加 |

______________________________________________________________________

## Step 6: Billing API 権限付与

`sa-billing-collector` が親請求先アカウントの情報を読み取るための権限。
**これは GCP 組織管理者の権限が必要なため、プロジェクト管理者では設定できない場合がある。**

- [ ] `terraform output billing_collector_sa_email` で SA のメールアドレスを確認する
- [ ] dragon.jp 親請求先アカウントに `roles/billing.viewer` を付与する
  ```bash
  gcloud billing accounts add-iam-policy-binding <PARENT_BILLING_ACCOUNT_ID> \
    --member="serviceAccount:sa-billing-collector@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/billing.viewer"
  ```
  または GCP コンソール → 請求先アカウント → アカウント管理 → 権限 → 追加

______________________________________________________________________

## Step 7: CI/CD デプロイ（git push → GitHub Actions → terraform apply）

Step 3〜5 の変更（alerts.yaml・tfvars・GitHub Variables）を本番環境に反映する。

- [ ] ローカルの変更をすべてコミットする
  ```bash
  git add alert/alerts.yaml           # Step 3 の変更（チャンネル名）
  # terraform.tfvars は .gitignore 対象のため push しない
  ```
- [ ] `main` ブランチに push する（または PR を作成してマージする）
  ```bash
  git push origin main
  ```
- [ ] GitHub Actions の `deploy` ワークフローが成功したことを確認する
  - GitHub → Actions タブ → 最新の実行が ✅ であること
  - 失敗した場合はログを確認して対処する
- [ ] terraform apply が完了し、Cloud Monitoring の Alerting Policy に通知チャンネルが紐付いたことを確認する
  - GCP コンソール → Cloud Monitoring → Alerting → Policies

______________________________________________________________________

## Step 8: Billing Export データの到着確認

Billing Export はデータ反映まで最大 24 時間かかる。Step 2 で設定してからこのステップまで時間が空いていない場合は待機する。

- [ ] BigQuery コンソールで以下のクエリを実行し、データが存在することを確認する
  ```sql
  SELECT MAX(export_time) AS latest_export, COUNT(*) AS row_count
  FROM `<BILLING_EXPORT_PROJECT_ID>.billing_data.gcp_billing_export_v1_XXXXXX`
  ```
  - `row_count > 0` であれば準備完了
  - 0 件の場合は最大 24 時間後に再確認する（設定直後は空でも正常）

______________________________________________________________________

## Step 9: 動作確認チェックリスト

Step 7 の CI/CD デプロイと Step 8 のデータ到着確認が済んでから実施する。

### 9-1. 日次バッチの動作確認

- [ ] 日次バッチを手動実行する
  ```bash
  gcloud run jobs execute billing-collector \
    --region=asia-northeast1 \
    --project=${GCP_PROJECT_ID}
  ```
- [ ] 実行が成功したことを確認する（ステータスが `SUCCEEDED`）
  ```bash
  gcloud run jobs executions list \
    --job=billing-collector \
    --region=asia-northeast1 \
    --project=${GCP_PROJECT_ID} \
    --limit=1
  ```
- [ ] BigQuery でデータが取得できていることを確認する
  ```sql
  -- レコード数と最終取得時刻
  SELECT status, COUNT(*) AS cnt
  FROM `${GCP_PROJECT_ID}.billing_data.billing_project_links`
  GROUP BY status;

  -- 最新バッチ実行時刻
  SELECT MAX(last_fetched_at) AS last_batch
  FROM `${GCP_PROJECT_ID}.billing_data.billing_project_links`;
  ```
  - `cnt > 0` かつ `last_batch` が直近の実行時刻であれば正常

### 9-2. アラートの Slack 通知確認

- [ ] 各アラートを手動発火してテスト通知を確認する
  ```bash
  # 課金開始検知アラート（日次）
  gcloud scheduler jobs run alert-billing_newly_started \
    --location=asia-northeast1 \
    --project=${GCP_PROJECT_ID}

  # 前月0円プロジェクト一覧（月次）
  gcloud scheduler jobs run alert-zero_cost_projects \
    --location=asia-northeast1 \
    --project=${GCP_PROJECT_ID}

  # 未課金プロジェクト一覧（月次）
  gcloud scheduler jobs run alert-never_billed_projects \
    --location=asia-northeast1 \
    --project=${GCP_PROJECT_ID}
  ```
- [ ] 各アラートの通知が正しいチャンネルに届いていることを確認する
  - `billing_newly_started` → 営業向けチャンネル
  - `zero_cost_projects`、`never_billed_projects` → CS 向けチャンネル
- [ ] 対象データが 0 件の場合は Slack 通知が**来ない**ことが正常（`no results` で終了）
  - Cloud Logging で「no results」ログが出ていれば Function 自体は正常動作している

### 9-3. Cloud Monitoring のエラー通知確認

- [ ] GCP コンソール → Cloud Monitoring → Alerting → Policies を開き、3 つのポリシーが有効であることを確認する
  - `billing-collector-error`（日次バッチ ERROR 検知）
  - `billing-cost-updater-error`（月次バッチ ERROR 検知）
  - `alert-handler-error`（Cloud Functions ERROR 検知）
- [ ] 各ポリシーに Slack 通知チャンネルが紐付いていることを確認する

### 9-4. Cloud Scheduler の確認

- [ ] 5 つのスケジューラジョブが `ENABLED` 状態であることを確認する
  ```bash
  gcloud scheduler jobs list \
    --location=asia-northeast1 \
    --project=${GCP_PROJECT_ID}
  ```
  - `billing-collector-daily`（毎日 02:00 JST）
  - `billing-cost-updater-monthly`（毎月 5 日 03:00 JST）
  - `alert-billing_newly_started`（毎日 09:00 JST）
  - `alert-zero_cost_projects`（毎月 6 日 09:00 JST）
  - `alert-never_billed_projects`（毎月 1 日 09:00 JST）

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
