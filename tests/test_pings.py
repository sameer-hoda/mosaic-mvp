"""Worker F: real-time ping gating — all three conditions must hold."""

import datetime

from hermes_bot import pings as pg


def _noon():
    return datetime.datetime(2026, 9, 21, 12, 0,
                             tzinfo=datetime.timezone(datetime.timedelta(hours=5,
                                                                         minutes=30)))


def _base(**over):
    kw = dict(direct=True, owner_silence_s=20 * 60, muted=False,
              pings_today=0, now=_noon())
    kw.update(over)
    return kw


def test_ping_fires_when_all_conditions_hold():
    ok, reason = pg.should_ping(**_base())
    assert (ok, reason) == (True, "ok")


def test_ping_gated_on_each_condition():
    assert pg.should_ping(**_base(direct=False)) == (False, "not-direct")
    assert pg.should_ping(**_base(owner_silence_s=5 * 60)) == (False, "owner-active")
    assert pg.should_ping(**_base(muted=True)) == (False, "muted")


def test_ping_cap_6_per_day():
    assert pg.should_ping(**_base(pings_today=6)) == (False, "cap")


def test_no_ping_in_quiet_hours():
    night = _noon().replace(hour=23, minute=30)
    assert pg.should_ping(**_base(now=night)) == (False, "quiet-hours")
    early = _noon().replace(hour=6, minute=0)
    assert pg.should_ping(**_base(now=early)) == (False, "quiet-hours")


def test_direct_address_rules():
    assert pg.is_direct_address("Sameer, can you call?", "g@g.us", ("sameer",)) is True
    assert pg.is_direct_address("random banter lol", "g@g.us", ("sameer",)) is False
    assert pg.is_direct_address("random banter lol", "dev@s.whatsapp.net", ()) is True


def test_format_is_one_line_plus_draft_ref():
    text = pg.format_ping("Dev", "Dev asked for the Q3 numbers", "7")
    lines = text.splitlines()
    assert len(lines) == 2 and "draft 7" in text and "Dev" in lines[0]
