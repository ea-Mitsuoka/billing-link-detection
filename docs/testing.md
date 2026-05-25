# テスト戦略

このリポジトリのテスト構成と、**テストを書く・実行する** ためのガイド。

______________________________________________________________________

## TL;DR

```bash
# プロジェクトルートで実行
make test                  # or `python -m pytest`

# 結果: 19 passed in ~0.5s
```

依存パッケージのインストール：

```bash
make install               # batch + alert + dev deps を一括インストール

# 手動で行う場合
uv pip install pytest pytest-mock --system
uv pip sync batch/requirements.txt --system
uv pip sync alert/requirements.txt --system
```

______________________________________________________________________

## テスト構造

```
billing-link-detection/
├── pyproject.toml              # pytest 設定（testpaths と warning filter）
├── batch/
│   ├── main.py
│   └── tests/
│       ├── conftest.py         # env + Cloud Logging client モック + 遅延 import
│       ├── test_prev_month.py  # _prev_month_yyyymm の境界値
│       ├── test_sql_shape.py   # SQL 文字列の構造的妥当性
│       └── test_fetch_api.py   # Billing API レコード組み立て
└── alert/
    ├── main.py
    └── tests/
        ├── conftest.py         # env + Cloud Logging client モック + 遅延 import
        └── test_alert_handler.py
```

______________________________________________________________________

## 設計のキモ

### 1. なぜ `importlib.util` で動的 import するのか

`batch/main.py` と `alert/main.py` は **どちらも `main` というモジュール名** で、`sys.path` に両方の親ディレクトリを足すと、後勝ちで上書きされる衝突が起きる。

`conftest.py` で `importlib.util.spec_from_file_location("batch_main", path)` のように **一意な名前を付けて** ロードすることで、互いに干渉せず共存できる。

```python
@pytest.fixture(scope="session")
def batch_module():
    path = Path(__file__).resolve().parent.parent / "main.py"
    spec = importlib.util.spec_from_file_location("batch_main", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
```

### 2. なぜ env を `conftest.py` で先に設定するのか

`main.py` の **module スコープで `os.environ["GCP_PROJECT_ID"]` を読んでいる** ため、import の瞬間に値がないと `KeyError` で落ちる。`pytest.fixture(autouse=True)` で先に `os.environ.setdefault(...)` を仕込み、その後 `batch_module` fixture が main.py を import する流れ。

### 3. なぜ `google.cloud.logging.Client` をモックするのか

`main.py` の **module スコープで** `google.cloud.logging.Client().setup_logging()` を呼んでいるため、テスト環境で本物の Cloud Logging を叩こうとして失敗する。`conftest.py` で `gcl.Client = MagicMock()` に差し替えてからロードする。

### 4. なぜ `tests/__init__.py` を置かないのか

pytest は `__init__.py` がある場合、テストパッケージとして名前空間を共有する。`batch/tests/__init__.py` と `alert/tests/__init__.py` が両方あると **「`tests`」というパッケージ名で衝突** し `ImportPathMismatchError` になる。`__init__.py` を置かないことで、pytest がディレクトリベースで rootdir 相対の独立した名前空間として扱う。

______________________________________________________________________

## テストの 3 つの層

### 層 1: 純粋関数のテスト（境界値・代表値）

例: `batch/tests/test_prev_month.py`

```python
def test_january_rolls_to_previous_december(batch_module):
    assert batch_module._prev_month_yyyymm(
        datetime(2026, 1, 5, tzinfo=timezone.utc)
    ) == "202512"
```

- 副作用なし、引数→戻り値の対応を確認
- 1 月 → 前年 12 月の境界、ゼロ埋め有無などをカバー

### 層 2: SQL 文字列の構造的妥当性

例: `batch/tests/test_sql_shape.py`

実 BigQuery を叩かず、**生成 SQL が必須要素を含むことを文字列マッチで保証** する。リファクタで MERGE の WHEN 分岐が落ちたり、トランザクション境界が消えたりする事故を防ぐ。

```python
def test_step4_5_merge_contains_all_branches(batch_module):
    bq = MagicMock()
    batch_module._step4_5_merge_unlinked(bq, batch_run_at=..., run_id="x")
    sql = bq.query.call_args.args[0]

    assert "BEGIN TRANSACTION" in sql
    assert "WHEN MATCHED AND T.status = 'UNLINKED' THEN UPDATE" in sql
    assert "WHEN NOT MATCHED THEN INSERT" in sql
    assert "BILLING_DISABLED" in sql
```

**注意**: あくまで「構造の崩れ」を検知するセーフティネット。実 SQL の正しさは BigQuery 上での結合テストでしか確認できない。

### 層 3: 副作用のモッキング（API クライアント / HTTP）

例: `alert/tests/test_alert_handler.py`

```python
def test_results_posted_to_slack_with_formatted_text(alert_module):
    rows = [{"project_id": "p1", "cost": 100}]
    with patch.object(alert_module.bigquery, "Client") as bq_cls, \
         patch.object(alert_module.requests, "post", return_value=_mk_slack_ok()) as post:
        bq_cls.return_value.query.return_value.result.return_value = iter(rows)
        req = _mk_request("SELECT 1", "#alerts", "課金開始")
        alert_module.alert_handler(req)

    payload = post.call_args.kwargs["json"]
    assert payload["channel"] == "#alerts"
    assert "project_id: p1" in payload["text"]
```

- `bigquery.Client` と `requests.post` を `unittest.mock.patch.object` で差し替え
- リクエストボディの整形・MAX_ROWS トランケート・エラー伝播などのパスを網羅

______________________________________________________________________

## カバレッジ観点（現状）

### batch

| テスト | カバー内容 |
|---|---|
| `test_prev_month.py` | 月境界（1月→前年12月）、ゼロ埋め |
| `test_sql_shape.py::test_step1_reset_resets_only_yesterday` | リセット対象が `< CURRENT_DATE('Asia/Tokyo')` であること |
| `test_sql_shape.py::test_step4_5_merge_contains_all_branches` | MERGE の 3 分岐 + UNLINKED 検知 + トランザクション境界 |
| `test_sql_shape.py::test_step4_5_passes_batch_run_at_param` | `@batch_run_at` パラメータバインド |
| `test_sql_shape.py::test_monthly_merge_uses_left_join_for_zero_cost` | LEFT JOIN + COALESCE(0) で全件補完 |
| `test_sql_shape.py::test_monthly_aggregation_filters_by_prev_month_yyyymm` | 前月 YYYYMM フィルタが正しく入る |
| `test_sql_shape.py::test_step6_7_skipped_when_export_table_missing` | `BILLING_EXPORT_TABLE` 未設定時の早期 return |
| `test_fetch_api.py::test_records_have_expected_shape` | Billing API レスポンス → レコード dict の組み立て |
| `test_fetch_api.py::test_parent_account_filter_passed_to_billing_api` | `master_billing_account` フィルタが正しい形式で渡される |

### alert

| テスト | カバー内容 |
|---|---|
| `test_query_substitutes_project_and_dataset` | `{project}/{dataset}` 置換 |
| `test_query_substitutes_billing_export_vars` | `{billing_export_project/dataset/table}` 置換 |
| `test_no_results_returns_200_without_posting` | 0 件時は Slack に投げない |
| `test_results_posted_to_slack_with_formatted_text` | 通常通知の整形 |
| `test_truncates_when_exceeds_max_rows` | MAX_ROWS 超過時のトランケート + サフィックス |
| `test_slack_api_error_raises` | Slack `ok=false` で例外 |
| `test_max_bytes_billed_is_set` | 課金事故防止の `maximum_bytes_billed` 設定 |

______________________________________________________________________

## テストを追加する

### バッチに新しい処理を追加した

1. `batch/main.py` に関数を追加
1. `batch/tests/test_xxx.py` を新規作成
1. `batch_module` fixture 経由でアクセス：
   ```python
   def test_my_new_logic(batch_module):
       result = batch_module.my_new_function(...)
       assert result == ...
   ```
1. `python -m pytest batch/tests/test_xxx.py -v` で確認

### アラートに新しいパスを追加した

1. `alert/main.py` を編集
1. `alert/tests/test_alert_handler.py` にケース追加（または別ファイル）
1. BigQuery / requests をモックして HTTP リクエスト → レスポンスを検証

### 「実 GCP に投げる結合テスト」を書きたい場合

現状はモックのみ。結合テストは **`billing_data_test` データセット** に対して走らせる方針（[../README.md](../README.md) §環境変数の設定 参照）。

実装する場合：

```python
@pytest.mark.integration
def test_step4_5_merge_against_real_bq():
    # 事前にテストデータを INSERT
    # 本物の _step4_5_merge_unlinked を呼ぶ
    # 結果を SELECT して検証
    # 後始末で DELETE
```

`pytest -m integration` でだけ走らせるよう `pyproject.toml` にマーカーを追加し、CI ではスキップ（または別 job）にする。

______________________________________________________________________

## CI での実行

`.github/workflows/deploy.yml` の `lint-and-test` ジョブで自動実行される：

```yaml
- name: Run unit tests
  run: pytest

- name: Terraform fmt check
  run: terraform fmt -check -recursive

- name: Terraform validate
  run: |
    terraform init -backend=false
    terraform validate
```

PR が main にマージされる前に必ず通る。**ローカルで同じチェックを走らせる** には `make lint && make test`。

______________________________________________________________________

## トラブルシューティング

### `KeyError: 'GCP_PROJECT_ID'`

`conftest.py` の env 設定 fixture が `autouse=True` でなくなったか、import 順が崩れた。`conftest.py` が `tests/` ディレクトリ直下にあることを確認。

### `ImportPathMismatchError`

`tests/__init__.py` がどこかに残っている。`find . -name __init__.py -path "*/tests/*"` で確認して削除。

### `AttributeError: module 'main' has no attribute 'alert_handler'`

`sys.path.insert` で `import main` する旧実装では、batch と alert が衝突する。`importlib.util.spec_from_file_location` で一意名ロードに切り替える（`conftest.py` 参照）。

### `DeprecationWarning` でテストが落ちる

`pyproject.toml` の `filterwarnings` を `"ignore::DeprecationWarning"` にしてある。サードパーティライブラリ（opentelemetry 等）の警告で落ちないよう緩めている。
