"""Step2 (Billing API 取得) のレコード組み立て検証。"""
from unittest.mock import MagicMock, patch


def _mk_sub_account(name: str, display_name: str, is_open: bool):
    sa = MagicMock()
    sa.name = name
    sa.display_name = display_name
    sa.open = is_open
    return sa


def _mk_proj(project_id: str, billing_enabled: bool):
    p = MagicMock()
    p.project_id = project_id
    p.billing_enabled = billing_enabled
    return p


def test_records_have_expected_shape(batch_module):
    sub1 = _mk_sub_account(
        "billingAccounts/AAAAAA-BBBBBB-CCCCCC", "Sub Account 1", True
    )
    sub2 = _mk_sub_account(
        "billingAccounts/DDDDDD-EEEEEE-FFFFFF", "", False
    )

    fake_client = MagicMock()
    fake_client.list_billing_accounts.return_value = [sub1, sub2]
    fake_client.list_project_billing_info.side_effect = [
        [_mk_proj("proj-a", True), _mk_proj("proj-b", False)],
        [_mk_proj("proj-c", True)],
    ]

    with patch.object(
        batch_module.billing_v1, "CloudBillingClient", return_value=fake_client
    ):
        records = batch_module._step2_fetch_api(run_id="x")

    assert len(records) == 3

    r0 = records[0]
    assert r0["parent_account_id"] == batch_module.PARENT_BILLING_ACCOUNT
    assert r0["sub_account_id"] == "AAAAAA-BBBBBB-CCCCCC"  # name の末尾だけ
    assert r0["sub_account_name"] == "Sub Account 1"
    assert r0["project_id"] == "proj-a"
    assert r0["billing_enabled"] is True
    assert r0["sub_account_open"] is True

    r2 = records[2]
    assert r2["sub_account_id"] == "DDDDDD-EEEEEE-FFFFFF"
    assert r2["sub_account_name"] is None  # 空文字は None に正規化
    assert r2["sub_account_open"] is False


def test_display_name_none_is_normalized_to_none(batch_module):
    """Billing API が display_name=None を返すケース（空文字とは別）でも None に正規化される。"""
    sub = _mk_sub_account("billingAccounts/AAAAAA-BBBBBB-CCCCCC", None, True)

    fake_client = MagicMock()
    fake_client.list_billing_accounts.return_value = [sub]
    fake_client.list_project_billing_info.return_value = [_mk_proj("proj-a", True)]

    with patch.object(
        batch_module.billing_v1, "CloudBillingClient", return_value=fake_client
    ):
        records = batch_module._step2_fetch_api(run_id="x")

    assert records[0]["sub_account_name"] is None


def test_parent_account_filter_passed_to_billing_api(batch_module):
    fake_client = MagicMock()
    fake_client.list_billing_accounts.return_value = []

    with patch.object(
        batch_module.billing_v1, "CloudBillingClient", return_value=fake_client
    ):
        batch_module._step2_fetch_api(run_id="x")

    request = fake_client.list_billing_accounts.call_args.kwargs["request"]
    assert (
        request.filter
        == f"master_billing_account=billingAccounts/{batch_module.PARENT_BILLING_ACCOUNT}"
    )
