output "billing_collector_sa_email" {
  description = "日次・月次バッチの SA（Phase 4-1: 親請求先アカウントへの Billing Account Viewer 付与に使用）"
  value       = google_service_account.billing_collector.email
}

output "alert_handler_sa_email" {
  description = "アラートハンドラの SA"
  value       = google_service_account.alert_handler.email
}

output "scheduler_sa_email" {
  description = "Cloud Scheduler の SA"
  value       = google_service_account.scheduler.email
}

output "batch_job_name" {
  description = "日次バッチの Cloud Run Job 名（手動実行時に使用）"
  value       = google_cloud_run_v2_job.billing_collector.name
}

output "bq_dataset_id" {
  description = "BigQuery 本番データセット ID"
  value       = google_bigquery_dataset.billing_data.dataset_id
}

output "artifact_registry_repo" {
  description = "Artifact Registry リポジトリ URI（Docker push 先）"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.batch.repository_id}"
}

output "alert_handler_url" {
  description = "Cloud Functions アラートハンドラの URL"
  value       = google_cloudfunctions2_function.alert_handler.url
}
