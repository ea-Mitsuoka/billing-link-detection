# ===================================================================
# BigQuery
# ===================================================================

resource "google_bigquery_dataset" "billing_data" {
  dataset_id  = var.bq_dataset
  location    = var.region
  description = "GCP Billing リンク情報・Billing Export 格納データセット"
}

resource "google_bigquery_dataset" "billing_data_test" {
  dataset_id  = "${var.bq_dataset}_test"
  location    = var.region
  description = "テスト用データセット（本番と同一スキーマ・CI 結合テストで使用）"
}

resource "google_bigquery_table" "billing_project_links" {
  dataset_id          = google_bigquery_dataset.billing_data.dataset_id
  table_id            = "billing_project_links"
  deletion_protection = false # lifecycle.prevent_destroy で保護するため false に設定

  schema = jsonencode([
    { name = "parent_account_id", type = "STRING", mode = "REQUIRED", description = "親請求先アカウントID" },
    { name = "sub_account_id", type = "STRING", mode = "REQUIRED", description = "請求先サブアカウントID" },
    { name = "sub_account_name", type = "STRING", mode = "NULLABLE", description = "サブアカウントの表示名" },
    { name = "project_id", type = "STRING", mode = "REQUIRED", description = "Google Cloud プロジェクトID" },
    { name = "billing_enabled", type = "BOOLEAN", mode = "REQUIRED", description = "課金有効状態" },
    { name = "sub_account_open", type = "BOOLEAN", mode = "REQUIRED", description = "サブアカウントがオープンかどうか" },
    { name = "status", type = "STRING", mode = "REQUIRED", description = "ACTIVE / UNLINKED / BILLING_DISABLED / SUB_CLOSED" },
    { name = "linked_at", type = "TIMESTAMP", mode = "REQUIRED", description = "最初にリンクを確認した日時" },
    { name = "unlinked_at", type = "TIMESTAMP", mode = "NULLABLE", description = "最後にアンリンクされた日時（NULL = 現在リンク中）" },
    { name = "relinked_at", type = "TIMESTAMP", mode = "NULLABLE", description = "最後に再リンクされた日時（NULL = 再リンク経験なし）" },
    { name = "link_count", type = "INTEGER", mode = "REQUIRED", description = "リンク回数（再リンクのたびにインクリメント）" },
    { name = "last_fetched_at", type = "TIMESTAMP", mode = "REQUIRED", description = "バッチが API からこのレコードを取得した最後の日時" },
    { name = "updated_at", type = "TIMESTAMP", mode = "REQUIRED", description = "レコードの内容が実際に変化したときのみ更新する日時" },
    { name = "prev_month_cost", type = "FLOAT64", mode = "NULLABLE", description = "前月の請求金額（NULL = 未取得）" },
    { name = "cost_currency", type = "STRING", mode = "NULLABLE", description = "前月請求金額の通貨コード（例: USD）" },
    { name = "ever_billed", type = "BOOLEAN", mode = "REQUIRED", description = "これまでに一度でも課金実績があるか" },
    { name = "first_billed_month", type = "STRING", mode = "NULLABLE", description = "初回課金月（YYYY-MM 形式）" },
    { name = "billing_newly_started", type = "BOOLEAN", mode = "REQUIRED", description = "当バッチ実行で初めて課金を確認したか" }
  ])

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_bigquery_dataset.billing_data]
}

resource "google_bigquery_table" "billing_project_links_test" {
  dataset_id          = google_bigquery_dataset.billing_data_test.dataset_id
  table_id            = "billing_project_links"
  deletion_protection = false

  schema = jsonencode([
    { name = "parent_account_id", type = "STRING", mode = "REQUIRED", description = "親請求先アカウントID" },
    { name = "sub_account_id", type = "STRING", mode = "REQUIRED", description = "請求先サブアカウントID" },
    { name = "sub_account_name", type = "STRING", mode = "NULLABLE", description = "サブアカウントの表示名" },
    { name = "project_id", type = "STRING", mode = "REQUIRED", description = "Google Cloud プロジェクトID" },
    { name = "billing_enabled", type = "BOOLEAN", mode = "REQUIRED", description = "課金有効状態" },
    { name = "sub_account_open", type = "BOOLEAN", mode = "REQUIRED", description = "サブアカウントがオープンかどうか" },
    { name = "status", type = "STRING", mode = "REQUIRED", description = "ACTIVE / UNLINKED / BILLING_DISABLED / SUB_CLOSED" },
    { name = "linked_at", type = "TIMESTAMP", mode = "REQUIRED", description = "最初にリンクを確認した日時" },
    { name = "unlinked_at", type = "TIMESTAMP", mode = "NULLABLE", description = "最後にアンリンクされた日時（NULL = 現在リンク中）" },
    { name = "relinked_at", type = "TIMESTAMP", mode = "NULLABLE", description = "最後に再リンクされた日時（NULL = 再リンク経験なし）" },
    { name = "link_count", type = "INTEGER", mode = "REQUIRED", description = "リンク回数（再リンクのたびにインクリメント）" },
    { name = "last_fetched_at", type = "TIMESTAMP", mode = "REQUIRED", description = "バッチが API からこのレコードを取得した最後の日時" },
    { name = "updated_at", type = "TIMESTAMP", mode = "REQUIRED", description = "レコードの内容が実際に変化したときのみ更新する日時" },
    { name = "prev_month_cost", type = "FLOAT64", mode = "NULLABLE", description = "前月の請求金額（NULL = 未取得）" },
    { name = "cost_currency", type = "STRING", mode = "NULLABLE", description = "前月請求金額の通貨コード（例: USD）" },
    { name = "ever_billed", type = "BOOLEAN", mode = "REQUIRED", description = "これまでに一度でも課金実績があるか" },
    { name = "first_billed_month", type = "STRING", mode = "NULLABLE", description = "初回課金月（YYYY-MM 形式）" },
    { name = "billing_newly_started", type = "BOOLEAN", mode = "REQUIRED", description = "当バッチ実行で初めて課金を確認したか" }
  ])

  depends_on = [google_bigquery_dataset.billing_data_test]
}

# ===================================================================
# Locals
# ===================================================================

locals {
  alerts = [
    for a in yamldecode(file("${path.module}/../alert/alerts.yaml"))["alerts"] : merge(a, {
      channel = lookup(var.alert_channel_overrides, a.name, a.channel)
    })
    if a.enabled
  ]
  batch_image_resolved   = var.batch_image != "" ? var.batch_image : "python:3.12-slim"
  notification_channels  = var.monitoring_channel_display_name != "" ? [data.google_monitoring_notification_channel.slack[0].name] : []
  billing_export_project = var.billing_export_project_id != "" ? var.billing_export_project_id : var.project_id
}

# ===================================================================
# GCS（Cloud Functions ソースコード格納バケット）
# ===================================================================

resource "google_storage_bucket" "function_source" {
  name                        = "${var.project_id}-function-source"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false
}

# ===================================================================
# Secret Manager
# ===================================================================

resource "google_secret_manager_secret" "slack_bot_token" {
  secret_id = "slack-bot-token"
  replication {
    auto {}
  }
}

# ===================================================================
# Artifact Registry
# ===================================================================

resource "google_artifact_registry_repository" "batch" {
  location      = var.region
  repository_id = "billing-link-detection"
  format        = "DOCKER"
}

# ===================================================================
# Service Accounts
# ===================================================================

resource "google_service_account" "billing_collector" {
  account_id   = "sa-billing-collector"
  display_name = "Billing Collector SA"
}

resource "google_service_account" "alert_handler" {
  account_id   = "sa-alert-handler"
  display_name = "Alert Handler SA"
}

resource "google_service_account" "scheduler" {
  account_id   = "sa-scheduler"
  display_name = "Scheduler SA"
}

# ===================================================================
# IAM — プロジェクトレベル
# ===================================================================

resource "google_project_iam_member" "collector_bq_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.billing_collector.email}"
}

resource "google_project_iam_member" "collector_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.billing_collector.email}"
}

resource "google_project_iam_member" "alert_bq_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.alert_handler.email}"
}

resource "google_project_iam_member" "alert_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.alert_handler.email}"
}

# ===================================================================
# IAM — BigQuery データセットレベル（同プロジェクト内）
# ===================================================================

resource "google_bigquery_dataset_iam_member" "collector_data_editor" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.billing_data.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.billing_collector.email}"
}

resource "google_bigquery_dataset_iam_member" "alert_data_viewer" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.billing_data.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.alert_handler.email}"
}

# ===================================================================
# IAM — Billing Export データセット（クロスプロジェクト）
# billing_export_project_id が設定されている場合のみ作成
# Terraform SA に billing_export_project_id の roles/bigquery.admin が必要
# ===================================================================

resource "google_bigquery_dataset_iam_member" "collector_reads_billing_export" {
  count      = var.billing_export_project_id != "" ? 1 : 0
  project    = var.billing_export_project_id
  dataset_id = var.billing_export_dataset
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.billing_collector.email}"
}

resource "google_bigquery_dataset_iam_member" "alert_reads_billing_export" {
  count      = var.billing_export_project_id != "" ? 1 : 0
  project    = var.billing_export_project_id
  dataset_id = var.billing_export_dataset
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.alert_handler.email}"
}

# ===================================================================
# IAM — Terraform SA の actAs（Cloud Run / Cloud Functions 更新に必要）
# terraform_sa_email が設定されている場合のみ作成
# ===================================================================

resource "google_service_account_iam_member" "terraform_acts_as_collector" {
  count              = var.terraform_sa_email != "" ? 1 : 0
  service_account_id = google_service_account.billing_collector.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.terraform_sa_email}"
}

resource "google_service_account_iam_member" "terraform_acts_as_alert_handler" {
  count              = var.terraform_sa_email != "" ? 1 : 0
  service_account_id = google_service_account.alert_handler.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.terraform_sa_email}"
}

# Cloud Functions Gen2 はビルド時に Cloud Build を使用する。
# Cloud Build は Compute Engine デフォルト SA として動作するため、
# Terraform SA にはこの SA への actAs 権限も必要。
data "google_project" "project" {
  project_id = var.project_id
}

resource "google_service_account_iam_member" "terraform_acts_as_compute_default" {
  count              = var.terraform_sa_email != "" ? 1 : 0
  service_account_id = "projects/${var.project_id}/serviceAccounts/${data.google_project.project.number}-compute@developer.gserviceaccount.com"
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.terraform_sa_email}"
}

# GCP IAM の伝播遅延を吸収するための待機
# actAs 権限付与直後に Cloud Run / Cloud Functions を更新すると 403 になるため
# triggers により IAM バインディングが変わるたびに time_sleep が再作成され、常に待機が走る
resource "time_sleep" "iam_propagation" {
  count = var.terraform_sa_email != "" ? 1 : 0
  depends_on = [
    google_service_account_iam_member.terraform_acts_as_collector,
    google_service_account_iam_member.terraform_acts_as_alert_handler,
    google_service_account_iam_member.terraform_acts_as_compute_default,
  ]
  create_duration = "120s"
  triggers = {
    collector_iam       = var.terraform_sa_email != "" ? google_service_account_iam_member.terraform_acts_as_collector[0].id : ""
    alert_iam           = var.terraform_sa_email != "" ? google_service_account_iam_member.terraform_acts_as_alert_handler[0].id : ""
    compute_default_iam = var.terraform_sa_email != "" ? google_service_account_iam_member.terraform_acts_as_compute_default[0].id : ""
  }
}

# ===================================================================
# IAM — Secret Manager
# ===================================================================

resource "google_secret_manager_secret_iam_member" "alert_secret_accessor" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.slack_bot_token.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.alert_handler.email}"
}

# ===================================================================
# Cloud Run Jobs
# ===================================================================

resource "google_cloud_run_v2_job" "billing_collector" {
  name     = "billing-collector"
  location = var.region

  template {
    task_count = 1
    template {
      max_retries     = 0
      timeout         = "600s"
      service_account = google_service_account.billing_collector.email
      containers {
        image = local.batch_image_resolved
        env {
          name  = "GCP_PROJECT_ID"
          value = var.project_id
        }
        env {
          name  = "BQ_DATASET"
          value = var.bq_dataset
        }
        env {
          name  = "PARENT_BILLING_ACCOUNT"
          value = var.parent_billing_account
        }
        env {
          name  = "BILLING_EXPORT_PROJECT_ID"
          value = local.billing_export_project
        }
        env {
          name  = "BILLING_EXPORT_DATASET"
          value = var.billing_export_dataset
        }
        env {
          name  = "BILLING_EXPORT_TABLE"
          value = var.billing_export_table
        }
        env {
          name  = "BATCH_TYPE"
          value = "daily"
        }
      }
    }
  }

  depends_on = [
    google_artifact_registry_repository.batch,
    time_sleep.iam_propagation,
  ]
}

resource "google_cloud_run_v2_job" "billing_cost_updater" {
  name     = "billing-cost-updater"
  location = var.region

  template {
    task_count = 1
    template {
      max_retries     = 0
      timeout         = "600s"
      service_account = google_service_account.billing_collector.email
      containers {
        image = local.batch_image_resolved
        env {
          name  = "GCP_PROJECT_ID"
          value = var.project_id
        }
        env {
          name  = "BQ_DATASET"
          value = var.bq_dataset
        }
        env {
          name  = "PARENT_BILLING_ACCOUNT"
          value = var.parent_billing_account
        }
        env {
          name  = "BILLING_EXPORT_PROJECT_ID"
          value = local.billing_export_project
        }
        env {
          name  = "BILLING_EXPORT_DATASET"
          value = var.billing_export_dataset
        }
        env {
          name  = "BILLING_EXPORT_TABLE"
          value = var.billing_export_table
        }
        env {
          name  = "BATCH_TYPE"
          value = "monthly"
        }
      }
    }
  }

  depends_on = [
    google_artifact_registry_repository.batch,
    time_sleep.iam_propagation,
  ]
}

# ===================================================================
# IAM — Cloud Run Jobs（Scheduler が実行するための権限）
# ===================================================================

resource "google_cloud_run_v2_job_iam_member" "scheduler_runs_collector" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.billing_collector.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_cloud_run_v2_job_iam_member" "scheduler_runs_cost_updater" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.billing_cost_updater.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

# ===================================================================
# Cloud Functions Gen2（汎用アラートハンドラ）
# ===================================================================

data "archive_file" "alert_handler_source" {
  type        = "zip"
  source_dir  = "${path.module}/../alert"
  output_path = "${path.module}/.tmp/alert-handler.zip"
  excludes    = ["alerts.yaml", ".env.example", "__pycache__"]
}

resource "google_storage_bucket_object" "function_zip" {
  name   = "alert-handler-${data.archive_file.alert_handler_source.output_md5}.zip"
  bucket = google_storage_bucket.function_source.name
  source = data.archive_file.alert_handler_source.output_path
}

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
    timeout_seconds       = 120
    available_memory      = "256M"
    service_account_email = google_service_account.alert_handler.email

    environment_variables = {
      GCP_PROJECT_ID            = var.project_id
      BQ_DATASET                = var.bq_dataset
      BILLING_EXPORT_PROJECT_ID = local.billing_export_project
      BILLING_EXPORT_DATASET    = var.billing_export_dataset
      BILLING_EXPORT_TABLE      = var.billing_export_table
    }

    secret_environment_variables {
      key        = "SLACK_BOT_TOKEN"
      project_id = var.project_id
      secret     = google_secret_manager_secret.slack_bot_token.secret_id
      version    = "latest"
    }
  }

  depends_on = [time_sleep.iam_propagation]
}

# ===================================================================
# IAM — Cloud Functions（Scheduler が呼び出すための権限）
# ===================================================================

resource "google_cloudfunctions2_function_iam_member" "scheduler_invokes_alert" {
  project        = var.project_id
  location       = var.region
  cloud_function = google_cloudfunctions2_function.alert_handler.name
  role           = "roles/cloudfunctions.invoker"
  member         = "serviceAccount:${google_service_account.scheduler.email}"
}

# ===================================================================
# Cloud Scheduler — バッチジョブ
# ===================================================================

resource "google_cloud_scheduler_job" "billing_collector_daily" {
  name      = "billing-collector-daily"
  schedule  = "0 2 * * *"
  time_zone = "Asia/Tokyo"
  region    = var.region

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/billing-collector:run"
    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }
}

resource "google_cloud_scheduler_job" "billing_cost_updater_monthly" {
  name      = "billing-cost-updater-monthly"
  schedule  = "0 3 5 * *"
  time_zone = "Asia/Tokyo"
  region    = var.region

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/billing-cost-updater:run"
    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }
}

# ===================================================================
# Cloud Scheduler — アラート（alerts.yaml の enabled エントリ数分を自動生成）
# ===================================================================

resource "google_cloud_scheduler_job" "alerts" {
  for_each  = { for a in local.alerts : a.name => a }
  name      = "alert-${each.key}"
  schedule  = each.value.schedule
  time_zone = "Asia/Tokyo"
  region    = var.region

  http_target {
    http_method = "POST"
    uri         = google_cloudfunctions2_function.alert_handler.url
    body = base64encode(jsonencode({
      query   = each.value.query
      channel = each.value.channel
      message = each.value.message
    }))
    oidc_token {
      service_account_email = google_service_account.scheduler.email
    }
  }
}

# ===================================================================
# Cloud Monitoring — ログベースメトリクス
# ===================================================================

resource "google_logging_metric" "billing_collector_error" {
  name   = "billing-collector-error-count"
  filter = "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"billing-collector\" AND severity>=ERROR"
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

resource "google_logging_metric" "billing_cost_updater_error" {
  name   = "billing-cost-updater-error-count"
  filter = "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"billing-cost-updater\" AND severity>=ERROR"
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

resource "google_logging_metric" "alert_handler_error" {
  name   = "alert-handler-error-count"
  filter = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"alert-handler\" AND severity>=ERROR"
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

# ===================================================================
# Cloud Monitoring — 通知チャンネル（Phase 4 で手動作成後に data ソース参照）
# monitoring_channel_display_name が未設定の場合は通知なし（アラートポリシーは作成される）
# ===================================================================

data "google_monitoring_notification_channel" "slack" {
  count        = var.monitoring_channel_display_name != "" ? 1 : 0
  display_name = var.monitoring_channel_display_name
  project      = var.project_id
}

# ===================================================================
# Cloud Monitoring — アラートポリシー
# ===================================================================

resource "google_monitoring_alert_policy" "billing_collector_error" {
  display_name = "Billing Collector Job Error"
  combiner     = "OR"

  conditions {
    display_name = "ERROR log detected"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/billing-collector-error-count\" AND resource.type=\"cloud_run_job\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_RATE"
      }
    }
  }

  notification_channels = local.notification_channels
  alert_strategy {
    auto_close = var.monitoring_auto_close
  }
}

resource "google_monitoring_alert_policy" "billing_cost_updater_error" {
  display_name = "Billing Cost Updater Job Error"
  combiner     = "OR"

  conditions {
    display_name = "ERROR log detected"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/billing-cost-updater-error-count\" AND resource.type=\"cloud_run_job\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_RATE"
      }
    }
  }

  notification_channels = local.notification_channels
  alert_strategy {
    auto_close = var.monitoring_auto_close
  }
}

resource "google_monitoring_alert_policy" "alert_handler_error" {
  display_name = "Alert Handler Function Error"
  combiner     = "OR"

  conditions {
    display_name = "ERROR log detected"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/alert-handler-error-count\" AND resource.type=\"cloud_run_revision\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_RATE"
      }
    }
  }

  notification_channels = local.notification_channels
  alert_strategy {
    auto_close = var.monitoring_auto_close
  }
}
