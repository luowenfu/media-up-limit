import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from emby.browse import settler


class UserSwitchMetaTest(unittest.TestCase):
    def setUp(self):
        self.name = 'test-switch-meta'
        settler._browse_key_meta.pop(self.name, None)

    def tearDown(self):
        settler._browse_key_meta.pop(self.name, None)

    def test_switch_keeps_client_from_previous_profile(self):
        sid = 'sid-1'
        uid = 'user-lwf'
        settler._remember_key_meta(self.name, f'browse:{uid}:{sid}', {
            'user_name': 'lwf',
            'user_id': uid,
        })
        meta = settler._meta_for_prev_user_switch(self.name, sid, uid, {
            'user_name': 'lwf',
            'user_id': uid,
            'client': 'Emby Web',
            'device_name': 'Android WebView Android',
            'client_ip': '114.138.187.123',
        })
        self.assertEqual(meta['user_name'], 'lwf')
        self.assertEqual(meta['client'], 'Emby Web')
        self.assertEqual(meta['device_name'], 'Android WebView Android')
        self.assertEqual(meta['client_ip'], '114.138.187.123')

    def test_thin_cache_does_not_wipe_client(self):
        key = 'browse:user-lwf:sid-1'
        settler._remember_key_meta(self.name, key, {
            'user_name': 'lwf',
            'user_id': 'user-lwf',
            'client': 'Emby Web',
            'device_name': 'Android WebView Android',
            'client_ip': '114.138.187.123',
        })
        settler._remember_key_meta(self.name, key, {
            'user_name': 'lwf',
            'user_id': 'user-lwf',
        })
        meta = settler._meta_for_browse_key(self.name, key)
        self.assertEqual(meta['client'], 'Emby Web')
        self.assertEqual(meta['device_name'], 'Android WebView Android')
        self.assertEqual(meta['client_ip'], '114.138.187.123')


if __name__ == '__main__':
    unittest.main()
