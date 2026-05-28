"""Cloud Functions Gen2: generic alert handler for billing-link-detection."""
import logging
import os
import uuid

import functions_framework
import google.cloud.logging
import requests
from google.cloud import bigquery

google.cloud.logging.Client().setup_logging()
logger = logging.getLogger(__name__)

MAX_BYTES_BILLED = 10 * 1024 * 1024 * 1024  # 10 GB
MAX_ROWS = 50


@functions_framework.http
def alert_handler(request):
    run_id  = str(uuid.uuid4())
    payload = request.get_json(force=True)
    query   = payload["query"].format(
        project=os.environ["GCP_PROJECT_ID"],
        dataset=os.environ["BQ_DATASET"],
        billing_export_project=os.environ.get("BILLING_EXPORT_PROJECT_ID") or os.environ["GCP_PROJECT_ID"],
        billing_export_dataset=os.environ.get("BILLING_EXPORT_DATASET", "billing_data"),
        billing_export_table=os.environ.get("BILLING_EXPORT_TABLE", ""),
    )
    channel = payload["channel"]
    message = payload["message"]

    logger.info(
        "alert_handler start",
        extra={"json_fields": {"run_id": run_id, "channel": channel, "batch_name": "alert-handler"}},
    )

    client     = bigquery.Client()
    job_config = bigquery.QueryJobConfig(maximum_bytes_billed=MAX_BYTES_BILLED)
    results    = list(client.query(query, job_config=job_config).result())

    if not results:
        logger.info(
            "no results, skipping notification",
            extra={"json_fields": {"run_id": run_id, "channel": channel, "result_count": 0}},
        )
        return "no results", 200

    rows_to_show = results[:MAX_ROWS]
    rows_text = "\n".join(
        " | ".join(f"{k}: {v}" for k, v in dict(row).items())
        for row in rows_to_show
    )
    suffix = (
        f"\n_...他 {len(results) - MAX_ROWS} 件。全件は BigQuery で確認してください。_"
        if len(results) > MAX_ROWS else ""
    )
    text = f"*{message}*\n```{rows_text}```{suffix}"

    # HTTP 4xx/5xx → raise_for_status() で例外化
    # HTTP 200 でも body["ok"] が false の場合は Slack 側エラー（無効トークン等）
    # 200 OK だが非 JSON ボディ（Slack 障害時の HTML 等）は JSONDecodeError を RuntimeError に変換
    response = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}"},
        json={"channel": channel, "text": text},
        timeout=10,
    )
    response.raise_for_status()
    try:
        body = response.json()
    except ValueError as e:
        raise RuntimeError(
            f"Slack returned non-JSON response (status={response.status_code}, channel={channel}, run_id={run_id}): {e}"
        )
    if not body.get("ok"):
        raise RuntimeError(
            f"Slack API error: {body.get('error')} (channel={channel}, run_id={run_id})"
        )

    logger.info(
        "notification sent",
        extra={"json_fields": {
            "run_id": run_id,
            "channel": channel,
            "result_count": len(results),
            "truncated": len(results) > MAX_ROWS,
        }},
    )
    return "ok", 200
