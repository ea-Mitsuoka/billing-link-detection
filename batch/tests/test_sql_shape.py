"""SQL 文字列の構造的妥当性を検証する（リファクタによる事故防止）。

意図: 実 BigQuery を叩かずに、生成 SQL が以下のキー要素を含むことを保証する。
- daily MERGE: 3 種類の WHEN 分岐 / UNLINKED 検出 UPDATE / BEGIN/COMMIT TRANSACTION
- monthly MERGE: LEFT JOIN による全件 COALESCE(0)
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest


def _capture_query_sql(bq_mock: MagicMock) -> list[str]:
    """bq.query の呼び出し履歴から SQL 文字列を取り出す。"""
    return [call.args[0] for call in bq_mock.query.call_args_list]


def _bq_with_query_results(*per_call_rows: list) -> MagicMock:
    """bq.query を「呼び出し順」に異なる結果へ割り当てた mock を返す。

    per_call_rows[i] は i 番目の bq.query(...).result() が返す行リスト。
    list で保持するため複数回イテレートしても枯渇しない（guard は [0] で参照する）。
    """
    bq = MagicMock()
    jobs = []
    for rows in per_call_rows:
        job = MagicMock()
        job.result.return_value = list(rows)
        jobs.append(job)
    bq.query.side_effect = jobs
    return bq


def _guard_row(link_count: int = 10, cost_count: int = 5, matched_count: int = 5) -> dict:
    """monthly cost guard の集計クエリ結果（1 行）。既定値はガードを通過する。"""
    return {"link_count": link_count, "cost_count": cost_count, "matched_count": matched_count}


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


def test_unlinked_update_uses_strict_less_than(batch_module):
    """初回実行時に全レコードが UNLINKED 化されないことを保証する。

    MERGE 直後、出現したレコードは last_fetched_at = @batch_run_at に更新済み。
    UNLINKED 化条件が `<` であれば初回実行時は誰もヒットしない（全件 INSERT で = になるため）。
    `<=` だと初回でも全件 UNLINKED になるリグレッションを防ぐ。
    """
    bq = MagicMock()
    batch_module._step4_5_merge_unlinked(
        bq, batch_run_at=datetime(2026, 5, 25, tzinfo=timezone.utc), run_id="x"
    )
    sql = _capture_query_sql(bq)[0]
    assert "last_fetched_at < @batch_run_at" in sql
    assert "last_fetched_at <= @batch_run_at" not in sql


def test_step4_5_passes_batch_run_at_param(batch_module):
    bq = MagicMock()
    run_at = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
    batch_module._step4_5_merge_unlinked(bq, batch_run_at=run_at, run_id="x")

    job_config = bq.query.call_args.kwargs["job_config"]
    params = job_config.query_parameters
    assert len(params) == 1
    assert params[0].name == "batch_run_at"
    assert params[0].value == run_at


def test_monthly_merge_uses_left_join_for_zero_cost(batch_module):
    """非出現プロジェクトにも prev_month_cost=0 を書くため LEFT JOIN が必要。"""
    bq = _bq_with_query_results(
        [{"project_id": "p1", "sub_account_id": "s1", "prev_month_cost": 100.0,
          "cost_currency": "USD", "currency_count": 1}],   # agg SELECT
        [_guard_row()],                                     # guard（通過）
        [],                                                 # MERGE
    )
    batch_module._step_monthly_cost(
        bq, batch_run_at=datetime(2026, 5, 1, tzinfo=timezone.utc), run_id="x"
    )

    # query は agg / guard / MERGE の 3 回。MERGE は最後。
    all_sql = _capture_query_sql(bq)
    assert len(all_sql) == 3
    merge_sql = all_sql[-1]

    assert "MERGE" in merge_sql
    assert "LEFT JOIN" in merge_sql
    assert "COALESCE(C.prev_month_cost, 0.0)" in merge_sql


def test_monthly_aggregation_filters_by_prev_month_yyyymm(batch_module):
    bq = _bq_with_query_results(
        [{"project_id": "p1", "sub_account_id": "s1", "prev_month_cost": 1.0,
          "cost_currency": "USD", "currency_count": 1}],
        [_guard_row()],
        [],
    )
    # 5月実行 → invoice.month = '202604' でフィルタ
    batch_module._step_monthly_cost(
        bq, batch_run_at=datetime(2026, 5, 1, tzinfo=timezone.utc), run_id="x"
    )
    agg_sql = _capture_query_sql(bq)[0]
    assert "invoice.month = '202604'" in agg_sql


def test_monthly_aggregation_excludes_null_project(batch_module):
    """project.id が NULL/空 の行を除外する。

    Billing Export には税金・調整・プロジェクト非紐付きサブスク課金など project.id=NULL の行が
    含まれる。除外しないと _tmp_monthly_cost.project_id（REQUIRED）へのロードが失敗し、
    月次バッチが MERGE 到達前にクラッシュして prev_month_cost が更新されなくなる（リグレッション防止）。
    """
    bq = _bq_with_query_results(
        [{"project_id": "p1", "sub_account_id": "s1", "prev_month_cost": 1.0,
          "cost_currency": "USD", "currency_count": 1}],
        [_guard_row()],
        [],
    )
    batch_module._step_monthly_cost(
        bq, batch_run_at=datetime(2026, 5, 1, tzinfo=timezone.utc), run_id="x"
    )
    agg_sql = _capture_query_sql(bq)[0]
    assert "project.id IS NOT NULL" in agg_sql
    assert "project.id != ''" in agg_sql


def test_step6_7_skipped_when_export_table_missing(batch_module, monkeypatch):
    """BILLING_EXPORT_TABLE 未設定時は ever_billed 更新をスキップする（早期警告）。"""
    monkeypatch.setattr(batch_module, "BILLING_EXPORT_TABLE", "")
    bq = MagicMock()
    batch_module._step6_7_update_ever_billed(
        bq, batch_run_at=datetime(2026, 5, 25, tzinfo=timezone.utc), run_id="x"
    )
    bq.query.assert_not_called()


def test_monthly_multi_currency_logs_warning_and_continues(batch_module, caplog):
    """複数通貨を持つプロジェクトが検出されても処理を継続し、warning を残す。"""
    import logging as py_logging

    # 1件は単一通貨、もう1件は複数通貨
    bq = _bq_with_query_results(
        [
            {"project_id": "p1", "sub_account_id": "s1", "prev_month_cost": 100.0,
             "cost_currency": "USD", "currency_count": 1},
            {"project_id": "p2", "sub_account_id": "s2", "prev_month_cost": 200.0,
             "cost_currency": "JPY", "currency_count": 2},
        ],
        [_guard_row()],
        [],
    )

    with caplog.at_level(py_logging.WARNING):
        batch_module._step_monthly_cost(
            bq, batch_run_at=datetime(2026, 5, 1, tzinfo=timezone.utc), run_id="x"
        )

    # warning が出ていること
    warnings = [r for r in caplog.records if r.levelno == py_logging.WARNING]
    assert any("multiple currencies" in r.message for r in warnings)

    # 処理は継続（agg SELECT / guard / MERGE で計 3 回 query 実行）
    assert bq.query.call_count == 3


def test_monthly_no_warning_when_all_single_currency(batch_module, caplog):
    """全て単一通貨なら warning を出さない（誤検知防止）。"""
    import logging as py_logging

    bq = _bq_with_query_results(
        [{"project_id": "p1", "sub_account_id": "s1", "prev_month_cost": 100.0,
          "cost_currency": "USD", "currency_count": 1}],
        [_guard_row()],
        [],
    )

    with caplog.at_level(py_logging.WARNING):
        batch_module._step_monthly_cost(
            bq, batch_run_at=datetime(2026, 5, 1, tzinfo=timezone.utc), run_id="x"
        )

    warnings = [r for r in caplog.records
                if r.levelno == py_logging.WARNING and "multiple currencies" in r.message]
    assert warnings == []


def test_monthly_guard_aborts_before_merge_when_nothing_matches(batch_module):
    """結合が全ハズレ（空 export / テーブル名・月の誤り / キー不一致）のとき、
    全レコードを 0 円で上書きする前に異常終了し、MERGE を実行しない。"""
    bq = _bq_with_query_results(
        [],   # agg: その月の Export が空（→ _tmp_monthly_cost も空）
        [_guard_row(link_count=262, cost_count=0, matched_count=0)],  # 1 件もマッチせず
    )
    with pytest.raises(RuntimeError, match="guard"):
        batch_module._step_monthly_cost(
            bq, batch_run_at=datetime(2026, 5, 1, tzinfo=timezone.utc), run_id="x"
        )

    # MERGE には到達しない（query は agg と guard の 2 回のみ）
    assert bq.query.call_count == 2
    # MERGE 文に固有のトークンで判定（agg_sql 内のコメントに "MERGE" の語が含まれるため）
    assert not any("COALESCE(C.prev_month_cost, 0.0)" in s for s in _capture_query_sql(bq))


def test_monthly_guard_skips_when_links_table_empty(batch_module):
    """links テーブルが空（初回デプロイ直後）なら保護対象が無いのでガードを発火させず MERGE する。"""
    bq = _bq_with_query_results(
        [{"project_id": "p1", "sub_account_id": "s1", "prev_month_cost": 0.0,
          "cost_currency": "USD", "currency_count": 1}],
        [_guard_row(link_count=0, cost_count=1, matched_count=0)],   # links 空 → matched 0 でも素通り
        [],
    )
    batch_module._step_monthly_cost(
        bq, batch_run_at=datetime(2026, 5, 1, tzinfo=timezone.utc), run_id="x"
    )
    assert bq.query.call_count == 3
    assert "MERGE" in _capture_query_sql(bq)[-1]
