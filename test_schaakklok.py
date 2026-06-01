import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from schaakklok import ChessClock, ClockMode, Player, PlayerSettings

T0 = datetime(2026, 4, 30, 12, 0, 0, tzinfo=timezone.utc)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def _make(white_s=300, black_s=300, inc_w=0, inc_b=0, mode=ClockMode.REGULAR):
    return ChessClock(
        PlayerSettings(white_s, inc_w),
        PlayerSettings(black_s, inc_b),
        mode,
    )


# ---------------------------------------------------------------------------
# _format_time
# ---------------------------------------------------------------------------

f = ChessClock._format_time

def test_format_zero():         assert f(0)         == "0:00"
def test_format_negative():     assert f(-500)      == "0:00"
def test_format_tenths():       assert f(1_500)     == "0:01"
def test_format_minutes():      assert f(65_000)    == "1:05"
def test_format_five_minutes(): assert f(300_000)   == "5:00"
def test_format_hours():        assert f(3_661_000) == "1:01:01"


# ---------------------------------------------------------------------------
# Regular mode
# ---------------------------------------------------------------------------

def test_initial_remaining():
    clock = _make(white_s=300, black_s=180)
    assert clock._remaining_ms == [300_000, 180_000]

def test_start_sets_white_active():
    clock = _make()
    with patch.object(ChessClock, '_now', return_value=T0):
        clock.start()
    assert clock._active == Player.WHITE

def test_double_start_raises():
    clock = _make()
    with patch.object(ChessClock, '_now', return_value=T0):
        clock.start()
    with pytest.raises(RuntimeError):
        clock.start()

def test_fischer_increment_applied():
    clock = _make(inc_w=5)
    with patch.object(ChessClock, '_now', return_value=T0):
        clock.start()
    with patch.object(ChessClock, '_now', return_value=at(10)):
        clock.switch_turn()          # white used 10 s, +5 s → 295 s
    assert clock._remaining_ms[0] == 295_000

def test_increment_not_applied_to_opponent():
    clock = _make(inc_w=5)
    with patch.object(ChessClock, '_now', return_value=T0):
        clock.start()
    with patch.object(ChessClock, '_now', return_value=at(10)):
        clock.switch_turn()
    assert clock._remaining_ms[1] == 300_000   # black untouched

def test_asymmetric_increments():
    clock = _make(inc_w=5, inc_b=3)
    with patch.object(ChessClock, '_now', return_value=T0):
        clock.start()
    with patch.object(ChessClock, '_now', return_value=at(10)):
        clock.switch_turn()          # white: 300 - 10 + 5 = 295
    with patch.object(ChessClock, '_now', return_value=at(16)):
        clock.switch_turn()          # black: 300 - 6 + 3 = 297
    assert clock._remaining_ms[0] == 295_000
    assert clock._remaining_ms[1] == 297_000

def test_time_never_negative():
    clock = _make(white_s=5)
    with patch.object(ChessClock, '_now', return_value=T0):
        clock.start()
    with patch.object(ChessClock, '_now', return_value=at(999)):
        assert clock.is_flagged(Player.WHITE)
        assert not clock.is_flagged(Player.BLACK)
        white_ms, _ = clock._snapshot_remaining_ms()
    assert white_ms == 0       # snapshot clamps to zero; never goes negative

def test_switch_blocked_when_flagged():
    clock = _make(white_s=5)
    with patch.object(ChessClock, '_now', return_value=T0):
        clock.start()
    clock._remaining_ms[0] = 0      # force flag
    with pytest.raises(RuntimeError):
        clock.switch_turn()

def test_switch_blocked_when_not_running():
    with pytest.raises(RuntimeError):
        _make().switch_turn()


# ---------------------------------------------------------------------------
# Hourglass mode
# ---------------------------------------------------------------------------

def test_hourglass_total_time_constant():
    clock = _make(white_s=10, black_s=10, mode=ClockMode.HOURGLASS)
    total = sum(clock._remaining_ms)
    with patch.object(ChessClock, '_now', return_value=T0):
        clock.start()
    # snapshot mid-turn (white active) must preserve total
    with patch.object(ChessClock, '_now', return_value=at(3)):
        assert sum(clock._snapshot_remaining_ms()) == total
        clock.switch_turn()
    # committed state must also preserve total
    assert sum(clock._remaining_ms) == total
    # snapshot mid-turn (black active) must preserve total
    with patch.object(ChessClock, '_now', return_value=at(5)):
        assert sum(clock._snapshot_remaining_ms()) == total

def test_hourglass_time_transferred_correctly():
    clock = _make(white_s=10, black_s=10, mode=ClockMode.HOURGLASS)
    with patch.object(ChessClock, '_now', return_value=T0):
        clock.start()
    with patch.object(ChessClock, '_now', return_value=at(4)):
        clock.switch_turn()          # white −4 s, black +4 s
    assert clock._remaining_ms[0] == 6_000
    assert clock._remaining_ms[1] == 14_000

def test_hourglass_increment_ignored():
    clock = _make(white_s=10, black_s=10, inc_w=99, inc_b=99, mode=ClockMode.HOURGLASS)
    with patch.object(ChessClock, '_now', return_value=T0):
        clock.start()
    with patch.object(ChessClock, '_now', return_value=at(3)):
        clock.switch_turn()
    assert sum(clock._remaining_ms) == 20_000   # no bonus added

def test_hourglass_total_time_constant_100_switches():
    # Use a generous initial time so neither player flags during the test.
    clock = _make(white_s=300, black_s=300, mode=ClockMode.HOURGLASS)
    total = sum(clock._remaining_ms)
    # Non-integer-second move durations (ms).
    move_durations_ms = [
        731, 1423, 892, 2017, 456, 1337, 99, 3201, 567, 1111,
    ] * 10  # 100 moves total

    # wall_ms tracks absolute milliseconds elapsed since T0.
    wall_ms = 0
    with patch.object(ChessClock, '_now', return_value=at(0)):
        clock.start()

    for i, duration_ms in enumerate(move_durations_ms):
        # Every 11th move (0-indexed): simulate a pause mid-move then resume,
        # mimicking server.py's /press logic (pressing the active player's button
        # while paused → resume()).
        if i % 11 == 5:
            # Pause 500 ms into the move.
            wall_ms += 500
            with patch.object(ChessClock, '_now', return_value=at(wall_ms / 1000)):
                assert sum(clock._snapshot_remaining_ms()) == total, \
                    f"snapshot wrong before pause at move {i + 1}"
                clock.pause()
            assert sum(clock._remaining_ms) == total, \
                f"committed total wrong after pause at move {i + 1}"

            # Resume 1337 ms later (active player presses their button).
            wall_ms += 1337
            with patch.object(ChessClock, '_now', return_value=at(wall_ms / 1000)):
                clock.resume()

            # Spend the remaining portion of the move duration.
            wall_ms += duration_ms - 500
        else:
            wall_ms += duration_ms

        with patch.object(ChessClock, '_now', return_value=at(wall_ms / 1000)):
            assert sum(clock._snapshot_remaining_ms()) == total, \
                f"snapshot wrong before switch {i + 1}"
            clock.switch_turn()
        assert sum(clock._remaining_ms) == total, \
            f"committed total wrong after switch {i + 1}"


def _parse_time_string(s: str) -> int:
    """Parse a time string produced by _format_time back to milliseconds.

    Accepts ``M:SS``, ``H:MM:SS``, or ``0:00`` (flagged).
    Returns ms at whole-second resolution.
    """
    parts = s.split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        hours = 0
    else:
        hours, minutes, seconds = parts
    return (int(hours) * 3600 + int(minutes) * 60 + int(seconds)) * 1000


def test_hourglass_time_string_totals_constant():
    """time_string() values for White and Black must sum to a constant in hourglass mode."""
    clock = _make(white_s=30, black_s=30, mode=ClockMode.HOURGLASS)
    # Total as visible in tenths: both start on a clean 100 ms boundary.
    with patch.object(ChessClock, '_now', return_value=at(0)):
        clock.start()

    displayed_total = (
        _parse_time_string(clock.time_string(Player.WHITE))
        + _parse_time_string(clock.time_string(Player.BLACK))
    )

    move_durations_ms = [731, 1423, 892, 2017, 456, 1337, 99, 801, 567, 1111] * 3
    wall_ms = 0

    for i, duration_ms in enumerate(move_durations_ms):
        wall_ms += duration_ms
        with patch.object(ChessClock, '_now', return_value=at(wall_ms / 1000)):
            w = _parse_time_string(clock.time_string(Player.WHITE))
            b = _parse_time_string(clock.time_string(Player.BLACK))
            assert w + b == displayed_total, \
                f"displayed total wrong at move {i + 1}: {w} + {b} = {w + b} != {displayed_total}"
            clock.switch_turn()


# ---------------------------------------------------------------------------
# Flag detection
# ---------------------------------------------------------------------------

def test_not_flagged_with_time():
    clock = _make()
    with patch.object(ChessClock, '_now', return_value=T0):
        clock.start()
        assert not clock.is_flagged(Player.WHITE)
        assert not clock.is_flagged(Player.BLACK)

def test_flagged_when_time_exceeded():
    clock = _make(white_s=5)
    with patch.object(ChessClock, '_now', return_value=T0):
        clock.start()
    with patch.object(ChessClock, '_now', return_value=at(10)):
        assert clock.is_flagged(Player.WHITE)
        assert not clock.is_flagged(Player.BLACK)

def test_flagged_string_white():
    clock = _make()
    clock._remaining_ms[0] = 0
    assert "White" in clock.flagged_string()

def test_flagged_string_black():
    clock = _make()
    clock._remaining_ms[1] = 0
    assert "Black" in clock.flagged_string()

def test_flagged_string_both():
    clock = _make()
    clock._remaining_ms = [0, 0]
    assert "Both" in clock.flagged_string()

def test_flagged_string_none():
    assert "No flags" in _make().flagged_string()

def test_invalid_player_raises():
    with pytest.raises(ValueError):
        _make().is_flagged(Player.NONE)


# ---------------------------------------------------------------------------
# Pause / resume
# ---------------------------------------------------------------------------

def test_pause_deducts_time_no_increment():
    clock = _make(inc_w=5)
    with patch.object(ChessClock, '_now', return_value=T0):
        clock.start()
    with patch.object(ChessClock, '_now', return_value=at(10)):
        clock.pause()               # 10 s deducted, no +5 s bonus
    assert clock._remaining_ms[0] == 290_000

def test_time_frozen_while_paused():
    clock = _make()
    with patch.object(ChessClock, '_now', return_value=T0):
        clock.start()
    with patch.object(ChessClock, '_now', return_value=at(5)):
        clock.pause()
    # Not running → _elapsed_ms() == 0 → snapshot is stable
    snap = clock._snapshot_remaining_ms()
    assert snap == (295_000, 300_000)
    assert clock._snapshot_remaining_ms() == snap

def test_resume_restores_same_player():
    clock = _make()
    with patch.object(ChessClock, '_now', return_value=T0):
        clock.start()
        clock.pause()
        clock.resume()
    assert clock._active is Player.WHITE

def test_resume_raises_if_running():
    clock = _make()
    with patch.object(ChessClock, '_now', return_value=T0):
        clock.start()
    with pytest.raises(RuntimeError):
        clock.resume()

def test_pause_raises_if_not_running():
    with pytest.raises(RuntimeError):
        _make().pause()


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

def test_history_event_sequence():
    clock = _make()
    with patch.object(ChessClock, '_now', return_value=T0):
        clock.start()
        clock.pause()
        clock.resume()
    with patch.object(ChessClock, '_now', return_value=at(5)):
        clock.switch_turn()
    assert [e.event for e in clock.history()] == ["start", "pause", "resume", "switch"]

def test_history_timestamps_are_utc():
    clock = _make()
    with patch.object(ChessClock, '_now', return_value=T0):
        clock.start()
    assert clock.history()[0].timestamp.tzinfo == timezone.utc

def test_history_returns_copy():
    clock = _make()
    with patch.object(ChessClock, '_now', return_value=T0):
        clock.start()
    clock.history().clear()         # mutate the returned copy
    assert len(clock.history()) == 1   # original unaffected

def test_history_remaining_snapshot():
    clock = _make(white_s=300)
    with patch.object(ChessClock, '_now', return_value=T0):
        clock.start()
    with patch.object(ChessClock, '_now', return_value=at(10)):
        clock.switch_turn()
    entry = clock.history()[-1]
    assert entry.white_remaining_ms == 290_000  # no increment
    assert entry.black_remaining_ms == 300_000


# ---------------------------------------------------------------------------
# current_player and time_string
# ---------------------------------------------------------------------------

def test_current_player_not_started():
    assert _make().current_player() is Player.NONE

def test_current_player_white_after_start():
    clock = _make()
    with patch.object(ChessClock, '_now', return_value=T0):
        clock.start()
        assert clock.current_player() is Player.WHITE

def test_current_player_black_after_switch():
    clock = _make()
    with patch.object(ChessClock, '_now', return_value=T0):
        clock.start()
        clock.switch_turn()
        assert clock.current_player() is Player.BLACK

def test_current_player_preserved_after_pause():
    clock = _make()
    with patch.object(ChessClock, '_now', return_value=T0):
        clock.start()
        clock.pause()
    assert clock.current_player() is Player.WHITE

def test_time_string_white():
    clock = _make(white_s=300, black_s=180)
    with patch.object(ChessClock, '_now', return_value=T0):
        clock.start()
        assert clock.time_string(Player.WHITE) == "5:00"

def test_time_string_black():
    clock = _make(white_s=300, black_s=180)
    with patch.object(ChessClock, '_now', return_value=T0):
        clock.start()
        assert clock.time_string(Player.BLACK) == "3:00"
