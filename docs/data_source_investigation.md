# データソース調査結果

## 0. なぜ2つのデータソースが必要か

このシステムは **Cloud Billing API** と **Cloud Billing Export（BigQuery）** の2つを併用する。それぞれが提供できる情報が異なるためであり、どちらか一方だけでは要件を満たせない。

| データソース | 提供できる情報 | 提供できない情報 |
|---|---|---|
| **Cloud Billing API** | サブアカウント一覧、各プロジェクトのリンク状態（billing_enabled / open）、アカウント階層 | **コスト金額**（課金額の集計 API は存在しない） |
| **Cloud Billing Export（BigQuery）** | プロジェクト・サービス単位の課金金額、過去の請求履歴 | リンク状態（billing_enabled の現在値）、サブアカウント階層 |

**Cloud Billing API 単体では課金金額を取得できない**（[requirements.md §9-4](./requirements.md) 参照）。課金額の取得には Billing Export を BigQuery に出力し、SQL で集計するしかない。逆に、リンク状態やアカウント階層はエクスポートデータに含まれないため Billing API が必要。

この制約がシステム全体の「2データソース構成」の根本的な理由であり、`billing_project_links` テーブルも2つのソースから別々に書き込む設計になっている（[architecture.md](./architecture.md) 参照）。

______________________________________________________________________

## 1. カラムとデータソースの対応

`billing_project_links` テーブルの各カラムは、以下の2つのデータソースから取得する。

| カラム | データソース | 取得方法 |
|---|---|---|
| parent_account_id | Cloud Billing API | gcloud / Python Client Library |
| sub_account_id | Cloud Billing API | gcloud / Python Client Library |
| sub_account_name | Cloud Billing API | gcloud / Python Client Library |
| billing_enabled | Cloud Billing API | gcloud / Python Client Library |
| sub_account_open | Cloud Billing API | gcloud / Python Client Library |
| status | バッチ側で計算 | last_fetched_at の更新有無から判定 |
| linked_at | バッチ側で計算 | 初回取得時のタイムスタンプ |
| unlinked_at | バッチ側で計算 | APIから消えたタイムスタンプ |
| relinked_at | バッチ側で計算 | 再登場したタイムスタンプ |
| link_count | バッチ側で計算 | リンク・アンリンク回数のカウント |
| last_fetched_at | バッチ側で付与 | バッチ実行時刻 |
| updated_at | バッチ側で付与 | 内容変化時のバッチ実行時刻 |
| prev_month_cost | Cloud Billing Export (BQ) | BQクエリで集計 |
| cost_currency | Cloud Billing Export (BQ) | BQクエリで取得（通常 USD） |
| ever_billed | Cloud Billing Export (BQ) | BQクエリで集計 |
| first_billed_month | Cloud Billing Export (BQ) | BQクエリで集計 |
| billing_newly_started | バッチ側で計算 | ever_billed の遷移検知 |

______________________________________________________________________

## 2. 推奨テーブル定義

**テーブル名**: `billing_project_links`

| カラム名 | データ型 | 説明 | 必須 |
|---|---|---|---|
| parent_account_id | STRING | 親請求先アカウントID | 必須 |
| sub_account_id | STRING | 請求先サブアカウントID | 必須 |
| sub_account_name | STRING | サブアカウントの表示名（顧客名など） | 推奨 |
| project_id | STRING | Google Cloud プロジェクトID | 必須 |
| billing_enabled | BOOLEAN | 課金有効状態（最新取得時点） | 推奨 |
| sub_account_open | BOOLEAN | サブアカウントがオープンかどうか | 推奨 |
| status | STRING | `ACTIVE` / `UNLINKED` / `BILLING_DISABLED` / `SUB_CLOSED` | 必須 |
| linked_at | TIMESTAMP | 最初にリンクを確認した日時 | 推奨 |
| unlinked_at | TIMESTAMP | 最後にアンリンクされた日時（NULL = 現在リンク中） | 推奨 |
| relinked_at | TIMESTAMP | 最後に再リンクされた日時 | 推奨 |
| link_count | INTEGER | リンク回数（再リンク時にインクリメント） | 任意 |
| last_fetched_at | TIMESTAMP | バッチがBilling APIからこのレコードのデータを取得した最後の日時。毎バッチ実行時にAPIレスポンスに含まれた全プロジェクトを更新する。APIに現れなくなったプロジェクトは更新されないため、UNLINKED検知に使用する | 必須 |
| updated_at | TIMESTAMP | レコードの内容（ステータス・課金状態・リンク情報など）が実際に変化したときのみ更新する日時。last_fetched_at と異なり、変化がなければ更新されない | 必須 |
| prev_month_cost | FLOAT64 | 前月の請求金額。NULL = 未取得 | 推奨 |
| cost_currency | STRING | 前月請求金額の通貨コード（例: `USD`）。prev_month_cost と対応する。NULL = 未取得 | 推奨 |
| ever_billed | BOOLEAN | 累積課金実績の有無（一度でも課金があればTRUE） | 推奨 |
| first_billed_month | STRING | 初回課金月（YYYY-MM形式。ever_billed=FALSEはNULL） | 推奨 |
| billing_newly_started | BOOLEAN | 当バッチで初めて課金を確認したか（遷移検知フラグ。次バッチでFALSEにリセット） | 推奨 |

**主キー**: `(parent_account_id, sub_account_id, project_id)`

______________________________________________________________________

## 3. Billing API の手動確認（gcloudコマンド）

### (1) サブアカウント一覧を取得

```bash
PARENT_BILLING="012345-6789AB-CDEF01"   # ← 実際の親アカウントIDに変更

gcloud billing accounts list \
  --filter="masterBillingAccount=billingAccounts/${PARENT_BILLING}" \
  --format="table(name, displayName, open, masterBillingAccount)"
```

### (2) サブアカウントにリンクされたプロジェクト一覧を取得

```bash
SUB_BILLING="XXXXXXXX-YYYYYYYY-ZZZZZZZ"   # ← 実際のサブアカウントID

gcloud alpha billing accounts projects list \
  --billing-account="${SUB_BILLING}" \
  --format="table(projectId, billingEnabled, name)"
```

______________________________________________________________________

## 4. Billing API 一括取得スクリプト（`billing_links.sh`）

全サブアカウント・全プロジェクトの情報をCSVに出力する簡易確認用スクリプト。
`billing_enabled` と `sub_account_open` を含む。

```bash
#!/bin/bash
# Google Cloud Billing Links 簡易取得スクリプト（調査用）

PARENT_BILLING_ACCOUNT="012345-6789AB-CDEF01"   # ← 実際の親アカウントIDに変更
OUTPUT_FILE="billing_links_$(date +%Y%m%d_%H%M).csv"

echo "parent_account_id,sub_account_id,sub_account_name,sub_account_open,project_id,billing_enabled" > "$OUTPUT_FILE"

echo "処理開始: $PARENT_BILLING_ACCOUNT"

gcloud billing accounts list \
  --filter="masterBillingAccount=billingAccounts/${PARENT_BILLING_ACCOUNT}" \
  --format="value(name,displayName,open)" | \
while IFS=$'\t' read -r SUB_ACCOUNT SUB_DISPLAY_NAME SUB_OPEN; do

  SUB_ID=$(echo "$SUB_ACCOUNT" | sed 's|billingAccounts/||')
  echo "  処理中: $SUB_ID ($SUB_DISPLAY_NAME, open=${SUB_OPEN})"

  gcloud alpha billing accounts projects list \
    --billing-account="$SUB_ID" \
    --format="value(projectId,billingEnabled)" 2>/dev/null | \
  while IFS=$'\t' read -r PROJECT_ID BILLING_ENABLED; do
    if [ -n "$PROJECT_ID" ]; then
      echo "\"$PARENT_BILLING_ACCOUNT\",\"$SUB_ID\",\"$SUB_DISPLAY_NAME\",\"$SUB_OPEN\",\"$PROJECT_ID\",\"$BILLING_ENABLED\"" >> "$OUTPUT_FILE"
    fi
  done
done

echo "完了: $OUTPUT_FILE（$(wc -l < "$OUTPUT_FILE") 行）"
```

### 実行方法

```bash
# 実行権限を付与して実行
chmod +x billing_links.sh
./billing_links.sh

# alpha コンポーネントが未インストールの場合
gcloud components install alpha
```

______________________________________________________________________

## 5. Billing Export の手動確認（BQクエリ）

親請求先アカウントで Cloud Billing Export to BigQuery を有効化すると、配下の全サブアカウント・全プロジェクトのコストデータが1つのテーブルに出力される。以下のクエリで内容を確認する。

> テーブル名の `PROJECT_ID`・`DATASET`・`XXXXXX` は実際の値に置き換えること。

### (1) エクスポートが正常に動作しているか確認

```sql
SELECT
  DATE(usage_start_time) AS date,
  COUNT(*)               AS row_count
FROM `PROJECT_ID.DATASET.gcp_billing_export_v1_XXXXXX`
WHERE DATE(usage_start_time) >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
GROUP BY date
ORDER BY date DESC
```

### (2) 前月のプロジェクト別請求金額

```sql
SELECT
  project.id            AS project_id,
  billing_account_id    AS sub_account_id,
  ROUND(SUM(cost), 2)   AS prev_month_cost,
  currency
FROM `PROJECT_ID.DATASET.gcp_billing_export_v1_XXXXXX`
WHERE
  invoice.month = FORMAT_DATE('%Y%m', DATE_SUB(CURRENT_DATE(), INTERVAL 1 MONTH))
GROUP BY project_id, sub_account_id, currency
ORDER BY prev_month_cost DESC
```

### (3) 累積課金実績のあるプロジェクト一覧（ever_billed = TRUE の候補）

```sql
SELECT
  project.id          AS project_id,
  ROUND(SUM(cost), 2) AS total_cost
FROM `PROJECT_ID.DATASET.gcp_billing_export_v1_XXXXXX`
GROUP BY project_id
HAVING SUM(cost) > 0
ORDER BY total_cost DESC
```

> `billing_project_links` に存在するがこのクエリに現れないプロジェクトが `ever_billed = FALSE` の対象。
