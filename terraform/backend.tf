terraform {
  backend "gcs" {
    # bucket はコード内に直接書けないため init 時に指定する
    #
    # ローカル実行:
    #   terraform init -backend-config="bucket=ea-yukihidemitsuoka2-tfstate"
    #
    # プロジェクト ID を変更する場合は上記バケット名を新しい project_id に合わせて変更する
    # CI/CD（deploy.yml）では vars.GCP_PROJECT_ID を使って自動的に組み立てる
    prefix = "billing-link-detection"
  }
}
