# 用語集

このリポジトリで頻出する用語を、**初見の人** が誤解しないよう定義する。
GCP の一般用語と、このシステム独自の用語の両方を含む。

______________________________________________________________________

## GCP 課金まわりの用語

### 親請求先アカウント（Parent Billing Account）

販売パートナー（請求代行事業者）が GCP から発行される、**サブアカウントを束ねる上位の請求先アカウント**。本システムでは `dragon.jp` がこれにあたる。

- フォーマット: `XXXXXX-YYYYYY-ZZZZZZ`
- このアカウント自体に直接プロジェクトはリンクされない（基本的に）
- 親アカウントから「標準コストエクスポート」を有効化すると、配下の **全サブアカウント・全プロジェクト** の課金データが BigQuery に出力される
- 本システムで監視対象とする中心概念

### サブアカウント（Sub Account / Reseller Sub Account）

販売パートナーが顧客ごとに発行する請求先アカウント。**顧客 1 社 = サブアカウント 1 つ** で運用するのが一般的。

- フォーマット: 親と同じく `XXXXXX-YYYYYY-ZZZZZZ`
- 親請求先アカウント直下に複数ぶら下がる
- 顧客の GCP プロジェクトはこのサブアカウントにリンクされる
- 解約時にはサブアカウント単位で `closed` 状態に遷移しうる

### プロジェクト（Project）

GCP の最小課金単位。顧客が複数持つこともある。

- フォーマット: `your-project-id`（小文字英数字とハイフン）
- 1 つのプロジェクトは **1 つのサブアカウント** にしかリンクできない
- リンクを切る（解除する）こともできる → このシステムでは UNLINKED として記録

### 標準コストエクスポート（Standard Usage Cost Export）

Cloud Billing が BigQuery に **日次で** 課金データを書き出す GCP 公式機能。

- 親請求先アカウント単位で有効化する
- 出力テーブル名: `gcp_billing_export_v1_XXXXXX_YYYYYY`
- スキーマ詳細: [data_source_investigation.md](./data_source_investigation.md)
- 「標準」と「詳細使用量」の 2 種類があるが、本システムは標準のみ使う

### `billing_account_id`（Billing Export 内）

Billing Export テーブルに含まれるカラム。**販売パートナー親アカウント配下では、ここに入るのはサブアカウント ID** で、親アカウント ID ではない。これは GCP 公式仕様（[GCP doc](https://docs.cloud.google.com/billing/docs/how-to/export-data-bigquery-tables/standard-usage)）で明記されている。

このため、本システムの `billing_project_links.sub_account_id` と直接 JOIN できる。

### `invoice.month`（Billing Export 内）

Billing Export テーブルの STRUCT カラム。請求対象月を `YYYYMM` 形式の文字列で持つ。

- 例: 2026 年 4 月分なら `"202604"`
- 月次バッチはこのカラムで前月分を抽出する

______________________________________________________________________

## このシステム独自の用語

### `billing_project_links` テーブル

**このシステムの主データテーブル**。サブアカウント × プロジェクトの組み合わせを 1 行で表す。

- 主キー: `(parent_account_id, sub_account_id, project_id)`
- 完全スキーマは [requirements.md](./requirements.md) §テーブル設計
- 日次バッチが MERGE で更新、月次バッチが `prev_month_cost` を更新

### `status` フィールド

`billing_project_links.status` が取りうる値：

| 値 | 意味 |
|---|---|
| `ACTIVE` | リンク中・課金有効・サブアカウントもオープン |
| `BILLING_DISABLED` | リンク中・サブアカウントもオープンだが、プロジェクトの課金が無効 |
| `SUB_CLOSED` | リンク中だがサブアカウント自体が閉鎖されている |
| `UNLINKED` | リンクが解除された（過去にリンクされていた記録は残す） |

状態遷移図は [architecture.md](./architecture.md) §状態遷移図 を参照。

### UNLINKED 検知

API で取得したサブアカウント・プロジェクト一覧に **「今回出てこなかった」既存レコード** を `status='UNLINKED'` に書き換える処理。

- 判定式: `status != 'UNLINKED' AND last_fetched_at < @batch_run_at`
- 物理削除ではなく **論理削除**（履歴を残すため）
- 再リンク時には `link_count` がインクリメントされ `relinked_at` が記録される

### 再リンク（Re-link）

過去に UNLINKED になったプロジェクトが再度同じサブアカウントにリンクされること。MERGE の `WHEN MATCHED AND T.status = 'UNLINKED'` 分岐で検知。

- `link_count` を +1
- `relinked_at` を更新
- `unlinked_at` は NULL に戻す（過去の値は失う設計）

### `ever_billed` フラグ

「このプロジェクトは **一度でも** 課金実績があるか」を示すフラグ。Billing Export の `SUM(cost) > 0` を満たすかで判定。

- 一度 TRUE になったら基本的に FALSE には戻らない
- 過去履歴を遡って判定するため、Billing Export 設定後に蓄積された期間の範囲でしか判定できないことに注意

### `billing_newly_started` フラグ

`ever_billed` が **FALSE → TRUE に変化したバッチ実行で TRUE になる** 一時フラグ。営業向けの「新規課金開始」アラート用。

- TRUE になった翌日のバッチで自動的に FALSE にリセットされる
- リセット条件: `DATE(last_fetched_at, 'Asia/Tokyo') < CURRENT_DATE('Asia/Tokyo')`
- アラートは日次でフラグが立った当日中に通知する設計（[alert_design.md](./alert_design.md) §7）

### `prev_month_cost`

前月の請求金額（USD など）。月次バッチが 5 日に前月分を集計してセットする。

- Billing Export が確定するまで数日かかる仕様のため、5 日に実行している
- 「今月の金額」は持たない（リアルタイム性は要件外）
- 0 円表示は **休眠** または **未活用リンク** のシグナル

### `first_billed_month`

そのプロジェクトの **初回課金月**（YYYY-MM 形式）。Billing Export 全期間を `MIN(invoice.month)` でスキャンして求める。

- `ever_billed=TRUE` なら必ず非 NULL
- 顧客の成長タイムライン分析に使う

______________________________________________________________________

## バッチ・コードまわりの用語

### 日次バッチ（billing-collector）

毎日 02:00 JST に走る Cloud Run Job。リンク情報の最新化と `ever_billed` 更新を担う。

- 環境変数 `BATCH_TYPE=daily`
- 処理ステップ詳細: [architecture.md](./architecture.md) §データフロー（日次バッチ）

### 月次バッチ（billing-cost-updater）

毎月 5 日 03:00 JST に走る Cloud Run Job。前月の `prev_month_cost` だけを更新する。

- 環境変数 `BATCH_TYPE=monthly`
- 日次バッチと **同じ Docker イメージ** を使い、`BATCH_TYPE` で動作分岐（[decisions.md](./decisions.md) §10）

### `@batch_run_at`

BigQuery クエリパラメータとして渡される、バッチ開始時刻（UTC）。**1 回のバッチ実行中はすべてのクエリで同じ値** を使うことで、トランザクション境界の判定を安定させる。

- Python 側: `datetime.now(timezone.utc)` を 1 回だけ呼んで保持
- SQL 側: `@batch_run_at` パラメータバインド

### `_tmp_billing_links` / `_tmp_monthly_cost`

バッチ実行ごとに `WRITE_TRUNCATE` で書き直される一時テーブル。MERGE の USING 句のソースに使う。

- 永続テーブルとして残るが、毎回上書きされるため監査用途には使えない

### アラートハンドラ（alert-handler）

Cloud Functions Gen2 で動く汎用 HTTP ハンドラ。

- 1 つの Function コードを **全アラートで共用**
- リクエストボディの `query` / `channel` / `message` をパラメータとして受ける
- `alerts.yaml` のエントリ数分の Cloud Scheduler ジョブが Terraform `for_each` で生成される

______________________________________________________________________

## 認証・IAM

### Workload Identity Federation（WIF）

GitHub Actions が GCP の Service Account を **キーファイルなしで** 借りる仕組み。

- 初回手動セットアップ: [initial_setup.md](./initial_setup.md) §2-5
- OIDC トークン → GCP SA Token に交換
- メリット: キー漏洩リスクなし、ローテーション不要

### サービスアカウント一覧

| SA | 用途 | 主な権限 |
|---|---|---|
| `sa-terraform` | Terraform 実行（人間 / GitHub Actions） | プロジェクト Admin 級 |
| `sa-billing-collector` | バッチが BigQuery / Billing API を叩く | `bigquery.dataEditor` / `billing.viewer`（親アカウント） |
| `sa-alert-handler` | Cloud Functions の実行 ID | `bigquery.dataViewer` / `secretmanager.secretAccessor` |
| `sa-scheduler` | Cloud Scheduler が Job / Function を叩く | `run.invoker` / `cloudfunctions.invoker` |

______________________________________________________________________

## YAML / Terraform

### `alerts.yaml`

`alert/alerts.yaml`。**全アラート定義をここに集約** している人間が編集する唯一のファイル。

- 1 エントリ = 1 アラート
- 必須キー: `name`, `enabled`, `schedule`, `channel`, `message`, `query`
- Terraform が `yamldecode` で読み込み、`for_each` で Cloud Scheduler ジョブを生成

### `for_each`

Terraform の機能。`alerts.yaml` のエントリ数だけ Cloud Scheduler リソースを動的生成する。

- アラート追加 → YAML 1 行追加 → `terraform apply` で Scheduler ジョブが自動作成
- アラート削除 → YAML から削除 → `terraform apply` で Scheduler ジョブが自動削除

### `archive_file`

Terraform の `data` ソース。`alert/` 配下を ZIP に固めて Cloud Storage にアップロード、Cloud Functions のソースとして使う。

- `alerts.yaml` などは `excludes` で除外
- ZIP の MD5 ハッシュで版管理（ソース変更時に自動再デプロイ）
