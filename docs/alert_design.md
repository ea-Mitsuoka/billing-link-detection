# アラートシステム設計

## 1. 概要

アラート条件をYAMLで定義し、Terraformの `for_each` が自動でCloud Schedulerジョブを生成する。汎用Cloud Functionsハンドラがクエリを実行し、結果があればSlackに通知する。

**アラートの追加・変更・削除はYAML編集 + `terraform apply` のみで完結する。Functionコードは触らない。**

______________________________________________________________________

## 2. ファイル構成

```plaintext
billing-link-detection/
├── alert/
│   ├── alerts.yaml          # アラート条件定義（人間が編集する唯一のファイル）
│   ├── main.py              # 汎用アラートハンドラ（全アラート共通・1つだけ）
│   ├── requirements.in
│   └── requirements.txt
└── terraform/               # リポジトリルートに配置（alert/ のサブディレクトリではない）
    ├── main.tf              # アラート関連の Terraform リソースを含む
    └── variables.tf
```

______________________________________________________________________

## 3. alerts.yaml の定義形式

```yaml
# チャンネル名（channel フィールド）は仮置き。実際の Slack チャンネル名に変更すること。

alerts:
  - name: billing_newly_started
    description: 未課金プロジェクトの課金開始検知（日次バッチ実行後に確認）
    schedule: "0 9 * * *"           # 毎日 09:00 JST（日次バッチ 02:00 完了後）
    query: |
      SELECT
        sub_account_name,
        project_id,
        first_billed_month,
        FORMAT_TIMESTAMP('%Y-%m-%d', linked_at, 'Asia/Tokyo') AS linked_date
      FROM `{project}.{dataset}.billing_project_links`
      WHERE billing_newly_started = TRUE
      ORDER BY first_billed_month DESC, sub_account_name
    channel: "#sales-alerts"        # 要変更：営業向けチャンネル
    message: "課金開始プロジェクトが検出されました"
    enabled: true

  - name: zero_cost_projects
    description: 前月請求金額が0円のアクティブプロジェクト一覧（月次バッチ翌日に確認）
    schedule: "0 9 6 * *"           # 毎月6日 09:00 JST（月次バッチが5日 03:00 完了後）
    query: |
      SELECT
        sub_account_name,
        project_id,
        prev_month_cost,
        ever_billed,
        FORMAT_TIMESTAMP('%Y-%m-%d', linked_at, 'Asia/Tokyo') AS linked_date
      FROM `{project}.{dataset}.billing_project_links`
      WHERE prev_month_cost = 0
        AND status = 'ACTIVE'
      ORDER BY ever_billed, linked_at, sub_account_name
    channel: "#cs-alerts"           # 要変更：CS向けチャンネル
    message: "前月請求が0円のプロジェクト一覧"
    enabled: true

  - name: never_billed_projects
    description: 過去に一度も課金がないアクティブプロジェクト一覧（月次確認）
    schedule: "0 9 1 * *"           # 毎月1日 09:00 JST
    query: |
      SELECT
        sub_account_name,
        project_id,
        FORMAT_TIMESTAMP('%Y-%m-%d', linked_at, 'Asia/Tokyo') AS linked_date
      FROM `{project}.{dataset}.billing_project_links`
      WHERE ever_billed = FALSE
        AND status = 'ACTIVE'
      ORDER BY linked_at, sub_account_name
    channel: "#cs-alerts"           # 要変更：CS向けチャンネル
    message: "過去に一度も課金がないプロジェクト一覧"
    enabled: true
```

### フィールド定義

| フィールド | 必須 | 説明 |
|---|---|---|
| `name` | 必須 | アラートの識別子（英数字・ハイフン。Cloud Schedulerジョブ名に使用） |
| `description` | 推奨 | 人間向けの説明 |
| `schedule` | 必須 | cron形式。タイムゾーンはTerraform側でAsia/Tokyoを指定 |
| `query` | 必須 | BigQuery SQL。結果が0件なら通知しない。WHERE句で検知条件を表現する |
| `channel` | 必須 | Slack チャンネル名（`#` 付きで指定。実際のワークスペースのチャンネル名に要変更） |
| `message` | 必須 | Slack通知のヘッダーテキスト |
| `enabled` | 必須 | false にするとTerraformがSchedulerジョブを削除する |

______________________________________________________________________

## 4. 汎用 Cloud Functions コード（全アラート共通）

### クエリの変数展開

`alerts.yaml` のクエリ内の `{project}` と `{dataset}` は、Cloud Functions の実行時に環境変数から展開する。BigQuery のフルテーブル参照（`` `project.dataset.table` ``）を YAML に直接ハードコードしないことで、以下が実現できる。

- **環境ごとの切り替え**: 開発・ステージング・本番の各環境で同じ alerts.yaml を共有しつつ、テーブル参照先のみ環境変数で切り替えられる
- **プロジェクトID変更への耐性**: プロジェクトIDが変わっても YAML 修正不要
- **YAML の可読性**: 長いフルパス（`` `my-prj-12345.billing_data.billing_project_links` ``）が `` `{project}.{dataset}.billing_project_links` `` に短縮される

展開は Python の `str.format()` で実行されるため、`{` `}` を SQL 内で使用する場合（JSON 関数など）は `{{` `}}` でエスケープする必要がある。

### Slack 通知方式

異なるアラートが異なるチャンネルに通知するため、チャンネルに紐付いた **Incoming Webhook ではなく Bot Token** を使用する（Incoming Webhook はチャンネル固定のため複数チャンネルへの通知に対応できない）。Bot Token で `chat.postMessage` API を呼び出し、リクエストごとに `channel` パラメータで送信先を指定する。

### Slack メッセージフォーマット

通知メッセージはシンプルな Slack Markdown テキストとする。Block Kit は採用しない（メンテナンスコストに対して可読性の向上が限定的なため）。

```
*{message}*
```

project_id: xxx | sub_account_name: yyy | ...
project_id: zzz | sub_account_name: www | ...

```
```

### クエリのコスト保護

`alerts.yaml` に書かれた SQL を Cloud Functions が直接実行する設計のため、誤って高コストなクエリ（JOIN漏れによる全件スキャン等）が仕込まれた場合の暴走を防ぐ必要がある。`bigquery.QueryJobConfig(maximum_bytes_billed=...)` で**1クエリあたり10GB**を上限に設定する。上限を超えるとBigQueryがクエリ実行前にエラーを返すため課金は発生しない。

`billing_project_links` は数百行のテーブルであり、10GB 上限は通常運用では十分すぎる余裕がある。

````python
# main.py
import logging
import os
import uuid

import functions_framework
import google.cloud.logging
import requests
from google.cloud import bigquery

# 構造化ログのセットアップ（標準 logging が Cloud Logging へ JSON で出力される）
google.cloud.logging.Client().setup_logging()
logger = logging.getLogger(__name__)

MAX_BYTES_BILLED = 10 * 1024 * 1024 * 1024  # 10 GB
MAX_ROWS = 50  # Slack 通知の最大行数。超過分は件数のみ通知

@functions_framework.http
def alert_handler(request):
    run_id  = str(uuid.uuid4())
    payload = request.get_json()
    query   = payload["query"].format(
        project=os.environ["GCP_PROJECT_ID"],
        dataset=os.environ["BQ_DATASET"],
    )
    channel = payload["channel"]
    message = payload["message"]

    logger.info(
        "alert_handler start",
        extra={"json_fields": {"run_id": run_id, "channel": channel, "batch_name": "alert-handler"}},
    )

    client     = bigquery.Client()
    job_config = bigquery.QueryJobConfig(maximum_bytes_billed=MAX_BYTES_BILLED)
    results    = list(client.query(query, job_config=job_config).result())

    if not results:
        logger.info(
            "no results, skipping notification",
            extra={"json_fields": {"run_id": run_id, "channel": channel, "result_count": 0}},
        )
        return "no results", 200

    rows_to_show = results[:MAX_ROWS]
    rows_text = "\n".join(
        " | ".join(f"{k}: {v}" for k, v in dict(row).items())
        for row in rows_to_show
    )
    suffix = (
        f"\n_...他 {len(results) - MAX_ROWS} 件。全件は BigQuery で確認してください。_"
        if len(results) > MAX_ROWS else ""
    )
    text = f"*{message}*\n```{rows_text}```{suffix}"

    # Slack API の応答チェックは必須：
    # - HTTP 4xx/5xx は raise_for_status() で例外化
    # - HTTP 200 でも Slack 側エラー（無効トークン・チャンネル不在）の場合 body の "ok" が false になる
    # 例外を上げると Cloud Functions Gen2 が ERROR ログを出力し Cloud Monitoring が検知する
    response = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}"},
        json={"channel": channel, "text": text},
        timeout=10,
    )
    response.raise_for_status()
    body = response.json()
    if not body.get("ok"):
        raise RuntimeError(
            f"Slack API error: {body.get('error')} (channel={channel}, run_id={run_id})"
        )

    logger.info(
        "notification sent",
        extra={
            "json_fields": {
                "run_id": run_id,
                "channel": channel,
                "result_count": len(results),
                "truncated": len(results) > MAX_ROWS,
            }
        },
    )
    return "ok", 200
````

______________________________________________________________________

## 5. Terraform 設計（for_each でアラートごとに自動生成）

### ソースコード ZIP 化とアップロード

Cloud Functions Gen2 のソースコードは GCS にアップロードした ZIP を参照する。Terraform の `archive_file` data source を使用して `terraform apply` 時に自動的に ZIP を生成・アップロードする方式を採用する（GitHub Actions 側で別途 zip コマンドを実行する必要がないためワークフローがシンプルになる）。

`google_storage_bucket_object` の `name` に ZIP の MD5 ハッシュを含めることで、ソースコードが変更された場合のみ Cloud Functions が再デプロイされる。

```hcl
# main.tf

locals {
  # alert/alerts.yaml を読み込む（terraform/ は repo ルート直下、alert/ も同じ階層）
  # enabled: true のアラートのみ対象
  alerts = [
    for a in yamldecode(file("${path.module}/../alert/alerts.yaml"))["alerts"] : a
    if a.enabled
  ]
}

# ソースコードを ZIP 化（alert/ ディレクトリのみが対象）
data "archive_file" "alert_handler_source" {
  type        = "zip"
  source_dir  = "${path.module}/../alert"  # alert/main.py, requirements.txt を含むディレクトリ
  output_path = "${path.module}/.tmp/alert-handler.zip"
  # alerts.yaml は Terraform 側で読み込むため Function には不要
  excludes    = ["alerts.yaml"]
}

# ZIP を GCS にアップロード（ファイル名にハッシュを含めて差分検知）
resource "google_storage_bucket_object" "function_zip" {
  name   = "alert-handler-${data.archive_file.alert_handler_source.output_md5}.zip"
  bucket = google_storage_bucket.function_source.name
  source = data.archive_file.alert_handler_source.output_path
}

# 全アラート共通の汎用Cloud Function（1つだけデプロイ）
resource "google_cloudfunctions2_function" "alert_handler" {
  name     = "alert-handler"
  location = var.region

  build_config {
    runtime     = "python312"
    entry_point = "alert_handler"
    source {
      storage_source {
        bucket = google_storage_bucket.function_source.name
        object = google_storage_bucket_object.function_zip.name
      }
    }
  }

  service_config {
    timeout_seconds  = 120
    available_memory = "256M"

    environment_variables = {
      GCP_PROJECT_ID = var.project_id
      BQ_DATASET     = var.bq_dataset
    }

    secret_environment_variables {
      key        = "SLACK_BOT_TOKEN"
      project_id = var.project_id
      secret     = google_secret_manager_secret.slack_bot_token.secret_id
      version    = "latest"
    }
  }
}

# alerts.yaml のエントリ数だけ Cloud Scheduler ジョブを自動生成
resource "google_cloud_scheduler_job" "alerts" {
  for_each  = { for a in local.alerts : a.name => a }

  name      = "alert-${each.key}"
  schedule  = each.value.schedule
  time_zone = "Asia/Tokyo"

  http_target {
    http_method = "POST"
    uri         = google_cloudfunctions2_function.alert_handler.url
    body = base64encode(jsonencode({
      query   = each.value.query
      channel = each.value.channel
      message = each.value.message
    }))
    oidc_token {
      service_account_email = google_service_account.alert_sa.email
    }
  }
}
```

______________________________________________________________________

## 6. 運用手順

### アラートを追加する

1. `alerts.yaml` に新しいエントリを追加（`enabled: true`）
1. `terraform apply`

### アラートを一時停止する（コード・デプロイ不要）

```bash
gcloud scheduler jobs pause alert-{name} --location={region}
gcloud scheduler jobs resume alert-{name} --location={region}
```

### アラートを完全に無効化する（Terraformで管理）

`alerts.yaml` の `enabled: false` に変更 → `terraform apply`（Schedulerジョブが削除される）

### 通知先チャンネルを変更する

1. `alerts.yaml` の `channel` を変更
1. `terraform apply`

### アラート条件を変更する

1. `alerts.yaml` の `query` を変更
1. `terraform apply`

### アラートを削除する

`alerts.yaml` からエントリを削除（または `enabled: false`）→ `terraform apply`

______________________________________________________________________

## 7. 繰り返し通知の設計方針

アラートの性質によって、毎回通知すべきものと初回だけ通知すべきものがある。

| アラート | 繰り返し通知 | 理由 |
|---|---|---|
| `billing_newly_started` | 初回のみ（フラグが次バッチでリセットされる） | 「課金開始」は一度きりのイベント |
| `zero_cost_projects` | 毎回通知（毎月6日） | 月次で状況を確認する定例チェック |
| `never_billed_projects` | 毎回通知（毎月1日） | 月次で未課金プロジェクトを一覧確認する定例チェック |

`ever_billed = FALSE` のプロジェクト一覧（`never_billed_projects`）は、前月と同じプロジェクトが繰り返し通知される。これは**意図的な設計**であり、毎月の棚卸し確認として機能させる。「うるさい」場合は `gcloud scheduler jobs pause` で一時停止するか、alerts.yaml の `enabled: false` で無効化する。

> **バッチ頻度を変更する場合の注意**: `billing_newly_started` のアラート `schedule` は**バッチ（`billing-collector`）の実行頻度と揃える必要がある**。
>
> `billing_newly_started` フラグは次回バッチ実行時に FALSE にリセットされる。バッチが週次（例：毎週月曜）になった場合、アラートを日次のままにするとフラグが TRUE のまま1週間残り、毎日重複通知が飛ぶ。
>
> ```yaml
> # バッチを週次（毎週月曜 02:00）に変更した場合の alerts.yaml
> - name: billing_newly_started
>   schedule: "0 9 * * 1"   # ← バッチ実行日（月曜）に揃える
> ```
>
> `zero_cost_projects` と `never_billed_projects` はバッチ頻度に依存しないため、スケジュール変更は不要。

______________________________________________________________________

## 8. Cloud Functions リソース設定・環境変数

| 設定項目 | 値 | 理由 |
|---|---|---|
| タイムアウト | 120秒 | BQクエリ + Slack通知の合計でも数秒。余裕を持って120秒を上限とする |
| メモリ | 256 MB | クエリ結果をメモリに展開する処理であり、300プロジェクト規模では256MBで十分 |
| `GCP_PROJECT_ID` | Terraformの `var.project_id` | `{project}` のクエリ変数展開に使用 |
| `BQ_DATASET` | Terraformの `var.bq_dataset` | `{dataset}` のクエリ変数展開に使用 |
| `SLACK_BOT_TOKEN` | Secret Manager 参照 | Slack `chat.postMessage` API の認証に使用。平文で保持しない |

これらはすべて上記 Terraform `service_config` ブロックで設定済み。

______________________________________________________________________

## 9. システムエラー監視（Cloud Monitoring）

データ収集バッチ（Cloud Run Jobs）とアラートハンドラ（Cloud Functions）のエラーを Cloud Monitoring で監視する。ビジネス条件の評価（0円プロジェクトの件数など）は BigQuery SQL で行うため、ここでは対象外。

### 監視の仕組み

```
Cloud Run Jobs / Cloud Functions
    ↓ ERROR ログ出力
Cloud Logging
    ↓ ログベースメトリクス（エラーログをカウント）
Cloud Monitoring アラートポリシー（メトリクス > 0 で発火）
    ↓
Slack #alerts-gcp-billing
```

### ログフィルター文字列

Cloud Logging のフィルター言語（SQL ではない）で記述する。エラーログの有無（件数 > 0）でアラートが発火する。

**日次データ収集バッチ用**

```
resource.type="cloud_run_job"
resource.labels.job_name="billing-collector"
severity>=ERROR
```

**月次コスト更新バッチ用**

```
resource.type="cloud_run_job"
resource.labels.job_name="billing-cost-updater"
severity>=ERROR
```

**アラートハンドラ用**（Cloud Functions Gen2 は Cloud Run 上で動作する）

```
resource.type="cloud_run_revision"
resource.labels.service_name="alert-handler"
severity>=ERROR
```

### 通知頻度の設定

Cloud Monitoring はインシデント単位で管理する。エラーログを検知するとインシデントが発生（通知）し、条件がクリアされるとインシデントが解消（解消通知）する。

通知頻度は Terraform 変数として定義し、コード変更なしで調整できるようにする。

| 変数名 | 推奨初期値 | 意味 |
|---|---|---|
| `monitoring_notification_rate_limit` | `86400s` | 同一インシデント内での最小再通知間隔（日次バッチは24時間で「1失敗1通知」） |
| `monitoring_auto_close` | `86400s` | エラーログが出なくなってからインシデントを自動クローズするまでの時間 |

### Terraform 定義例

```hcl
# ログベースメトリクス（データ収集バッチ）
resource "google_logging_metric" "batch_error" {
  name   = "billing-collector-error-count"
  filter = "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"billing-collector\" AND severity>=ERROR"
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

# ログベースメトリクス（アラートハンドラ）
resource "google_logging_metric" "alert_handler_error" {
  name   = "alert-handler-error-count"
  filter = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"alert-handler\" AND severity>=ERROR"
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

# アラートポリシー（データ収集バッチ）
resource "google_monitoring_alert_policy" "batch_error" {
  display_name = "Billing Collector Job Error"
  combiner     = "OR"

  conditions {
    display_name = "ERROR log detected"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/billing-collector-error-count\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_RATE"
      }
    }
  }

  # Notification Channel は手動で作成済みのものを data ソース経由で参照する
  # （Slack 連携の OAuth は API 提供されないため、Cloud Console での初回作成が必須）
  notification_channels = [data.google_monitoring_notification_channel.slack.name]

  alert_strategy {
    notification_rate_limit {
      period = var.monitoring_notification_rate_limit
    }
    auto_close = var.monitoring_auto_close
  }
}
```

アラートハンドラ用も同様のパターンで `google_monitoring_alert_policy` を別リソースとして定義する。

### Slack 通知チャンネルの設定

`notification_channels` で参照している `google_monitoring_notification_channel.slack` は Cloud Monitoring 標準の Slack 連携で、別途 Slack ワークスペースに **Google Cloud Monitoring の Slack App** をインストールし、認証トークンを取得する必要がある。

**初回セットアップ手順（手動）**

1. GCP コンソール → Monitoring → Alerting → Notification Channels → 「ADD NEW」→ Slack
1. Slack ワークスペースで認可フローを完了
1. 通知先チャンネル（例: `#alerts-gcp-billing`）を選択して保存
1. Terraform は既存リソースを `import` して取り込むか、`data` ソースとして参照する

**Terraform 定義例（手動作成済みチャンネルを参照する場合）**

```hcl
# 手動で作成した Notification Channel を data ソースで取得
data "google_monitoring_notification_channel" "slack" {
  display_name = "Slack - alerts-gcp-billing"  # コンソールで設定した名前と一致させる
}

# google_monitoring_alert_policy の参照を data ソースに変更
# notification_channels = [data.google_monitoring_notification_channel.slack.name]
```

> Slack App の自動インストールは API では提供されないため、初回のみ手動設定が必須。`initial_setup.md` の Phase 4 に組み込む。

## 10. コスト見積もり（アラートサブシステムのみ）

> システム全体のコスト見積もりは `requirements.md` Section 5 を参照。ここではアラートサブシステム（Cloud Functions + Cloud Scheduler）のみを対象とする。

30顧客・300プロジェクト規模において、2年後でもBigQueryのスキャン量は月数GB以下（無料枠1TB/月に対して0.1%未満）。

| コンポーネント | 月額 | 備考 |
|---|---|---|
| Cloud Functions 実行 | $0 | 無料枠200万回/月に対して数十〜数百回 |
| Cloud Scheduler | $0〜 | 3ジョブまで無料。超過分は$0.10/job/月 |
| BigQuery クエリ | $0 | 無料枠1TB/月に対してスキャン量が無視できる規模 |
| **合計（アラート3件の場合）** | **$0〜$0.20/月** | データ収集2ジョブ（日次・月次）+ アラート3ジョブ = 計5ジョブ。超過2ジョブ×$0.10 |
