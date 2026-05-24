# MERGE SQL プロトタイプ

要件定義の「カラム別挙動表」「ステータス遷移ルール」「処理フロー」を BigQuery MERGE 文として
書き起こしたプロトタイプ。実装着手前の仕様検証を目的とする。

______________________________________________________________________

## 前提：@batch_run_at パラメータ

すべての SQL ステートメントで `@batch_run_at`（TIMESTAMP 型）を統一使用する。

```python
# batch/main.py（抜粋）
from datetime import datetime, timezone

batch_run_at = datetime.now(timezone.utc)

job_config = bigquery.QueryJobConfig(
    query_parameters=[
        bigquery.ScalarQueryParameter("batch_run_at", "TIMESTAMP", batch_run_at),
    ]
)
```

`CURRENT_TIMESTAMP()` を SQL 内で直接使うと Step 4 と Step 5 でタイムスタンプがずれ、
Step 5 の `last_fetched_at < @batch_run_at` 判定が壊れる。必ず Python 側で固定すること。

______________________________________________________________________

## Step 1: billing_newly_started リセット

```sql
-- 前日以前の実行で TRUE になったレコードのみリセット（同日再実行はスキップ）
UPDATE `{project}.{dataset}.billing_project_links`
SET    billing_newly_started = FALSE
WHERE  billing_newly_started = TRUE
  AND  DATE(last_fetched_at, 'Asia/Tokyo') < CURRENT_DATE('Asia/Tokyo')
```

**判定ロジック**

| 状況 | last_fetched_at の値 | 条件成立 | 結果 |
|---|---|---|---|
| 新しい日の初回実行 | 昨日の日時 | TRUE | リセット ✓ |
| 同日の2回目実行 | 今日の日時（前回バッチの Step 4 で更新済み） | FALSE | スキップ ✓ |

______________________________________________________________________

## Step 4 + Step 5: MERGE + UNLINKED 後処理（同一トランザクション）

Step 4 が成功して Step 5 が失敗すると、`last_fetched_at` は更新済みだが
`UNLINKED` が設定されない不整合状態になる。`BEGIN TRANSACTION / COMMIT` で
2つのステートメントをアトミックに実行する。

```sql
BEGIN TRANSACTION;

-- ===== Step 4: MERGE =====
MERGE `{project}.{dataset}.billing_project_links` AS T
USING `{project}.{dataset}._tmp_billing_links`    AS S
ON    T.parent_account_id = S.parent_account_id
  AND T.sub_account_id    = S.sub_account_id
  AND T.project_id        = S.project_id

-- 再リンク（UNLINKED → 復活）
-- ※ 必ずこの句を先に書く。後続の無条件 WHEN MATCHED より先に評価される
WHEN MATCHED AND T.status = 'UNLINKED' THEN UPDATE SET
  billing_enabled  = S.billing_enabled,
  sub_account_open = S.sub_account_open,
  sub_account_name = S.sub_account_name,
  status           = CASE
                       WHEN S.billing_enabled  = FALSE THEN 'BILLING_DISABLED'
                       WHEN S.sub_account_open = FALSE THEN 'SUB_CLOSED'
                       ELSE 'ACTIVE'
                     END,
  unlinked_at      = NULL,
  relinked_at      = @batch_run_at,
  link_count       = T.link_count + 1,
  last_fetched_at  = @batch_run_at,
  updated_at       = @batch_run_at

-- 既存レコードの通常更新（UNLINKED 以外）
WHEN MATCHED THEN UPDATE SET
  billing_enabled  = S.billing_enabled,
  sub_account_open = S.sub_account_open,
  sub_account_name = S.sub_account_name,
  status           = CASE
                       WHEN S.billing_enabled  = FALSE THEN 'BILLING_DISABLED'
                       WHEN S.sub_account_open = FALSE THEN 'SUB_CLOSED'
                       ELSE 'ACTIVE'
                     END,
  last_fetched_at  = @batch_run_at,
  updated_at       = CASE
                       -- IS DISTINCT FROM で NULL を含む比較を安全に行う
                       WHEN T.billing_enabled  IS DISTINCT FROM S.billing_enabled
                         OR T.sub_account_open IS DISTINCT FROM S.sub_account_open
                         OR T.sub_account_name IS DISTINCT FROM S.sub_account_name
                       THEN @batch_run_at
                       ELSE T.updated_at
                     END

-- 新規プロジェクト
WHEN NOT MATCHED THEN INSERT (
  parent_account_id, sub_account_id, sub_account_name, project_id,
  billing_enabled,   sub_account_open, status,
  linked_at,         unlinked_at,      relinked_at,     link_count,
  last_fetched_at,   updated_at,
  prev_month_cost,   cost_currency,
  ever_billed,       first_billed_month,                billing_newly_started
) VALUES (
  S.parent_account_id, S.sub_account_id, S.sub_account_name, S.project_id,
  S.billing_enabled,   S.sub_account_open,
  CASE
    WHEN S.billing_enabled  = FALSE THEN 'BILLING_DISABLED'
    WHEN S.sub_account_open = FALSE THEN 'SUB_CLOSED'
    ELSE 'ACTIVE'
  END,
  @batch_run_at, NULL, NULL, 1,
  @batch_run_at, @batch_run_at,
  NULL, NULL,
  FALSE, NULL, FALSE
);

-- ===== Step 5: UNLINKED 後処理 UPDATE =====
-- ACTIVE / BILLING_DISABLED / SUB_CLOSED のうち、今回 API に出現しなかったものを UNLINKED に
UPDATE `{project}.{dataset}.billing_project_links`
SET
  status      = 'UNLINKED',
  unlinked_at = @batch_run_at,
  updated_at  = @batch_run_at
WHERE
  status         != 'UNLINKED'
  AND last_fetched_at < @batch_run_at;

COMMIT TRANSACTION;
```

______________________________________________________________________

## Step 7: ever_billed / first_billed_month / billing_newly_started 更新

```sql
MERGE `{project}.{dataset}.billing_project_links` AS T
USING (
  SELECT
    project.id          AS project_id,
    billing_account_id  AS sub_account_id,
    MIN(invoice.month)  AS first_billed_month
  FROM `{project}.{dataset}.{billing_export_table}`
  GROUP BY project.id, billing_account_id
  HAVING SUM(cost) > 0
) AS S
ON  T.project_id     = S.project_id
AND T.sub_account_id = S.sub_account_id

-- FALSE → TRUE の遷移（課金開始検知）
WHEN MATCHED AND T.ever_billed = FALSE THEN UPDATE SET
  ever_billed           = TRUE,
  first_billed_month    = S.first_billed_month,
  -- UNLINKED には billing_newly_started = TRUE をセットしない（論点④）
  billing_newly_started = CASE WHEN T.status != 'UNLINKED' THEN TRUE ELSE FALSE END,
  updated_at            = @batch_run_at

-- 既に TRUE だが、より古い月が判明した場合（Billing Export の遅延反映、論点⑤）
WHEN MATCHED AND T.ever_billed = TRUE
              AND S.first_billed_month < T.first_billed_month THEN UPDATE SET
  first_billed_month = S.first_billed_month,
  updated_at         = @batch_run_at

-- WHEN NOT MATCHED は意図的に省略
-- （Billing Export にあるが billing_project_links にないプロジェクトは管理対象外）
```

______________________________________________________________________

## 発見した仕様課題（requirements.md への反映が必要）

| # | 問題 | 影響 | 対処 |
|---|---|---|---|
| P-1 | `CURRENT_TIMESTAMP()` を各ステートメントで個別に呼ぶと Step 4/5 間でタイムスタンプがずれる | Step 5 の UNLINKED 誤判定 | Python 側で `batch_run_at` を固定し `@batch_run_at` パラメータとして渡す |
| P-2 | Step 4 と Step 5 が別トランザクションだと、Step 4 成功・Step 5 失敗で不整合状態になる | last_fetched_at 更新済み・UNLINKED 未設定の中間状態が残る | `BEGIN TRANSACTION / COMMIT` で Step 4+5 を1トランザクションにまとめる |
| P-3 | `T.sub_account_name != S.sub_account_name` は NULL を含む比較で変化を検知できない | updated_at が更新されず変化を見落とす | `IS DISTINCT FROM` を使用（MERGE SQL 上で対処済み） |
| P-4 | 複数 `WHEN MATCHED` 句の評価順序が要件ドキュメントに明記されていない | 順序誤りで再リンク検知が無視される | `UNLINKED 判定を先、無条件 WHEN MATCHED を後` と明記 |
