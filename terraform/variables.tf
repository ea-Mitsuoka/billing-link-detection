variable "project_id" {
  description = "GCP プロジェクト ID"
  type        = string
}

variable "region" {
  description = "デフォルトリージョン"
  type        = string
  default     = "asia-northeast1"
}

variable "parent_billing_account" {
  description = "親請求先アカウント ID（XXXXXX-YYYYYY-ZZZZZZ 形式）"
  type        = string
}

variable "bq_dataset" {
  description = "BigQuery データセット名（本番）"
  type        = string
  default     = "billing_data"
}

variable "billing_export_dataset" {
  description = "Cloud Billing Export の BigQuery データセット名"
  type        = string
}

variable "billing_export_table" {
  description = "Cloud Billing Export のテーブル名（例: gcp_billing_export_v1_XXXXXX）"
  type        = string
}

variable "monitoring_slack_channel" {
  description = "Cloud Monitoring エラー通知先 Slack チャンネル名"
  type        = string
  default     = "#alerts-gcp-billing"
}

variable "batch_image" {
  description = "バッチコンテナイメージ URI（CI/CD が terraform apply 時に TF_VAR_batch_image で渡す）"
  type        = string
  default     = ""
}

variable "monitoring_auto_close" {
  description = "エラーログが出なくなってからインシデントを自動クローズするまでの時間"
  type        = string
  default     = "86400s"
}

variable "billing_export_project_id" {
  description = "Billing Export 専用プロジェクトの ID（分析システムと別プロジェクトの場合に設定）"
  type        = string
  default     = ""
}

variable "monitoring_channel_display_name" {
  description = "Cloud Monitoring Slack 通知チャンネルの display_name（Phase 4 で手動作成後に設定）"
  type        = string
  default     = ""
}

variable "terraform_sa_email" {
  description = "Terraform を実行する SA のメールアドレス（Cloud Run / Cloud Functions 更新時に actAs 権限が必要）"
  type        = string
  default     = ""
}

variable "alert_channel_overrides" {
  description = "alerts.yaml の channel を上書きするマップ。キー: アラート名、値: Slack チャンネル名。未指定のアラートは alerts.yaml の値をそのまま使用"
  type        = map(string)
  default     = {}
}
