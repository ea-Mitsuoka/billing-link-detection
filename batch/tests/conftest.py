"""共通フィクスチャ: batch/main.py の import 前に必要な env と logging client を準備する。"""
import importlib.util
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture(scope="session", autouse=True)
def _env_and_logging_setup():
    """batch.main は module-level で os.environ と Cloud Logging クライアントを参照するため
    import 前に環境変数を設定し、google.cloud.logging.Client をモックする。"""
    os.environ.setdefault("GCP_PROJECT_ID", "test-project")
    os.environ.setdefault("BQ_DATASET", "billing_data_test")
    os.environ.setdefault("PARENT_BILLING_ACCOUNT", "AAAAAA-BBBBBB-CCCCCC")
    os.environ.setdefault("BILLING_EXPORT_PROJECT_ID", "test-export-project")
    os.environ.setdefault("BILLING_EXPORT_DATASET", "billing_data")
    os.environ.setdefault("BILLING_EXPORT_TABLE", "gcp_billing_export_v1_TEST")
    os.environ.setdefault("BATCH_TYPE", "daily")

    import google.cloud.logging as gcl
    gcl.Client = MagicMock()

    yield


@pytest.fixture(scope="session")
def batch_module():
    """batch/main.py を一意の名前で読み込み、alert 側の main と衝突しないようにする。"""
    path = Path(__file__).resolve().parent.parent / "main.py"
    spec = importlib.util.spec_from_file_location("batch_main", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
