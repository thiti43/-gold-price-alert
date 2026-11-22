import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch
import market_status_notify as m

class MarketTests(unittest.TestCase):
    def test_sunday_before_open(self):
        self.assertEqual(m.phase_at(datetime(2026,11,22,22,59,tzinfo=timezone.utc)), 'weekend_closed')
    def test_sunday_open(self):
        self.assertEqual(m.phase_at(datetime(2026,11,22,23,0,tzinfo=timezone.utc)), 'open')
    def test_friday_close(self):
        self.assertEqual(m.phase_at(datetime(2026,11,20,22,0,tzinfo=timezone.utc)), 'weekend_closed')
    def test_daily_break(self):
        self.assertEqual(m.phase_at(datetime(2026,11,24,22,30,tzinfo=timezone.utc)), 'daily_break')
    def test_daily_reopen(self):
        self.assertEqual(m.phase_at(datetime(2026,11,24,23,0,tzinfo=timezone.utc)), 'open')
    def test_saved_price(self):
        p = m.quote_from_saved({'price':'3538.11','at':'2026-11-20T22:00:00Z'})
        self.assertEqual(p[0],Decimal('3538.11'))
    def test_no_price(self):
        self.assertIsNone(m.quote_from_saved({}))

if __name__ == '__main__': unittest.main()

class Transitions(unittest.TestCase):
    def test_first_close_and_no_repeat(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(m,'STATE',Path(tmp)/'state.json'), patch.object(m,'load_env'), patch.object(m,'read_json') as feed, patch.object(m,'push') as push:
                feed.return_value = {'xau':{'currency':'USD','unit':'oz','price':'3538.11'}, 'data_state':{'as_of':'2026-11-20T21:59:00Z'},'spot_usd_oz':'3538.11'}
                m.main(datetime(2026,11,22,22,0,tzinfo=timezone.utc))
                m.main(datetime(2026,11,22,22,1,tzinfo=timezone.utc))
                self.assertEqual(push.call_count, 1)
                self.assertEqual(m.load()['phase'],'weekend_closed')

    def test_reopen_waits_for_fresh_feed(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(m,'STATE',Path(tmp)/'state.json'), patch.object(m,'load_env'), patch.object(m,'read_json') as feed, patch.object(m,'spot_from_data') as strict, patch.object(m,'push') as push:
                m.save('weekend_closed',(Decimal('3538.11'),datetime(2026,11,20,21,59,tzinfo=timezone.utc)))
                feed.return_value = {'xau':{'currency':'USD','unit':'oz','price':'3538.11'}, 'data_state':{'as_of':'2026-11-20T21:59:00Z'},'spot_usd_oz':'3538.11'}
                strict.side_effect=ValueError('stale')
                m.main(datetime(2026,11,22,23,0,tzinfo=timezone.utc))
                self.assertEqual(push.call_count,0)
                strict.side_effect = None
                strict.return_value=(Decimal('3541.22'),datetime(2026,11,22,23,1,tzinfo=timezone.utc))
                m.main(datetime(2026,11,22,23,1,tzinfo=timezone.utc))
                self.assertEqual(push.call_count,1)
                self.assertEqual(m.load()['phase'],'open')
