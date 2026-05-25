"""_prev_month_yyyymm の境界値テスト。"""
from datetime import datetime, timezone


def test_normal_month(batch_module):
    # 3月実行 → 前月は 02
    assert batch_module._prev_month_yyyymm(datetime(2026, 3, 5, tzinfo=timezone.utc)) == "202602"


def test_january_rolls_to_previous_december(batch_module):
    # 1月実行 → 前年 12月
    assert batch_module._prev_month_yyyymm(datetime(2026, 1, 5, tzinfo=timezone.utc)) == "202512"


def test_single_digit_month_is_zero_padded(batch_module):
    # 10月実行 → 前月 "09" にゼロ埋め
    assert batch_module._prev_month_yyyymm(datetime(2026, 10, 1, tzinfo=timezone.utc)) == "202609"


def test_december_returns_november(batch_module):
    assert batch_module._prev_month_yyyymm(datetime(2026, 12, 31, tzinfo=timezone.utc)) == "202611"
