# 設計議論の記録（採用・却下）

設計過程で検討した選択肢と、採用・却下の理由をまとめる。

______________________________________________________________________

## 決定一覧

| # | テーマ | 採用 | 却下 |
|---|---|---|---|
| 1 | バッチ実行頻度 | 日次 | 週次 |
| 2 | アラート通知の実装方式 | 別バッチ（Cloud Functions）がBQクエリして通知 | Cloud Runが検知時に直接通知 ※当初推奨→撤回 |
| 3 | アラート条件の記述言語 | SQL（BigQuery WHERE句） | Python |
| 4 | アラート設定ファイルの形式 | YAML | スプレッドシート |
| 5 | アラートのインフラ基盤 | Cloud Functions | BigQuery スケジュールクエリ |
| 6 | アラート基盤：Cloud Monitoring ネイティブ vs Cloud Functions | Cloud Functions（現行アーキテクチャを維持） | Cloud Monitoring Alerting Policy に寄せる |
| 7 | Cloud Functions の設計方式 | 汎用ハンドラ1つ + Terraform for_each | 個別Function / コード生成スクリプト |
| 8 | CI/CD 基盤 | GitHub Actions + Workload Identity Federation | Cloud Build |
| 9 | Slack 通知方式 | Bot Token + `chat.postMessage` API | Incoming Webhook |
| 10 | 月次バッチの実装方式 | 単一コンテナ（`BATCH_TYPE` 環境変数で分岐） | 日次・月次で別コンテナ（Dockerfile 分割） |
| 11 | Billing Export の格納先プロジェクト | 分析システムとは別の専用プロジェクト（2プロジェクト構成） | 分析システムプロジェクトに同居 |

______________________________________________________________________

## 1. バッチ実行頻度

**採用**: 日次実行\
**却下**: 週次実行

**採用理由（日次）**

- プロジェクトの追加・削除・課金ステータス変更に翌日気づける
- `unlinked_at` / `relinked_at` の精度が「日次」になり分析価値が高い
- API呼び出し量が微小なため週次との実質コスト差はゼロ
- バッチ失敗を24時間以内に検知・対応できる

**却下理由（週次）**

- 最大7日のタイムラグが発生する
- 正確な変更日を特定できない
- バッチ失敗に最大1週間気づかないリスクがある

**頻度変更時の柔軟性**

要件が変わり週次への変更が必要になった場合、以下の変更だけで対応できる。コードの変更は不要。

| 変更対象 | 変更内容 |
|---|---|
| Cloud Scheduler cron（`billing-collector`） | `0 2 * * *` → `0 2 * * 1`（例：毎週月曜）など任意の曜日に変更 |
| `alerts.yaml` の `billing_newly_started` の `schedule` | バッチ実行日に合わせて変更（下記「注意」参照） |

> **注意**: `billing_newly_started` フラグは**次回バッチ実行時にリセット**される設計のため、バッチが週1回になるとフラグが翌日以降も `TRUE` のまま残り、アラートが毎日通知され続ける。`alerts.yaml` の `billing_newly_started` の `schedule` をバッチ実行日と揃えることで防止できる。詳細は [`alert_design.md`](./alert_design.md) Section 7 を参照。
>
> MERGE SQL・UNLINKED 検知・Step 1 リセットロジック（`DATE(last_fetched_at) < CURRENT_DATE()`）はいずれも頻度に依存しないため、Python コード・SQL に変更は不要。

______________________________________________________________________

## 2. アラート通知の実装方式

**採用**: 別バッチ（Cloud Functions）がBigQueryをクエリして通知\
**却下（当初推奨→撤回）**: Cloud Run Jobsがデータ検知した瞬間に直接通知

当初はCloud Run直接通知を推奨したが、以下の観点で撤回し別バッチ方式に変更した。

**採用理由（別バッチ）**

- 通知のオン/オフは `gcloud scheduler jobs pause/resume` のみで完結。コード・デプロイ不要
- アラート種別ごとに独立して制御できる（一部だけ止める、など）
- データ収集と通知の関心分離（それぞれが独立して失敗・回復できる）
- 日次バッチなのでいずれにせよ「即時性」の差はない

**却下理由（Cloud Run直接通知）**

- 通知だけ止めたい場合にデータ収集ジョブを止めなければならない
- アラート種別ごとの独立した制御ができない
- 通知先の変更や条件変更がコード修正を伴う

______________________________________________________________________

## 3. アラート条件の記述言語・管理方式

**採用**: YAML にフル SQL を直接記述\
**却下**: Python による SQL 生成\
**却下**: SQL を外部ファイル化し YAML からファイルパスを参照

**採用理由（YAML にフル SQL を直接記述）**

- 1アラートの定義（スケジュール・条件・通知先・メッセージ）が1ファイルに集約され、追加・変更が YAML 編集だけで完結する
- BigQuery コンソールにそのまま貼り付けて動作確認できる
- 条件変更で Docker イメージの再ビルド・デプロイが不要
- Python コードは `str.format()` による変数展開のみ。実装・テストコストが最小

**却下理由（Python による SQL 生成）**

- SQL はすでに「条件を表現する DSL」であり、その上に独自 DSL を重ねても価値が薄い
- Python の SQL ビルダー自体の実装・テスト・保守コストが発生する
- JOIN・集計・サブクエリなど複雑な条件に対応するにはビルダーの拡張が必要になり、結果的に SQL に近づいていく
- 生成された SQL を確認するために Python を実行する手順が必要になり、BigQuery コンソールでの直接デバッグができなくなる

**却下理由（SQL 外部ファイル化）**

- アラートの追加・変更に YAML と SQL の 2 ファイルを編集する必要が生じ、「YAML 編集だけで完結する」という設計の核心が崩れる
- Cloud Functions の ZIP 展開後のファイルパスへの依存が生まれ、Terraform の `archive_file` 設定も複雑になる
- 現在のアラート数（3件）・クエリ規模（数行〜十数行）では管理コストに対してメリットがない
- **再検討の目安**: アラートが 10件超 または 1クエリが 50行超になった場合は外部ファイル化を検討する価値がある

______________________________________________________________________

## 4. アラート設定ファイルの形式

**採用**: YAML\
**検討**: スプレッドシート

**採用理由（YAML）**

- Terraformが `yamldecode(file("alerts.yaml"))` でネイティブに読み込める
- Gitリポジトリで管理でき、変更履歴・コードレビューが可能
- `for_each` と組み合わせてTerraformリソースを動的生成できる

**スプレッドシートの利点と却下理由**

- 非エンジニアが直感的に編集できる利点はある
- ただしTerraformとのCI/CD連携が複雑になる（GCS経由やAPI連携が別途必要）ため不採用

______________________________________________________________________

## 5. アラートのインフラ基盤

**採用**: Cloud Functions\
**却下**: BigQuery スケジュールクエリ

**採用理由（Cloud Functions）**

- クエリ結果に応じてSlack通知などの外部アクションを直接実行できる
- 軽量な処理に最適で無料枠内に完全に収まる

**却下理由（BigQuery スケジュールクエリ）**

- クエリ結果を別テーブルに書き込む機能のみであり、Slack通知などの外部アクションを直接起こせない
- 通知のためにPub/Sub + Cloud Functionsとの連携が別途必要になり複雑さが増す

______________________________________________________________________

## 6. アラート基盤：Cloud Monitoring ネイティブ vs Cloud Functions

**採用**: Cloud Functions（現行アーキテクチャを維持）\
**検討・却下**: Cloud Monitoring の Alerting Policy + Notification Channel に寄せる

Cloud Monitoring 中心の構成では、通知先管理・GitHub Actions 連携などがコード不要でネイティブに実現できるため一時検討した。

**却下理由**

- 通知先は alerts.yaml で管理すれば十分であり、Cloud Monitoring の Notification Channel 管理と比べて運用上の差はほぼない
- **最重要要件がSQLによるアラート条件の柔軟な変更**であり、Cloud Monitoring はメトリクス閾値ベースの設計のため SQL 条件を直接扱えない
- SQL 条件を Cloud Run の Python コードに戻すと、条件変更のたびに再デプロイが必要になる

**結論**: 「SQL で条件を定義し、デプロイなしで変更できる」を最優先とする場合、alerts.yaml + Cloud Functions + Terraform for_each の現構成が最適解。

______________________________________________________________________

## 7. Cloud Functions の設計方式

**採用**: 汎用ハンドラ1つ + Terraform `for_each` でアラートごとにSchedulerジョブを自動生成\
**却下**: アラートごとに個別のFunctionコードを作成\
**却下**: 設定ファイルからFunctionコードとTerraformファイルをコード生成スクリプトで生成

**採用理由（汎用ハンドラ + for_each）**

- Functionコードは1つだけ管理すればよい
- アラートの追加・変更・削除はYAML編集 + `terraform apply` のみで完結
- コード生成スクリプトが不要（Terraformのfor_eachで同等の自動化を実現できる）
- コード生成スクリプト自体のメンテナンスコストが発生しない

**却下理由（個別Function）**

- アラートを追加するたびにFunctionコードの追加・管理が必要

**却下理由（コード生成）**

- 汎用Function + for_eachで同じことが実現できる
- コード生成スクリプト自体のメンテナンスが必要になる
- 生成後のコードとスクリプトの二重管理が発生する

______________________________________________________________________

## 8. CI/CD 基盤

**採用**: GitHub Actions + Workload Identity Federation\
**却下**: Cloud Build

**採用理由（GitHub Actions）**

- Terraform / Docker / Python テスト向けの公式・サードパーティアクションが豊富
- ワークフロー定義（`.github/workflows/`）をコードと同じリポジトリで管理でき、PR レビューの対象にできる
- PR ごとのチェック結果表示・コメント連携が自然に機能する
- GitHub Secrets / Variables で Terraform 変数を管理でき、ローカル開発と CI/CD で設定を統一しやすい

**却下理由（Cloud Build）**

- GCP ネイティブで追加認証設定が不要という利点はあるが、Workload Identity Federation は初回設定のみで以降はメンテナンス不要
- このプロジェクトの処理（Billing API・BigQuery・Cloud Run）はすべてパブリックエンドポイントであり、Cloud Build の VPC アクセス優位性が活きない
- ビルド履歴の確認が GCP コンソールに限定され、GitHub との行き来が増える

______________________________________________________________________

## 9. Slack 通知方式

**採用**: Slack Bot Token + `chat.postMessage` API\
**却下**: Incoming Webhook

**採用理由（Bot Token）**

- アラートごとに異なるチャンネルへ通知する設計（営業向け／CS向け／システムエラー）に必要
- リクエストごとに `channel` パラメータを指定して送信先を切り替えられる
- 1つの Bot Token で複数チャンネルをカバーできるため、Secret Manager の管理コストが最小

**却下理由（Incoming Webhook）**

- Webhook URL はチャンネル単位で固定される
- アラート3件（営業1・CS2・システム1の最大4チャンネル）分の Webhook URL を別々に Secret Manager に登録する必要があり、運用が煩雑
- チャンネル追加・変更のたびに Webhook URL の再発行と Secret 更新が必要になる

______________________________________________________________________

## 10. 月次バッチの実装方式

**採用**: 単一コンテナ（`batch/`）＋ `BATCH_TYPE` 環境変数で動作切り替え\
**却下**: 日次・月次で別コンテナ（Dockerfile 分割）

**採用理由（単一コンテナ）**

- 日次・月次バッチの依存パッケージが完全に同一（`google-cloud-bigquery` / `google-cloud-billing` / `google-cloud-logging`）のため、別コンテナにしても `requirements.txt` の内容が変わらない
- CI/CD で build/push するイメージが1つで済み、`TF_VAR_batch_image` を追加せずに済む
- BQクライアント初期化・構造化ログ設定・`@batch_run_at` 固定・エラーハンドリングなどの共通処理を重複なく共有できる
- `BATCH_TYPE` による分岐はシンプルで、`main.py` の可読性を損なわない

**却下理由（別コンテナ）**

- 依存パッケージが同一のため分離コストに対してメリットがない
- CI/CD でイメージを2回 build/push する必要が生じる
- Terraform に `var.batch_image_daily` / `var.batch_image_monthly` の2変数が必要になり `deploy.yml` の env セクションも増える

**実装パターン（`batch/main.py` の起動ロジック）**

```python
import os

def main():
    batch_type = os.environ.get("BATCH_TYPE", "daily")
    if batch_type == "monthly":
        run_monthly_batch()   # batch_name = "cost-updater"
    else:
        run_daily_batch()     # batch_name = "billing-collector"

if __name__ == "__main__":
    main()
```

______________________________________________________________________

## 11. Billing Export の格納先プロジェクト

**採用**: 分析システムとは別の専用プロジェクト（2プロジェクト構成）\
**却下**: 分析システムプロジェクトに Billing Export を同居させる

**採用理由（専用プロジェクト）**

- **GCP の UI 制約**: Billing Export の設定画面（GCP コンソールの「請求先アカウント」→「BigQuery へのエクスポート」）は、**エクスポート先として選択できるプロジェクトが「その親請求先アカウントに直接リンクされているプロジェクト」のみ** に制限されている。分析システムプロジェクトは dragon.jp の親請求先アカウントではなく社内の別請求先アカウントにリンクされているため、Export 先として選択できない
- **設計の安定性**: Export 先プロジェクトを差し替える場合（例: 親請求先アカウントの切替）に、分析システムのインフラを巻き込まずに完結できる
- **セキュリティ境界**: Billing Export テーブルは親請求先アカウント管理者の「持ち物」。分析システム側の Terraform や Cloud Run が誤って Export テーブルを書き換えないよう IAM 境界を分けたい

**却下理由（同居）**

- 上記 UI 制約を突破できない（技術的に不可能）
- 仮に同居できたとしても、Export 先変更時に分析システムも巻き込まれる運用リスクがある

**アーキテクチャ上の帰結**

- `sa-billing-collector` はクロスプロジェクトで Export 専用プロジェクトの BigQuery データセットに `roles/bigquery.dataViewer` を付与される
- `billing_export_project_id` 変数を `terraform.tfvars` に設定することで、クロスプロジェクトの BigQuery 参照が有効化される（空文字列のときは `google_project_iam_member` リソースが `count = 0` で生成されない）
- Terraform SA も Export 専用プロジェクトに `roles/bigquery.admin` が必要（`billing_project_links` テーブルの IAM 設定のため）

詳細構成図は [architecture.md §7](./architecture.md#7-%E3%83%97%E3%83%AD%E3%82%B8%E3%82%A7%E3%82%AF%E3%83%88%E5%88%86%E9%9B%A22-%E3%83%97%E3%83%AD%E3%82%B8%E3%82%A7%E3%82%AF%E3%83%88%E6%A7%8B%E6%88%90) を参照。
