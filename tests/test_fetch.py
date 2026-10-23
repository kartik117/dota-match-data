import io
import json
import pathlib
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
import fetch
import validate


def match(mid, lobby=0):
    return dict(match_id=mid, start_time=mid, player_slot=129,
                radiant_win=False, hero_id=48, lobby_type=lobby)


def detail(mid, version=22):
    return dict(match_id=mid, duration=2400, version=version,
                players=[dict(account_id=int(fetch.ACCOUNT))])


class FetchTests(unittest.TestCase):
    def test_rate_limit_honors_retry_after(self):
        error = urllib.error.HTTPError('url', 429, 'limited', {'Retry-After': '7'}, None)
        with patch.object(fetch.urllib.request, 'urlopen', side_effect=[error, io.BytesIO(b'[]')]), \
                patch.object(fetch.time, 'sleep') as sleep:
            self.assertEqual(fetch.fetch('/test'), [])
            sleep.assert_called_once_with(7)

    def test_transient_errors_and_invalid_json_exhaust_retries(self):
        for error in [urllib.error.URLError('offline'),
                      urllib.error.HTTPError('url', 503, 'unavailable', {}, None)]:
            with self.subTest(error=error), patch.object(fetch.urllib.request, 'urlopen', side_effect=error) as request, \
                    patch.object(fetch.time, 'sleep'):
                self.assertIsNone(fetch.fetch('/test'))
                self.assertEqual(request.call_count, fetch.ATTEMPTS)
        with patch.object(fetch.urllib.request, 'urlopen', side_effect=lambda *a, **kw: io.BytesIO(b'not json')), \
                patch.object(fetch.time, 'sleep'):
            self.assertIsNone(fetch.fetch('/test'))

    def test_permanent_error_is_not_retried(self):
        error = urllib.error.HTTPError('url', 404, 'missing', {}, None)
        with patch.object(fetch.urllib.request, 'urlopen', side_effect=error) as request:
            self.assertIsNone(fetch.fetch('/test'))
            self.assertEqual(request.call_count, 1)

    def test_long_cooldown_defers_request(self):
        error = urllib.error.HTTPError('url', 429, 'limited', {'Retry-After': '3600'}, None)
        with patch.object(fetch.urllib.request, 'urlopen', side_effect=error), patch.object(fetch.time, 'sleep') as sleep:
            self.assertIsNone(fetch.fetch('/test'))
            sleep.assert_not_called()

    def test_failed_history_preserves_all_data(self):
        for history in [None, [], {'error': 'bad'}, [None], [match(1), match(1)]]:
            with self.subTest(history=history), tempfile.TemporaryDirectory() as temp, \
                    patch.object(fetch, 'OUT', pathlib.Path(temp)), patch.object(fetch, 'fetch', return_value=history):
                target = pathlib.Path(temp) / 'matches.json'
                target.write_text('[{"existing": true}]')
                with self.assertRaises(SystemExit):
                    fetch.main()
                self.assertEqual(target.read_text(), '[{"existing": true}]')
                self.assertFalse((pathlib.Path(temp) / 'summary.json').exists())

    def test_all_modes_unparsed_missing_cached_and_stale_details(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(fetch, 'OUT', pathlib.Path(temp)), \
                patch.object(fetch, 'DETAIL_COUNT', 4), patch.object(fetch.time, 'sleep'):
            fetch.save(fetch.OUT / 'matches' / '2.json', detail(2))
            fetch.save(fetch.OUT / 'matches' / '1.json', detail(1))
            fetch.save(fetch.OUT / 'matches' / '99.json', detail(99))
            history = [match(1, 7), match(4), match(2), match(3)]
            with patch.object(fetch, 'fetch', side_effect=[history, detail(4, None), None, None, detail(1, None)]):
                fetch.main()
            summary = json.loads((fetch.OUT / 'summary.json').read_text())
            self.assertEqual([s['match_id'] for s in summary['detail_status']], [4, 3, 2, 1])
            self.assertEqual(summary['detail_count_updated'], 1)
            self.assertEqual(summary['detail_count_available'], 3)
            self.assertEqual(summary['detail_count_parsed'], 2)
            self.assertFalse((fetch.OUT / 'matches' / '99.json').exists())
            self.assertEqual(json.loads((fetch.OUT / 'matches' / '1.json').read_text())['version'], 22)
            validate.validate()

    def test_wrong_player_and_error_payload_rejected(self):
        self.assertFalse(fetch.valid_detail({'match_id': 1, 'error': 'unavailable'}, 1))
        self.assertFalse(fetch.valid_detail(dict(match_id=1, duration=50, players=[{'account_id': 123}]), 1))
        self.assertFalse(fetch.valid_detail(detail(2), 1))


if __name__ == '__main__':
    unittest.main()
