"""共享期号重建辅助函数测试。"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.adapters.base import rebuild_full_issue, rebuild_short_period

_CST = ZoneInfo('Asia/Shanghai')


def _dt(year, month, day):
    return datetime(year, month, day, 21, 30, tzinfo=_CST)


class TestRebuildFullIssue:
    def test_normal(self):
        assert rebuild_full_issue(_dt(2026, 7, 19), '082') == '2026082'

    def test_year_start(self):
        assert rebuild_full_issue(_dt(2026, 1, 3), '001') == '2026001'

    def test_year_end(self):
        # 年末 12/31 开奖，year 仍 2026（draw_date 为 aware CST）
        assert rebuild_full_issue(_dt(2026, 12, 31), '154') == '2026154'

    def test_defensive_truncation(self, caplog):
        """draw_no 超长（未归一化）→ log warning + 取后 3 位（1B 决策）。"""
        with caplog.at_level(logging.WARNING):
            result = rebuild_full_issue(_dt(2026, 7, 19), '2026082')
        assert result == '2026082'  # '2026082'[-3:] = '082'
        assert 'draw_no_too_long' in caplog.text


class TestRebuildShortPeriod:
    def test_normal(self):
        assert rebuild_short_period(_dt(2026, 7, 19), '082') == '26082'

    def test_year_start(self):
        assert rebuild_short_period(_dt(2026, 1, 3), '001') == '26001'

    def test_year_end(self):
        assert rebuild_short_period(_dt(2026, 12, 31), '154') == '26154'

    def test_defensive_truncation(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = rebuild_short_period(_dt(2026, 7, 19), '2026082')
        assert result == '26082'
        assert 'draw_no_too_long' in caplog.text
