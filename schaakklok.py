from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum, auto


# ---------------------------------------------------------------------------
# Supporting types
# ---------------------------------------------------------------------------

class ClockMode(Enum):
    REGULAR = auto()
    HOURGLASS = auto()


class Player(Enum):
    NONE  = -1
    WHITE =  0
    BLACK =  1


@dataclass
class PlayerSettings:
    """Time settings for one player.

    Args:
        initial_time: Starting time in whole seconds.
        increment:    Fischer increment added after each move, in whole seconds.
                      Ignored when mode is HOURGLASS.
    """
    initial_time: int
    increment: int = 0


@dataclass
class HistoryEntry:
    """A single event recorded in the clock's press history."""
    timestamp: datetime          # UTC
    event: str                   # "start" | "switch" | "pause" | "resume"
    player: Player
    white_remaining_ms: int
    black_remaining_ms: int


# ---------------------------------------------------------------------------
# Chess clock
# ---------------------------------------------------------------------------

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class ChessClock:
    """A two-player chess clock supporting Regular (Fischer) and Hourglass modes.

    Regular mode
    ------------
    Each player starts with ``initial_time`` seconds.  After a player presses
    the clock (``switch_turn``), their Fischer ``increment`` (in seconds) is
    added to their remaining time.

    Hourglass mode
    --------------
    The time a player spends on their move is transferred to the opponent's
    clock when the turn is switched.  ``increment`` is ignored.

    Internal state
    --------------
    All timers are stored as ``int`` milliseconds to avoid floating-point
    accumulation errors.  ``PlayerSettings.initial_time`` and
    ``PlayerSettings.increment`` are in whole seconds and are multiplied by
    1000 on construction.
    """

    def __init__(
        self,
        white: PlayerSettings,
        black: PlayerSettings,
        mode: ClockMode = ClockMode.REGULAR,
    ) -> None:
        self._settings = [white, black]
        self._mode = mode

        # Internal timers (milliseconds)
        self._remaining_ms: list[int] = [
            white.initial_time * 1000,
            black.initial_time * 1000,
        ]

        self._active: Player = Player.NONE
        self._paused_clock: bool = False
        self._turn_start: datetime = _EPOCH   # non-optional; irrelevant when not running
        self._running: bool = False
        self._history: list[HistoryEntry] = []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _elapsed_ms(self) -> int:
        """Milliseconds elapsed since the current turn started.  0 if not running."""
        if not self._running:
            return 0
        delta = self._now() - self._turn_start
        return int(delta.total_seconds() * 1000)

    def _snapshot_remaining_ms(self) -> tuple[int, int]:
        """Return (white_ms, black_ms) reflecting real-time elapsed.

        Does NOT mutate internal state.  The active player's value is reduced
        by the time elapsed in the current turn and clamped to zero.  In
        Hourglass mode the opponent's value is simultaneously increased by the
        same amount, capped at what the active player actually had.
        """
        white_ms, black_ms = self._remaining_ms[0], self._remaining_ms[1]
        elapsed = self._elapsed_ms()
        if self._active is Player.WHITE:
            if self._mode is ClockMode.HOURGLASS:
                transferred = min(elapsed, white_ms)
                white_ms = max(0, white_ms - elapsed)
                black_ms = black_ms + transferred
            else:
                white_ms = max(0, white_ms - elapsed)
        elif self._active is Player.BLACK:
            if self._mode is ClockMode.HOURGLASS:
                transferred = min(elapsed, black_ms)
                black_ms = max(0, black_ms - elapsed)
                white_ms = white_ms + transferred
            else:
                black_ms = max(0, black_ms - elapsed)
        return white_ms, black_ms

    def _commit_turn(self, elapsed_ms: int) -> None:
        """Apply end-of-turn bookkeeping to ``_remaining_ms``.

        Must be called *before* flipping ``_active``.
        """
        active = self._active
        opponent = Player.BLACK if active is Player.WHITE else Player.WHITE

        if self._mode is ClockMode.REGULAR:
            self._remaining_ms[active.value] -= elapsed_ms
            self._remaining_ms[active.value] += self._settings[active.value].increment * 1000
            self._remaining_ms[active.value] = max(0, self._remaining_ms[active.value])
        elif self._mode is ClockMode.HOURGLASS:
            transferred = min(elapsed_ms, self._remaining_ms[active.value])
            self._remaining_ms[active.value] -= elapsed_ms
            self._remaining_ms[active.value] = max(0, self._remaining_ms[active.value])
            self._remaining_ms[opponent.value] += transferred
        else:
            raise NotImplementedError(f"Clock mode {self._mode!r} is not implemented.")

    def _record(self, event: str, player: Player = Player.NONE) -> None:
        white_ms, black_ms = self._snapshot_remaining_ms()
        self._history.append(
            HistoryEntry(
                timestamp=self._now(),
                event=event,
                player=player,
                white_remaining_ms=white_ms,
                black_remaining_ms=black_ms,
            )
        )

    @staticmethod
    def _format_time(ms: int) -> str:
        """Format milliseconds as ``M:SS`` or ``H:MM:SS``.

        Negative values display as ``0:00``.
        """
        if ms <= 0:
            return "0:00"
        total_seconds = ms // 1000
        seconds = total_seconds % 60
        total_minutes = total_seconds // 60
        minutes = total_minutes % 60
        hours = total_minutes // 60
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the clock.  White moves first.

        Raises:
            RuntimeError: If the clock is already running.
        """
        if self._running:
            raise RuntimeError("Clock is already running.")
        self._active = Player.WHITE
        self._turn_start = self._now()
        self._running = True
        self._record("start", player=Player.WHITE)

    def switch_turn(self) -> None:
        """Press the clock to end the current player's move and start the opponent's.

        The Fischer increment (Regular mode) or time transfer (Hourglass mode)
        is applied at this point.

        Raises:
            RuntimeError: If the clock is not running or the active player has flagged.
        """
        if not self._running:
            raise RuntimeError("Clock is not running.")
        active = self._active
        if self._snapshot_remaining_ms()[active.value] <= 0:
            raise RuntimeError(
                f"{active.name.capitalize()} has flagged; cannot switch turns."
            )

        elapsed = self._elapsed_ms()
        self._commit_turn(elapsed)
        self._active = Player.BLACK if active is Player.WHITE else Player.WHITE
        self._turn_start = self._now()
        self._record("switch", player=self._active)

    def pause(self) -> None:
        """Pause the clock mid-turn.  No increment is awarded.

        Raises:
            RuntimeError: If the clock is not running.
        """
        if not self._running:
            raise RuntimeError("Clock is not running.")
        elapsed = self._elapsed_ms()
        active = self._active
        if self._mode is ClockMode.HOURGLASS:
            # Transfer elapsed time to the opponent, same as a turn switch.
            opponent = Player.BLACK if active is Player.WHITE else Player.WHITE
            transferred = min(elapsed, self._remaining_ms[active.value])
            self._remaining_ms[active.value] -= elapsed
            self._remaining_ms[active.value] = max(0, self._remaining_ms[active.value])
            self._remaining_ms[opponent.value] += transferred
        else:
            # Regular mode: deduct elapsed without awarding increment.
            self._remaining_ms[active.value] -= elapsed
            self._remaining_ms[active.value] = max(0, self._remaining_ms[active.value])
        self._paused_clock = True
        self._running = False
        self._record("pause", player=self._active)

    def resume(self) -> None:
        """Resume a paused game, continuing the same player's turn.

        Raises:
            RuntimeError: If the clock is already running or was never started.
        """
        if self._running:
            raise RuntimeError("Clock is already running.")
        if not self._paused_clock:
            raise RuntimeError("Clock has not been started or paused yet.")
        self._paused_clock = False
        self._turn_start = self._now()
        self._running = True
        self._record("resume", player=self._active)

    def time_string(self, player: Player) -> str:
        """Return a formatted time string for the given player.

        In Hourglass mode White's display is truncated to the nearest whole
        second and Black's display is derived from the total, so the two
        strings always represent times that add up to the original total.
        """
        w_ms, b_ms = self._snapshot_remaining_ms()
        if self._mode is ClockMode.HOURGLASS:
            w_display = (w_ms // 1000) * 1000
            b_display = (w_ms + b_ms) - w_display
            ms = w_display if player is Player.WHITE else b_display
        else:
            ms = w_ms if player is Player.WHITE else b_ms
        return self._format_time(ms)

    def current_player(self) -> Player:
        """Return the player whose turn it currently is.

        Returns ``Player.NONE`` if the clock has not been started.
        """
        return self._active

    def is_flagged(self, player: Player) -> bool:
        """Return True if ``player`` has run out of time.

        Raises:
            ValueError: If ``player`` is ``Player.NONE``.
        """
        if player is Player.NONE:
            raise ValueError("player must be Player.WHITE or Player.BLACK.")
        return self._snapshot_remaining_ms()[player.value] <= 0

    def flagged_string(self) -> str:
        """Return a human-readable string describing the flag status of both players."""
        white_flagged = self.is_flagged(Player.WHITE)
        black_flagged = self.is_flagged(Player.BLACK)
        if white_flagged and black_flagged:
            return "Both players are flagged!"
        if white_flagged:
            return "White is flagged!"
        if black_flagged:
            return "Black is flagged!"
        return "No flags."

    def history(self) -> list[HistoryEntry]:
        """Return a deep copy of the press history."""
        return copy.deepcopy(self._history)
