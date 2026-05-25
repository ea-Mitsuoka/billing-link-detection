"""Cloud Run Jobs batch: GCP billing link detection."""
import logging
import os
import sys
import uuid
from datetime import datetime, timezone

import google.cloud.logging
from google.cloud import bigquery, billing_v1

google.cloud.logging.Client().setup_logging()
logger = logging.getLogger(__name__)

PROJECT_ID             = os.environ["GCP_PROJECT_ID"]
BQ_DATASET             = os.environ["BQ_DATASET"]
PARENT_BILLING_ACCOUNT = os.environ["PARENT_BILLING_ACCOUNT"]
BILLING_EXPORT_PROJECT = os.environ.get("BILLING_EXPORT_PROJECT_ID", PROJECT_ID)
BILLING_EXPORT_DATASET = os.environ.get("BILLING_EXPORT_DATASET", BQ_DATASET)
BILLING_EXPORT_TABLE   = os.environ.get("BILLING_EXPORT_TABLE", "")
BATCH_TYPE             = os.environ.get("BATCH_TYPE", "daily")


def main() -> None:
    run_id       = str(uuid.uuid4())
    batch_run_at = datetime.now(timezone.utc)
    batch_name   = f"billing-{BATCH_TYPE}"

    logger.info(
        "batch start",
        extra={"json_fields": {
            "run_id": run_id, "batch_name": batch_name,
            "batch_type": BATCH_TYPE, "batch_run_at": batch_run_at.isoformat(),
        }},
    )
    try:
        if BATCH_TYPE == "daily":
            _run_daily(run_id, batch_run_at)
        elif BATCH_TYPE == "monthly":
            _run_monthly(run_id, batch_run_at)
        else:
            raise ValueError(f"Unknown BATCH_TYPE: {BATCH_TYPE!r}")
    except Exception as exc:
        logger.error(
            "batch failed",
            extra={"json_fields": {
                "run_id": run_id, "batch_name": batch_name,
                "error_type": type(exc).__name__, "error_detail": str(exc),
            }},
            exc_info=True,
        )
        sys.exit(1)

    logger.info(
        "batch complete",
        extra={"json_fields": {"run_id": run_id, "batch_name": batch_name}},
    )


# ── daily ─────────────────────────────────────────────────────────────────────

def _run_daily(run_id: str, batch_run_at: datetime) -> None:
    bq = bigquery.Client(project=PROJECT_ID)
    _step1_reset(bq, run_id)
    records = _step2_fetch_api(run_id)
    _step3_write_tmp(bq, records, run_id)
    _step4_5_merge_unlinked(bq, batch_run_at, run_id)
    _step6_7_update_ever_billed(bq, batch_run_at, run_id)


def _step1_reset(bq: bigquery.Client, run_id: str) -> None:
    sql = f"""
        UPDATE `{PROJECT_ID}.{BQ_DATASET}.billing_project_links`
        SET    billing_newly_started = FALSE
        WHERE  billing_newly_started = TRUE
          AND  DATE(last_fetched_at, 'Asia/Tokyo') < CURRENT_DATE('Asia/Tokyo')
    """
    bq.query(sql).result()
    logger.info(
        "step1 done",
        extra={"json_fields": {"run_id": run_id, "operation": "reset_billing_newly_started"}},
    )


def _step2_fetch_api(run_id: str) -> list[dict]:
    client = billing_v1.CloudBillingClient()
    sub_accounts = list(client.list_billing_accounts(
        request=billing_v1.ListBillingAccountsRequest(
            filter=f"master_billing_account=billingAccounts/{PARENT_BILLING_ACCOUNT}"
        )
    ))
    logger.info(
        "step2 sub-accounts fetched",
        extra={"json_fields": {
            "run_id": run_id, "operation": "billing_api_fetch",
            "sub_account_count": len(sub_accounts),
        }},
    )

    records: list[dict] = []
    for sa in sub_accounts:
        sub_account_id = sa.name.split("/")[-1]
        projects = list(client.list_project_billing_info(
            request=billing_v1.ListProjectBillingInfoRequest(name=sa.name)
        ))
        for proj in projects:
            records.append({
                "parent_account_id": PARENT_BILLING_ACCOUNT,
                "sub_account_id":    sub_account_id,
                "sub_account_name":  sa.display_name or None,
                "project_id":        proj.project_id,
                "billing_enabled":   proj.billing_enabled,
                "sub_account_open":  sa.open,
            })

    logger.info(
        "step2 done",
        extra={"json_fields": {
            "run_id": run_id, "operation": "billing_api_fetch",
            "sub_account_count": len(sub_accounts), "project_count": len(records),
        }},
    )
    return records


def _step3_write_tmp(bq: bigquery.Client, records: list[dict], run_id: str) -> None:
    schema = [
        bigquery.SchemaField("parent_account_id", "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("sub_account_id",    "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("sub_account_name",  "STRING",  mode="NULLABLE"),
        bigquery.SchemaField("project_id",        "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("billing_enabled",   "BOOLEAN", mode="REQUIRED"),
        bigquery.SchemaField("sub_account_open",  "BOOLEAN", mode="REQUIRED"),
    ]
    bq.load_table_from_json(
        records,
        f"{PROJECT_ID}.{BQ_DATASET}._tmp_billing_links",
        job_config=bigquery.LoadJobConfig(
            schema=schema, write_disposition="WRITE_TRUNCATE"
        ),
    ).result()
    logger.info(
        "step3 done",
        extra={"json_fields": {
            "run_id": run_id, "operation": "write_temp_table", "row_count": len(records),
        }},
    )


def _step4_5_merge_unlinked(bq: bigquery.Client, batch_run_at: datetime, run_id: str) -> None:
    sql = f"""
BEGIN TRANSACTION;

MERGE `{PROJECT_ID}.{BQ_DATASET}.billing_project_links` AS T
USING `{PROJECT_ID}.{BQ_DATASET}._tmp_billing_links`    AS S
ON    T.parent_account_id = S.parent_account_id
  AND T.sub_account_id    = S.sub_account_id
  AND T.project_id        = S.project_id

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
                       WHEN T.billing_enabled  IS DISTINCT FROM S.billing_enabled
                         OR T.sub_account_open IS DISTINCT FROM S.sub_account_open
                         OR T.sub_account_name IS DISTINCT FROM S.sub_account_name
                       THEN @batch_run_at
                       ELSE T.updated_at
                     END

WHEN NOT MATCHED THEN INSERT (
  parent_account_id, sub_account_id, sub_account_name, project_id,
  billing_enabled,   sub_account_open, status,
  linked_at,         unlinked_at,      relinked_at,     link_count,
  last_fetched_at,   updated_at,
  prev_month_cost,   cost_currency,
  ever_billed,       first_billed_month, billing_newly_started
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

UPDATE `{PROJECT_ID}.{BQ_DATASET}.billing_project_links`
SET
  status      = 'UNLINKED',
  unlinked_at = @batch_run_at,
  updated_at  = @batch_run_at
WHERE status         != 'UNLINKED'
  AND last_fetched_at < @batch_run_at;

COMMIT TRANSACTION;
    """
    bq.query(
        sql,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("batch_run_at", "TIMESTAMP", batch_run_at)
            ]
        ),
    ).result()
    logger.info(
        "step4+5 done",
        extra={"json_fields": {"run_id": run_id, "operation": "merge"}},
    )


def _step6_7_update_ever_billed(bq: bigquery.Client, batch_run_at: datetime, run_id: str) -> None:
    if not BILLING_EXPORT_TABLE:
        logger.warning(
            "BILLING_EXPORT_TABLE not set, skipping step6+7",
            extra={"json_fields": {"run_id": run_id, "operation": "billing_export_scan"}},
        )
        return

    sql = f"""
        MERGE `{PROJECT_ID}.{BQ_DATASET}.billing_project_links` AS T
        USING (
          SELECT
            project.id         AS project_id,
            billing_account_id AS sub_account_id,
            MIN(invoice.month) AS first_billed_month
          FROM `{BILLING_EXPORT_PROJECT}.{BILLING_EXPORT_DATASET}.{BILLING_EXPORT_TABLE}`
          GROUP BY project.id, billing_account_id
          HAVING SUM(cost) > 0
        ) AS S
        ON  T.project_id     = S.project_id
        AND T.sub_account_id = S.sub_account_id

        WHEN MATCHED AND T.ever_billed = FALSE THEN UPDATE SET
          ever_billed           = TRUE,
          first_billed_month    = S.first_billed_month,
          billing_newly_started = CASE WHEN T.status != 'UNLINKED' THEN TRUE ELSE FALSE END,
          updated_at            = @batch_run_at

        WHEN MATCHED AND T.ever_billed = TRUE
                      AND S.first_billed_month < T.first_billed_month THEN UPDATE SET
          first_billed_month = S.first_billed_month,
          updated_at         = @batch_run_at
    """
    job = bq.query(
        sql,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("batch_run_at", "TIMESTAMP", batch_run_at)
            ]
        ),
    )
    job.result()
    logger.info(
        "step6+7 done",
        extra={"json_fields": {
            "run_id": run_id, "operation": "billing_export_scan",
            "scanned_bytes": job.total_bytes_processed,
        }},
    )


# ── monthly ───────────────────────────────────────────────────────────────────

def _run_monthly(run_id: str, batch_run_at: datetime) -> None:
    if not BILLING_EXPORT_TABLE:
        raise ValueError("BILLING_EXPORT_TABLE must be set for monthly batch")
    bq = bigquery.Client(project=PROJECT_ID)
    _step_monthly_cost(bq, batch_run_at, run_id)


def _step_monthly_cost(bq: bigquery.Client, batch_run_at: datetime, run_id: str) -> None:
    prev_month = _prev_month_yyyymm(batch_run_at)

    # Step 1: aggregate prev month cost from Billing Export
    agg_sql = f"""
        SELECT
          project.id              AS project_id,
          billing_account_id      AS sub_account_id,
          SUM(cost)               AS prev_month_cost,
          ANY_VALUE(currency)     AS cost_currency,
          COUNT(DISTINCT currency) AS currency_count
        FROM `{BILLING_EXPORT_PROJECT}.{BILLING_EXPORT_DATASET}.{BILLING_EXPORT_TABLE}`
        WHERE invoice.month = '{prev_month}'
        GROUP BY project.id, billing_account_id
    """
    rows = list(bq.query(agg_sql).result())

    multi_currency = [r for r in rows if r["currency_count"] > 1]
    if multi_currency:
        logger.warning(
            "multiple currencies detected in billing export",
            extra={"json_fields": {
                "run_id": run_id, "operation": "billing_export_cost_agg",
                "affected_count": len(multi_currency),
            }},
        )

    logger.info(
        "step1(monthly) aggregation done",
        extra={"json_fields": {
            "run_id": run_id, "operation": "billing_export_cost_agg",
            "prev_month": prev_month, "project_count": len(rows),
        }},
    )

    agg_records = [
        {
            "project_id":      r["project_id"],
            "sub_account_id":  r["sub_account_id"],
            "prev_month_cost": float(r["prev_month_cost"]),
            "cost_currency":   r["cost_currency"],
        }
        for r in rows
    ]

    schema = [
        bigquery.SchemaField("project_id",      "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("sub_account_id",  "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("prev_month_cost", "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("cost_currency",   "STRING",  mode="NULLABLE"),
    ]
    bq.load_table_from_json(
        agg_records,
        f"{PROJECT_ID}.{BQ_DATASET}._tmp_monthly_cost",
        job_config=bigquery.LoadJobConfig(
            schema=schema, write_disposition="WRITE_TRUNCATE"
        ),
    ).result()

    # Step 2: MERGE — LEFT JOIN ensures non-appearing records get prev_month_cost = 0
    merge_sql = f"""
        MERGE `{PROJECT_ID}.{BQ_DATASET}.billing_project_links` AS T
        USING (
          SELECT
            L.parent_account_id,
            L.project_id,
            L.sub_account_id,
            COALESCE(C.prev_month_cost, 0.0) AS prev_month_cost,
            C.cost_currency
          FROM `{PROJECT_ID}.{BQ_DATASET}.billing_project_links` AS L
          LEFT JOIN `{PROJECT_ID}.{BQ_DATASET}._tmp_monthly_cost` AS C
            ON  L.project_id     = C.project_id
            AND L.sub_account_id = C.sub_account_id
        ) AS S
        ON  T.parent_account_id = S.parent_account_id
        AND T.project_id        = S.project_id
        AND T.sub_account_id    = S.sub_account_id
        WHEN MATCHED THEN UPDATE SET
          prev_month_cost = S.prev_month_cost,
          cost_currency   = S.cost_currency,
          updated_at      = CASE
            WHEN T.prev_month_cost IS DISTINCT FROM S.prev_month_cost
              OR T.cost_currency   IS DISTINCT FROM S.cost_currency
            THEN @batch_run_at
            ELSE T.updated_at
          END
    """
    bq.query(
        merge_sql,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("batch_run_at", "TIMESTAMP", batch_run_at)
            ]
        ),
    ).result()
    logger.info(
        "step2(monthly) merge done",
        extra={"json_fields": {
            "run_id": run_id, "operation": "merge", "prev_month": prev_month,
        }},
    )


def _prev_month_yyyymm(dt: datetime) -> str:
    if dt.month == 1:
        return f"{dt.year - 1}12"
    return f"{dt.year}{dt.month - 1:02d}"


if __name__ == "__main__":
    main()
