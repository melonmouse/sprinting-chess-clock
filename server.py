#!/usr/bin/env python3
"""Chess clock web server.

URL parameters (all optional):
    white    White's initial time in seconds  (default: 300)
    black    Black's initial time in seconds  (default: 300)
    inc_w    White's increment in seconds     (default: 0)
    inc_b    Black's increment in seconds     (default: 0)
    mode     'regular' or 'hourglass'         (default: regular)

Example:
    http://localhost:5000/?white=600&black=600&inc_w=5&inc_b=5

Keys:
    Enter   White presses clock (ends White's turn if running; if paused and it's
            White's turn, unpauses and hands clock to Black; if paused and it's
            Black's turn, unpauses and resumes Black's turn)
    Space   Black presses clock (mirror of Enter for Black)
    P       Pause / Resume
    R       Reset
"""

import argparse
import pathlib
import threading
from datetime import timezone

from flask import Flask, jsonify, request, render_template_string, send_file

from schaakklok import ChessClock, ClockMode, Player, PlayerSettings

app = Flask(__name__)
_lock = threading.Lock()

_config: dict = {
    "white": 300, "black": 300, "inc_w": 0, "inc_b": 0, "mode": "regular"
}
_clock: ChessClock  # initialised before app.run() and on every GET /


# ---------------------------------------------------------------------------
# Game logger
# ---------------------------------------------------------------------------

class _GameLogger:
    """Writes one log file per game to a ``logs/`` directory.

    A new file is opened by ``start()`` and subsequent calls to ``flush()``
    append any history entries that have not been written yet.  The file is
    flushed eagerly so a crash does not lose data.
    """

    _LOG_DIR = pathlib.Path(__file__).parent / "logs"

    def __init__(self) -> None:
        self._file = None
        self._written = 0      # number of HistoryEntry rows already written
        self._flagged: set[str] = set()  # players already logged as flagged

    def start(self, cfg: dict) -> None:
        """Open a new log file for a game described by *cfg*."""
        self._LOG_DIR.mkdir(exist_ok=True)
        from datetime import datetime
        now = datetime.now(timezone.utc)
        fname = now.strftime("%Y-%m-%d-%H%M%SZ") + ".txt"
        if self._file is not None:
            self._file.close()
        self._file = open(self._LOG_DIR / fname, "w", encoding="utf-8")
        self._written = 0
        self._flagged = set()
        mode_label = cfg["mode"].upper()
        self._file.write(
            f"# Chess clock game — {now.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"# Mode: {mode_label}  "
            f"White: {cfg['white']}s+{cfg['inc_w']}  "
            f"Black: {cfg['black']}s+{cfg['inc_b']}\n"
            f"# {'TIMESTAMP (UTC)':<26}  {'EVENT':<10}  {'PLAYER':<6}  "
            f"WHITE_REMAINING  BLACK_REMAINING\n"
        )
        self._file.flush()

    def flush(self, clock: "ChessClock") -> None:
        """Append any new history entries and flag events from *clock*."""
        if self._file is None:
            return
        history = clock.history()
        for entry in history[self._written:]:
            ts = entry.timestamp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            w = _fmt_ms(entry.white_remaining_ms)
            b = _fmt_ms(entry.black_remaining_ms)
            self._file.write(
                f"{ts:<28}  {entry.event:<10}  {entry.player.name:<6}  {w:>15}  {b:>15}\n"
            )
        self._written = len(history)

        # Flag events are not in history; detect them here.
        w_ms, b_ms = clock._snapshot_remaining_ms()
        from datetime import datetime
        now = datetime.now(timezone.utc)
        for player, ms in (("WHITE", w_ms), ("BLACK", b_ms)):
            if ms <= 0 and player not in self._flagged:
                self._flagged.add(player)
                ts = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                w = _fmt_ms(max(0, w_ms))
                b = _fmt_ms(max(0, b_ms))
                self._file.write(
                    f"{ts:<28}  {'flag':<10}  {player:<6}  {w:>15}  {b:>15}\n"
                )

        self._file.flush()


def _fmt_ms(ms: int) -> str:
    """Format milliseconds as M:SS.mmm for log files."""
    ms = max(0, ms)
    total_s, millis = divmod(ms, 1000)
    minutes, secs = divmod(total_s, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}.{millis:03d}"
    return f"{minutes}:{secs:02d}.{millis:03d}"


_logger = _GameLogger()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_clock(cfg: dict) -> ChessClock:
    """Construct a ChessClock from *cfg*, starting paused with White active."""
    white = PlayerSettings(cfg["white"], cfg["inc_w"])
    black = PlayerSettings(cfg["black"], cfg["inc_b"])
    mode = ClockMode.HOURGLASS if cfg["mode"] == "hourglass" else ClockMode.REGULAR
    c = ChessClock(white, black, mode)
    c.start()   # sets White active, begins ticking
    c.pause()   # freeze immediately — elapsed is ~0 ms
    return c


def _parse_int(value, default: int, lo: int) -> int:
    try:
        return max(lo, int(value))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/font/DSEG7Classic-Bold.woff2")
def font():
    path = pathlib.Path(__file__).parent / "DSEG7Classic-Bold.woff2"
    return send_file(path, mimetype="font/woff2")


@app.route("/")
def index() -> str:
    global _clock, _config

    white = _parse_int(request.args.get("white"), _config["white"], lo=1)
    black = _parse_int(request.args.get("black"), _config["black"], lo=1)
    inc_w = _parse_int(request.args.get("inc_w"), _config["inc_w"], lo=0)
    inc_b = _parse_int(request.args.get("inc_b"), _config["inc_b"], lo=0)
    mode  = request.args.get("mode", _config["mode"])
    if mode not in ("regular", "hourglass"):
        mode = "regular"

    cfg = {"white": white, "black": black, "inc_w": inc_w, "inc_b": inc_b, "mode": mode}
    with _lock:
        _config = cfg
        _clock = _make_clock(cfg)
        _logger.start(cfg)
        _logger.flush(_clock)
        white_str = _clock.time_string(Player.WHITE)
        black_str = _clock.time_string(Player.BLACK)

    return render_template_string(
        _HTML,
        white_str=white_str,
        black_str=black_str,
        inc_w=inc_w,
        inc_b=inc_b,
    )


@app.route("/state")
def state():
    with _lock:
        w_ms, b_ms = _clock._snapshot_remaining_ms()
        _logger.flush(_clock)
        return jsonify({
            "white_str":     _clock.time_string(Player.WHITE),
            "black_str":     _clock.time_string(Player.BLACK),
            "white_ms":      max(0, w_ms),
            "black_ms":      max(0, b_ms),
            "white_flagged": w_ms <= 0,
            "black_flagged": b_ms <= 0,
            "active":        _clock.current_player().name,
            "running":       _clock._running,
            "paused":        _clock._paused_clock,
        })


@app.route("/press", methods=["POST"])
def press():
    player = request.args.get("player", "")
    with _lock:
        active = _clock.current_player()
        is_your_turn  = (player == active.name)
        is_their_turn = (player in ("WHITE", "BLACK") and not is_your_turn
                         and active is not Player.NONE)
        try:
            if _clock._running:
                if is_your_turn:
                    _clock.switch_turn()
                # else: not your turn — no-op
            elif _clock._paused_clock:
                _clock.resume()
                if is_your_turn:
                    # Active player pressed their button → end their turn on unpause,
                    # consistent with non-paused behaviour where pressing your button
                    # always hands the clock to the opponent.
                    _clock.switch_turn()
        except RuntimeError:
            pass  # flagged or invalid state; ignore
        _logger.flush(_clock)
    return ("", 204)


@app.route("/pause", methods=["POST"])
def pause_toggle():
    with _lock:
        try:
            if _clock._running:
                _clock.pause()
            elif _clock._paused_clock:
                _clock.resume()
        except RuntimeError:
            pass
        _logger.flush(_clock)
    return ("", 204)


@app.route("/reset", methods=["POST"])
def reset():
    global _clock
    with _lock:
        _clock = _make_clock(_config)
        _logger.start(_config)
        _logger.flush(_clock)
    return ("", 204)


# ---------------------------------------------------------------------------
# Embedded HTML / CSS / JS
# ---------------------------------------------------------------------------

_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Chess Clock</title>
  <style>
    @font-face {
      font-family: 'DSEG7';
      src: url('/font/DSEG7Classic-Bold.woff2') format('woff2');
      font-weight: bold;
      font-style: normal;
    }

    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      height: 100dvh;
      display: flex;
      background: #000;
      font-family: 'Courier New', monospace;
      overflow: hidden;
      user-select: none;
      cursor: none;
    }

    .panel {
      flex: 1;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 0.5rem;
      background: #00001a;
      transition: background 0.15s;
      overflow: hidden;
    }
    #white-panel { align-items: flex-start; }
    #black-panel { align-items: flex-end; }

    .panel.active  { background: #000; }
    .panel.paused  { background: #000; }
    .panel.flagged { background: #1a0000 !important; }

    /* calc(50vw/3): exact max for M:SS (4-char) on 16:9 with 2ch center gap */
    .time-display {
      font-family: 'DSEG7', 'Courier New', monospace;
      font-size: min(calc(50vw / 3), 100dvh);
      font-weight: bold;
      line-height: 1;
      letter-spacing: 0;
      color: #fff;
      white-space: nowrap;
      padding: 0 1ch;
    }
    #white-panel .time-display { padding-left:  0; }
    #black-panel .time-display { padding-right: 0; }
    .panel.active  .time-display {
      text-decoration: underline;
      text-decoration-thickness: 0.18em;
      text-underline-offset: 0.25em;
    }
    .panel.flagged .time-display { color: #ff6060; }

    .inc-label {
      font-size: 0.8rem;
      color: rgba(255, 255, 255, 0.25);
      letter-spacing: 0.12em;
    }

    #bottom-label {
      position: fixed;
      bottom: 3vh;
      left: 0;
      right: 0;
      text-align: center;
      font-weight: bold;
      letter-spacing: 0.2em;
      pointer-events: none;
      opacity: 0;
      transition: opacity 0.2s;
    }
    #bottom-label.show { opacity: 1; }
    #bottom-label.flag   { font-size: min(8vw, 50vh); color: #ff4444; }
    #bottom-label.paused { font-size: min(4vw, 25vh); color: rgba(255,255,255,0.45); }
  </style>
</head>
<body>

  <div class="panel paused" id="white-panel">
    <div class="time-display" id="white-time">{{ white_str }}</div>
    {% if inc_w %}<div class="inc-label">+{{ inc_w }}s / move</div>{% endif %}
  </div>

  <div class="panel paused" id="black-panel">
    <div class="time-display" id="black-time">{{ black_str }}</div>
    {% if inc_b %}<div class="inc-label">+{{ inc_b }}s / move</div>{% endif %}
  </div>

  <div id="bottom-label"></div>

<script>
  const whitePanel = document.getElementById('white-panel');
  const blackPanel = document.getElementById('black-panel');
  const whiteTime  = document.getElementById('white-time');
  const blackTime  = document.getElementById('black-time');

  let lastState = null;

  // ---- Web Audio sound effects ----
  const _ac = new (window.AudioContext || window.webkitAudioContext)();

  function _beep(freq, duration, gain = 1.0, type = 'sine') {
    const osc = _ac.createOscillator();
    const env = _ac.createGain();
    osc.connect(env);
    env.connect(_ac.destination);
    osc.type = type;
    osc.frequency.value = freq;
    env.gain.setValueAtTime(gain, _ac.currentTime);
    env.gain.exponentialRampToValueAtTime(0.001, _ac.currentTime + duration);
    osc.start(_ac.currentTime);
    osc.stop(_ac.currentTime + duration);
  }

  function soundTurnChange() {
    // Short crisp double-click
    _beep(880, 0.06, 1.0, 'square');
    setTimeout(() => _beep(1100, 0.07, 1.0, 'square'), 70);
  }

  function soundCountdown(urgent = false) {
    if (urgent) {
      // High sharp double-tick for last 3 seconds
      _beep(1320, 0.06, 1.0, 'square');
      setTimeout(() => _beep(1760, 0.05, 1.0, 'square'), 80);
    } else {
      // Soft tick
      _beep(660, 0.05, 1.0, 'sine');
    }
  }

  function soundFlag() {
    // Descending buzz
    _beep(440, 0.18, 1.0, 'sawtooth');
    setTimeout(() => _beep(220, 0.35, 1.0, 'sawtooth'), 200);
  }

  // Ensure AudioContext is resumed on first user gesture
  document.addEventListener('keydown', () => {
    if (_ac.state === 'suspended') _ac.resume();
  }, { once: true });
  document.addEventListener('click', () => {
    if (_ac.state === 'suspended') _ac.resume();
  }, { once: true });

  // ---- State tracking for sound triggers ----
  let _prevActive  = null;  // 'WHITE' | 'BLACK' | 'NONE'
  let _prevRunning = false;
  let _prevSecsW   = null;  // floor(ms/1000) of active player last tick
  let _prevSecsB   = null;
  let _wFlagged    = false;
  let _bFlagged    = false;
  let _suppressNextTurnSound = false;  // set when sound already played eagerly on keydown

  function applyState(s) {
    // -- Sound: flag (time runs out) --
    if (s.white_flagged && !_wFlagged) soundFlag();
    if (s.black_flagged && !_bFlagged) soundFlag();
    _wFlagged = s.white_flagged;
    _bFlagged = s.black_flagged;

    // -- Sound: turn change (also fires on unpause) --
    if (s.running && lastState !== null && s.active !== 'NONE' &&
        (s.active !== _prevActive || !_prevRunning)) {
      if (_suppressNextTurnSound) {
        _suppressNextTurnSound = false;
      } else {
        soundTurnChange();
      }
    }

    // -- Sound: countdown beep (last 10 s, once per second) --
    if (s.running && s.active !== 'NONE') {
      // Derive seconds from the display string to guarantee alignment with
      // what is shown (avoids off-by-one from dual _snapshot_remaining_ms calls).
      const activeStr = s.active === 'WHITE' ? s.white_str : s.black_str;
      const parts = activeStr.split(':');
      const secsLeft = parts.length === 2
        ? parseInt(parts[0]) * 60 + parseInt(parts[1])
        : parseInt(parts[0]) * 3600 + parseInt(parts[1]) * 60 + parseInt(parts[2]);
      const prevSecs = s.active === 'WHITE' ? _prevSecsW : _prevSecsB;
      if (secsLeft <= 10 && secsLeft > 0 && secsLeft !== prevSecs) {
        soundCountdown(secsLeft <= 3);
      }
      if (s.active === 'WHITE') _prevSecsW = secsLeft; else _prevSecsB = secsLeft;
    }

    _prevActive  = s.active;
    _prevRunning = s.running;
    lastState = s;

    whiteTime.textContent = s.white_str;
    blackTime.textContent = s.black_str;

    const wActive = s.running && s.active === 'WHITE';
    const bActive = s.running && s.active === 'BLACK';
    const wPaused = !s.running && s.active === 'WHITE';
    const bPaused = !s.running && s.active === 'BLACK';

    whitePanel.classList.toggle('active',  wActive);
    whitePanel.classList.toggle('paused',  wPaused);
    whitePanel.classList.toggle('flagged', s.white_flagged);
    blackPanel.classList.toggle('active',  bActive);
    blackPanel.classList.toggle('paused',  bPaused);
    blackPanel.classList.toggle('flagged', s.black_flagged);

    const lbl = document.getElementById('bottom-label');
    const anyFlagged = s.white_flagged || s.black_flagged;
    const isPaused   = !s.running && s.active !== 'NONE';
    lbl.classList.toggle('flag',   anyFlagged);
    lbl.classList.toggle('paused', !anyFlagged && isPaused);
    lbl.classList.toggle('show',   anyFlagged || isPaused);
    lbl.textContent = anyFlagged ? 'FLAG' : 'PAUSED';
  }

  async function poll() {
    try {
      const r = await fetch('/state');
      applyState(await r.json());
    } catch (_) {}
  }

  setInterval(poll, 100);
  poll();

  async function post(url) {
    try { await fetch(url, { method: 'POST' }); } catch (_) {}
  }

  document.addEventListener('keydown', async e => {
    if (!['Enter', ' ', 'p', 'P', 'r', 'R'].includes(e.key)) return;
    e.preventDefault();

    // Play turn-change sound immediately — before the HTTP round-trip — to
    // eliminate the 100–300 ms delay that would otherwise occur waiting for
    // the POST + poll() to complete.  applyState() will swallow the echo.
    if (lastState && lastState.running && lastState.active !== 'NONE') {
      if ((e.key === 'Enter' && lastState.active === 'WHITE') ||
          (e.key === ' '     && lastState.active === 'BLACK')) {
        if (_ac.state === 'suspended') _ac.resume();
        soundTurnChange();
        _suppressNextTurnSound = true;
      }
    }

    if      (e.key === 'Enter')              { await post('/press?player=WHITE'); }
    else if (e.key === ' ')                  { await post('/press?player=BLACK'); }
    else if (e.key === 'p' || e.key === 'P') { await post('/pause'); }
    else if (e.key === 'r' || e.key === 'R') { await post('/reset'); }
    await poll();
  });
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Chess clock web server.")
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="Bind address (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, default=5000,
        help="Bind port (default: 5000)"
    )
    args = parser.parse_args()

    with _lock:
        _clock = _make_clock(_config)

    print(f"Chess clock at  http://{args.host}:{args.port}/")
    print("URL params:     ?white=300&black=300&inc_w=0&inc_b=0&mode=regular")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)
