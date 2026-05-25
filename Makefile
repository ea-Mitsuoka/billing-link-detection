# billing-link-detection - 開発・運用コマンド
#
# このファイルは「頻繁に叩く・引数なし」のコマンドだけを集約する。
# 一度きりの初期構築は docs/initial_setup.md、gcloud オペレーションは docs/operations.md を参照。

.DEFAULT_GOAL := help

.PHONY: help install test fmt lint plan

help:  ## このヘルプを表示
	@printf "Usage: make <target>\n\nTargets:\n"
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

install:  ## 依存パッケージをインストール（batch + alert + dev）
	uv venv
	uv pip sync batch/requirements.txt alert/requirements.txt
	uv pip install pytest pytest-mock

test:  ## ユニットテストを実行（pytest）
	uv run pytest

fmt:  ## terraform fmt を再帰実行（書き換え）
	cd terraform && terraform fmt -recursive

lint:  ## CI と同等のチェック（terraform fmt-check + validate）
	cd terraform && terraform fmt -check -recursive
	cd terraform && terraform init -backend=false && terraform validate

plan:  ## terraform plan を実行（要 GCP_PROJECT_ID 環境変数）
	@test -n "$(GCP_PROJECT_ID)" || (echo "ERROR: GCP_PROJECT_ID env var is required" && exit 1)
	cd terraform && terraform init -backend-config="bucket=$(GCP_PROJECT_ID)-tfstate"
	cd terraform && terraform plan
