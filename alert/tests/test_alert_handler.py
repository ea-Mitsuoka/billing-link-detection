"""alert_handler の主要パス検証: クエリ置換 / Slack 整形 / トランケート / no-result / エラー伝播。"""
from unittest.mock import MagicMock, patch

import pytest


def _mk_request(query: str, channel: str, message: str):
    """functions_framework HTTP リクエストの最小モック。"""
    req = MagicMock()
    req.get_json.return_value = {"query": query, "channel": channel, "message": message}
    return req


def _mk_slack_ok():
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"ok": True}
    return resp


def _mk_row(d: dict):
    """bigquery.Row 風: dict(row) で元 dict が得られるオブジェクト。"""
    row = MagicMock()
    row.__iter__ = lambda self: iter(d.items())
    row.keys = lambda: d.keys()
    return d  # dict そのものは dict(d) でコピーされ alert_main 内の処理と整合する


def test_query_substitutes_project_and_dataset(alert_module):
    """payload.query 内の {project}/{dataset} が env から置換される。"""
    captured = {}

    def fake_query(q, **kwargs):
        captured["sql"] = q
        result = MagicMock()
        result.result.return_value = iter([])
        return result

    with patch.object(alert_module.bigquery, "Client") as bq_cls:
        bq_cls.return_value.query.side_effect = fake_query
        req = _mk_request(
            "SELECT * FROM `{project}.{dataset}.t`", "#ch", "msg"
        )
        body, status = alert_module.alert_handler(req)

    assert status == 200
    assert body == "no results"
    assert "test-project.billing_data_test.t" in captured["sql"]


def test_query_substitutes_billing_export_vars(alert_module):
    """{billing_export_project}/{billing_export_dataset}/{billing_export_table} が env から置換される。"""
    captured = {}

    def fake_query(q, **kwargs):
        captured["sql"] = q
        result = MagicMock()
        result.result.return_value = iter([])
        return result

    with patch.object(alert_module.bigquery, "Client") as bq_cls:
        bq_cls.return_value.query.side_effect = fake_query
        req = _mk_request(
            "FROM `{billing_export_project}.{billing_export_dataset}.{billing_export_table}`",
            "#ch", "msg",
        )
        alert_module.alert_handler(req)

    assert "test-export-project.billing_data.gcp_billing_export_v1_TEST" in captured["sql"]


def test_no_results_returns_200_without_posting(alert_module):
    """ヒット 0 件のときは Slack に投稿しない。"""
    with patch.object(alert_module.bigquery, "Client") as bq_cls, \
         patch.object(alert_module.requests, "post") as post:
        bq_cls.return_value.query.return_value.result.return_value = iter([])
        req = _mk_request("SELECT 1", "#ch", "msg")
        body, status = alert_module.alert_handler(req)

    assert status == 200
    post.assert_not_called()


def test_results_posted_to_slack_with_formatted_text(alert_module):
    rows = [{"project_id": "p1", "cost": 100}, {"project_id": "p2", "cost": 200}]

    with patch.object(alert_module.bigquery, "Client") as bq_cls, \
         patch.object(alert_module.requests, "post", return_value=_mk_slack_ok()) as post:
        bq_cls.return_value.query.return_value.result.return_value = iter(rows)
        req = _mk_request("SELECT 1", "#alerts", "課金開始")
        body, status = alert_module.alert_handler(req)

    assert status == 200
    payload = post.call_args.kwargs["json"]
    assert payload["channel"] == "#alerts"
    assert "課金開始" in payload["text"]
    assert "project_id: p1" in payload["text"]
    assert "cost: 100" in payload["text"]
    # 件数が MAX_ROWS 以下なので "他 ... 件" は出ない
    assert "他" not in payload["text"]


def test_truncates_when_exceeds_max_rows(alert_module):
    rows = [{"project_id": f"p{i}", "cost": i} for i in range(alert_module.MAX_ROWS + 5)]

    with patch.object(alert_module.bigquery, "Client") as bq_cls, \
         patch.object(alert_module.requests, "post", return_value=_mk_slack_ok()) as post:
        bq_cls.return_value.query.return_value.result.return_value = iter(rows)
        req = _mk_request("SELECT 1", "#ch", "m")
        alert_module.alert_handler(req)

    text = post.call_args.kwargs["json"]["text"]
    # 最大表示行数の最後の行は含まれる
    assert f"p{alert_module.MAX_ROWS - 1}" in text
    # 超過行は含まれない
    assert f"p{alert_module.MAX_ROWS + 4}" not in text
    # 「他 N 件」サフィックスが付く
    assert "他 5 件" in text


def test_slack_api_error_raises(alert_module):
    """Slack が ok=false を返したら例外を上げる（Cloud Functions が ERROR ログ出力）。"""
    bad = MagicMock()
    bad.raise_for_status.return_value = None
    bad.json.return_value = {"ok": False, "error": "invalid_auth"}

    with patch.object(alert_module.bigquery, "Client") as bq_cls, \
         patch.object(alert_module.requests, "post", return_value=bad):
        bq_cls.return_value.query.return_value.result.return_value = iter(
            [{"project_id": "p1"}]
        )
        req = _mk_request("SELECT 1", "#ch", "m")
        with pytest.raises(RuntimeError, match="invalid_auth"):
            alert_module.alert_handler(req)


def test_slack_5xx_propagates_http_error(alert_module):
    """Slack が 5xx を返したら HTTPError が伝播する（raise_for_status 経由）。"""
    import requests as req_module

    bad = MagicMock()
    bad.raise_for_status.side_effect = req_module.HTTPError("503 Service Unavailable")

    with patch.object(alert_module.bigquery, "Client") as bq_cls, \
         patch.object(alert_module.requests, "post", return_value=bad):
        bq_cls.return_value.query.return_value.result.return_value = iter(
            [{"project_id": "p1"}]
        )
        req = _mk_request("SELECT 1", "#ch", "m")
        with pytest.raises(req_module.HTTPError, match="503"):
            alert_module.alert_handler(req)


def test_slack_non_json_body_raises_runtime_error(alert_module):
    """Slack が 200 OK で非 JSON ボディを返した場合は RuntimeError に変換される。"""
    bad = MagicMock()
    bad.raise_for_status.return_value = None
    bad.json.side_effect = ValueError("Expecting value: line 1 column 1 (char 0)")
    bad.status_code = 200

    with patch.object(alert_module.bigquery, "Client") as bq_cls, \
         patch.object(alert_module.requests, "post", return_value=bad):
        bq_cls.return_value.query.return_value.result.return_value = iter(
            [{"project_id": "p1"}]
        )
        req = _mk_request("SELECT 1", "#ch", "m")
        with pytest.raises(RuntimeError, match="non-JSON response"):
            alert_module.alert_handler(req)


def test_max_bytes_billed_is_set(alert_module):
    """課金事故防止のため maximum_bytes_billed が QueryJobConfig に設定される。"""
    with patch.object(alert_module.bigquery, "Client") as bq_cls:
        bq_cls.return_value.query.return_value.result.return_value = iter([])
        req = _mk_request("SELECT 1", "#ch", "m")
        alert_module.alert_handler(req)

    job_config = bq_cls.return_value.query.call_args.kwargs["job_config"]
    assert job_config.maximum_bytes_billed == alert_module.MAX_BYTES_BILLED
