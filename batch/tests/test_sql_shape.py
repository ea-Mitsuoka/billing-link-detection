"""SQL 文字列の構造的妥当性を検証する（リファクタによる事故防止）。

意図: 実 BigQuery を叩かずに、生成 SQL が以下のキー要素を含むことを保証する。
- daily MERGE: 3 種類の WHEN 分岐 / UNLINKED 検出 UPDATE / BEGIN/COMMIT TRANSACTION
- monthly MERGE: LEFT JOIN による全件 COALESCE(0)
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock


def _capture_query_sql(bq_mock: MagicMock) -> list[str]:
    """bq.query の呼び出し履歴から SQL 文字列を取り出す。"""
    return [call.args[0] for call in bq_mock.query.call_args_list]


def test_step1_reset_resets_only_yesterday(batch_module):
    bq = MagicMock()
    batch_module._step1_reset(bq, run_id="x")
    sql = _capture_query_sql(bq)[0]
    assert "UPDATE" in sql
    assert "billing_newly_started = FALSE" in sql
    assert "billing_newly_started = TRUE" in sql
    assert "Asia/Tokyo" in sql  # JST 基準のリセット


def test_step4_5_merge_contains_all_branches(batch_module):
    bq = MagicMock()
    batch_module._step4_5_merge_unlinked(
        bq, batch_run_at=datetime(2026, 5, 25, tzinfo=timezone.utc), run_id="x"
    )
    sql = _capture_query_sql(bq)[0]

    # トランザクション内であること
    assert "BEGIN TRANSACTION" in sql
    assert "COMMIT TRANSACTION" in sql

    # MERGE の 3 分岐すべてが存在
    assert "WHEN MATCHED AND T.status = 'UNLINKED' THEN UPDATE" in sql
    assert "WHEN MATCHED THEN UPDATE" in sql
    assert "WHEN NOT MATCHED THEN INSERT" in sql

    # UNLINKED 検出
    assert "status         = 'UNLINKED'" in sql or "status = 'UNLINKED'" in sql.replace("  ", " ")
    assert "last_fetched_at < @batch_run_at" in sql

    # 再リンク時の link_count インクリメント
    assert "link_count       = T.link_count + 1" in sql or "T.link_count + 1" in sql

    # BILLING_DISABLED / SUB_CLOSED 判定
    assert "BILLING_DISABLED" in sql
    assert "SUB_CLOSED" in sql


def test_step4_5_passes_batch_run_at_param(batch_module):
    bq = MagicMock()
    run_at = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
    batch_module._step4_5_merge_unlinked(bq, batch_run_at=run_at, run_id="x")

    job_config = bq.query.call_args.kwargs["job_config"]
    params = job_config.query_parameters
    assert len(params) == 1
    assert params[0].name == "batch_run_at"
    assert params[0].value == run_at


def test_monthly_merge_uses_left_join_for_zero_cost(batch_module, monkeypatch):
    """非出現プロジェクトにも prev_month_cost=0 を書くため LEFT JOIN が必要。"""
    bq = MagicMock()
    # SELECT 集計の結果は空でも MERGE は実行される
    bq.query.return_value.result.return_value = iter([])

    batch_module._step_monthly_cost(
        bq, batch_run_at=datetime(2026, 5, 1, tzinfo=timezone.utc), run_id="x"
    )

    # 2回 query が呼ばれる: 集計 SELECT と MERGE
    all_sql = _capture_query_sql(bq)
    assert len(all_sql) == 2
    merge_sql = all_sql[1]

    assert "MERGE" in merge_sql
    assert "LEFT JOIN" in merge_sql
    assert "COALESCE(C.prev_month_cost, 0.0)" in merge_sql


def test_monthly_aggregation_filters_by_prev_month_yyyymm(batch_module):
    bq = MagicMock()
    bq.query.return_value.result.return_value = iter([])

    # 5月実行 → invoice.month = '202604' でフィルタ
    batch_module._step_monthly_cost(
        bq, batch_run_at=datetime(2026, 5, 1, tzinfo=timezone.utc), run_id="x"
    )
    agg_sql = _capture_query_sql(bq)[0]
    assert "invoice.month = '202604'" in agg_sql


def test_step6_7_skipped_when_export_table_missing(batch_module, monkeypatch):
    """BILLING_EXPORT_TABLE 未設定時は ever_billed 更新をスキップする（早期警告）。"""
    monkeypatch.setattr(batch_module, "BILLING_EXPORT_TABLE", "")
    bq = MagicMock()
    batch_module._step6_7_update_ever_billed(
        bq, batch_run_at=datetime(2026, 5, 25, tzinfo=timezone.utc), run_id="x"
    )
    bq.query.assert_not_called()
