import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from qb.collect_log import format_byte_amount, format_collect_summary

MB = 1024 * 1024
GB = 1024 * MB


def summary(**overrides):
    fields = dict(
        name='qb-做种-WAN2',
        elapsed=600,
        ticks=120,
        expected_ticks=120,
        delta_up=0,
        delta_dl=0,
        cycle_upload=round(88.12 * GB),
        is_quota_limited=False,
        limit_kbps=0,
        backfill_up=0,
        backfill_dl=0,
    )
    fields.update(overrides)
    return format_collect_summary(**fields)


class CollectLogFormatTest(unittest.TestCase):
    def test_busy_line_keeps_download_and_drops_session(self):
        text = summary(
            delta_up=round(147.88 * MB),
            delta_dl=round(1540.40 * MB),
            cycle_upload=round(1117.68 * GB),
        )
        self.assertEqual(
            text,
            '[qb-做种-WAN2] 10分钟 上传 +147.9MB，下载 +1.50GB，周期 1117.7GB，未限速',
        )
        self.assertNotIn('会话', text)
        self.assertNotIn('采样', text)

    def test_idle_line_omits_zero_download(self):
        text = summary(name='qb-影视-WAN1')
        self.assertEqual(
            text,
            '[qb-影视-WAN1] 10分钟 上传 +0，周期 88.1GB，未限速',
        )

    def test_limit_and_backfill_append_after_cycle(self):
        text = summary(
            delta_up=12 * MB,
            cycle_upload=round(1200.4 * GB),
            is_quota_limited=True,
            limit_kbps=128,
            backfill_up=20 * MB,
        )
        self.assertEqual(
            text,
            '[qb-做种-WAN2] 10分钟 上传 +12.0MB，周期 1200.4GB，限速 128KB/s，补录上传 20.0MB',
        )

    def test_backfill_download_only_when_present(self):
        text = summary(backfill_dl=round(1.25 * GB))
        self.assertIn('补录下载 1.25GB', text)
        self.assertNotIn('补录上传', text)

    def test_low_sample_count_is_appended(self):
        text = summary(ticks=40)
        self.assertTrue(text.endswith('，采样 40 次'))

    def test_full_sample_count_stays_quiet(self):
        self.assertNotIn('采样', summary(ticks=96))
        self.assertNotIn('采样', summary(ticks=120, elapsed=480))

    def test_short_window_names_samples(self):
        text = summary(elapsed=45, ticks=9, delta_up=12 * MB, cycle_upload=round(1.5 * GB))
        self.assertEqual(
            text,
            '[qb-做种-WAN2] 45秒 上传 +12.0MB，周期 1.50GB，未限速，采样 9 次',
        )

    def test_tiny_amount_does_not_round_to_zero(self):
        self.assertEqual(format_byte_amount(0), '0')
        self.assertEqual(format_byte_amount(1024), '<0.1MB')
        self.assertEqual(format_byte_amount(GB - 1), f'{(GB - 1) / MB:.1f}MB')


if __name__ == '__main__':
    unittest.main()
