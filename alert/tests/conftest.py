"""alert/main.py の import 前に env と Cloud Logging client をモックする。"""
import importlib.util
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture(scope="session", autouse=True)
def _env_and_logging_setup():
    os.environ.setdefault("GCP_PROJECT_ID", "test-project")
    os.environ.setdefault("BQ_DATASET", "billing_data_test")
    os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test-token")
    os.environ.setdefault("BILLING_EXPORT_PROJECT_ID", "test-export-project")
    os.environ.setdefault("BILLING_EXPORT_DATASET", "billing_data")
    os.environ.setdefault("BILLING_EXPORT_TABLE", "gcp_billing_export_v1_TEST")

    import google.cloud.logging as gcl
    gcl.Client = MagicMock()

    yield


@pytest.fixture(scope="session")
def alert_module():
    """alert/main.py を一意の名前で読み込み、batch 側の main と衝突しないようにする。"""
    path = Path(__file__).resolve().parent.parent / "main.py"
    spec = importlib.util.spec_from_file_location("alert_main", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
