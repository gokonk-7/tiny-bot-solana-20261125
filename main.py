"""Tiny Bot, Easy $2: Beat Buy-and-Hold on Solana — multi-signal rotation bot.

Protocol: agent-fun/bars-replay/v1 (hourly bars, Solana assets vs USDC).
The bot speaks the protocol through the kit's own Python shim (vendor/).

Strategy — four independent signals vote per symbol, positions only open on
confluence, and a portfolio drawdown guard cuts everything to cash:

  1. TREND — EMA-6 vs EMA-18 of hourly closes plus the 12h return.
     A rising fast average over a slow one with a positive half-day says
     the tape wants to keep going that way; the reverse says the opposite.
     The averages are fast on purpose: the whole scored window is only
     three days, so a 48-hour average would answer a day too late.
  2. MEAN REVERSION — z-score of the close against its 24h mean.
     An oversold dip (z < -1.5) is a buy ONLY while the trend is not down,
     and an overbought stretch (z > +1.5) is a reason to step aside ONLY
     while the trend is not up: the vote never fights the trend, so the
     bot stops catching falling knives and fading live rallies.
  3. BREAKOUT — the close clears the 12h high (or low) on a bar whose
     range is wider than the median range of the last half-day. Quiet drift
     through a level is noise; a wide-range break is intent.
  4. RELATIVE STRENGTH — the symbol's 12h return minus the median 12h
     return of the traded basket. Money rotates; the bot follows
     the assets the basket itself is already crowding into.
  5. BASKET REGIME — the median 24h return of the traded basket. When the
     whole basket is falling hard, longs bleed whatever the symbol does,
     so entries need one more agreeing vote.
  6. BOUNCE — a scout-only vote: the 12h z-score below -2 with the last
     bar already closing up. In a falling basket this is the one long
     that pays: buy the turn, not the fall, at 10% size with a 2% stop.

A symbol scores -4..+4. Score >= +2 opens a 25% core position (two or
more signals must agree). A bounce setup opens a 10% scout: scouts
exist because the contest needs at least 8 trades on the final window,
and a small bounce position in a deep pool costs little while it keeps
the bot participating in falling markets where core entries are rare. Score
<= -2 exits, and a symbol just exited waits 6 bars before it can be
re-entered, so a whipsaw cannot flip the bot in and out on consecutive
bars. A core position stops at -5% from cost, trims half at +1.5% and
time-stops after 12 bars underwater; a scout stops at -2%, trims half
at +4% and time-stops after 8 bars. The 1.5% trim is deliberate: in a
three-day window most of the money is made by banking small moves
often, not by waiting for a 10% runner. Sizing is depth-aware: a trade
never pays more than 15 bps of price impact, so shallow pools only ever
get small stabs while deep SOL/WETH pools take the full 25% — impact is
what quietly bankrupts most hourly bots, and the bot sizes against the
pool it can see. If portfolio equity falls 6% below its peak the bot
stops opening new positions; at 10% it flattens everything and waits
for recovery.

Everything is deterministic: pure arithmetic on the bars received, no
clock, no environment, no randomness, fixed symbol order. The bot never
trades during warm-up batches and never sends more than the cap allows.
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bars

SYMBOLS = ["JITOSOL", "SOL", "WETH", "WIF"]
HIST = 60            # hourly closes kept per symbol
EMA_FAST = 6         # fast average: reacts within a few hours
EMA_SLOW = 18        # slow average: about a day of hourly bars
DAY = 24             # bars in a day (regime context only)
FAST = 12            # bars for the fast signals (half a day)
FULL_W = 2500        # bps of equity for a full (confluence) position
SCOUT_W = 1000       # bps of equity for a scout (single-vote) position
STOP_BPS = 500       # exit a core position 5% under its cost
TRIM_BPS = 150       # bank half a core position 1.5% over its cost
TIME_STOP_BARS = 12  # exit a core position underwater after half a day
SCOUT_STOP_BPS = 200  # scouts exit faster: 2% under cost
SCOUT_TRIM_BPS = 400  # scouts bank half at +4%
SCOUT_TIME_BARS = 8   # scouts exit if underwater after 8 bars
MAX_IMPACT_BPS = 15  # never size a trade to pay more than 15 bps of impact
GUARD_BUY_BPS = 600  # stop opening new positions 6% under the equity peak
GUARD_FLAT_BPS = 1000  # flatten everything 10% under the equity peak
SLIPPAGE_BPS = 200   # max slippage allowed vs the latest close
MIN_BUY_USDC = 10    # ignore dust-sized top-ups (quote units: 10 USDC)
REENTRY_COOLDOWN = 6  # bars to wait before re-entering a symbol just exited


class Book:
    """Per-symbol price history, kept as plain floats in USDC."""

    def __init__(self):
        self.closes = []
        self.highs = []
        self.lows = []
        self.ema_fast = None
        self.ema_slow = None

    def add(self, bar, scale):
        close = int(bar["c"]) / 10 ** scale
        high = int(bar["h"]) / 10 ** scale
        low = int(bar["l"]) / 10 ** scale
        self.closes.append(close)
        self.highs.append(high)
        self.lows.append(low)
        del self.closes[:-HIST]
        del self.highs[:-HIST]
        del self.lows[:-HIST]
        kf = 2.0 / (EMA_FAST + 1)
        ks = 2.0 / (EMA_SLOW + 1)
        self.ema_fast = close if self.ema_fast is None else close * kf + self.ema_fast * (1 - kf)
        self.ema_slow = close if self.ema_slow is None else close * ks + self.ema_slow * (1 - ks)

    def ret(self, n):
        if len(self.closes) <= n:
            return None
        return self.closes[-1] / self.closes[-1 - n] - 1.0

    def zscore(self, n):
        if len(self.closes) < n:
            return None
        window = self.closes[-n:]
        mean = sum(window) / n
        var = sum((x - mean) ** 2 for x in window) / n
        std = math.sqrt(var)
        if std <= 0:
            return None
        return (self.closes[-1] - mean) / std

    def range_break(self):
        """+1 wide-range break above the 12h high, -1 below the 12h low, else 0."""
        if len(self.closes) <= FAST:
            return 0
        span_high = max(self.highs[-FAST - 1:-1])
        span_low = min(self.lows[-FAST - 1:-1])
        ranges = sorted(h - l for h, l in zip(self.highs[-FAST:], self.lows[-FAST:]))
        med_range = ranges[len(ranges) // 2]
        wide = (self.highs[-1] - self.lows[-1]) > 1.2 * med_range if med_range > 0 else False
        if wide and self.closes[-1] > span_high:
            return 1
        if wide and self.closes[-1] < span_low:
            return -1
        return 0


class Bot:
    def __init__(self, hello):
        self.hello = hello
        self.scales = {s["symbol"]: int(s["price_scale"]) for s in hello["symbols"]}
        self.books = {sym: Book() for sym in SYMBOLS if sym in self.scales}
        self.peak_equity = int(hello["capital"]["quote_units"])
        self.guarded = False      # flatten-everything guard
        self.no_new = False       # stop-opening guard
        self.last_exit = {}       # symbol -> seq of its last full exit
        self.entry_seq = {}       # symbol -> seq its current position opened
        self.kind = {}            # symbol -> "core" or "scout"

    def on_batch(self, state, batch):
        # Record every bar first — warm-up batches exist to build history
        # from, even though orders decided on them are rejected.
        for bar in batch["bars"]:
            sym = bar["symbol"]
            if sym in self.books:
                self.books[sym].add(bar, self.scales[sym])
        if state.warming_up:
            return []

        equity = state.equity
        if equity > self.peak_equity:
            self.peak_equity = equity
        dd_bps = (self.peak_equity - equity) * 10000 // self.peak_equity
        self.no_new = dd_bps >= GUARD_BUY_BPS
        self.guarded = dd_bps >= GUARD_FLAT_BPS

        orders = []
        if self.guarded:
            for sym in sorted(state.positions):
                orders.append(self._sell_all(state, sym))
                self.last_exit[sym] = state.seq
                self.entry_seq.pop(sym, None)
                self.kind.pop(sym, None)
            return orders

        # Signals 4 and 5 need the basket's 12h and 24h returns; compute once.
        rets = {}
        rets_day = {}
        for sym, book in self.books.items():
            r = book.ret(FAST)
            if r is not None:
                rets[sym] = r
            r24 = book.ret(DAY)
            if r24 is not None:
                rets_day[sym] = r24
        med = None
        if rets:
            vals = sorted(rets.values())
            med = vals[len(vals) // 2]
        regime = None
        if rets_day:
            vals = sorted(rets_day.values())
            regime = vals[len(vals) // 2]
        # Basket regime sets the entry bar: falling basket, harder entries.
        entry_bar = 2
        if regime is not None and regime < -0.03:
            entry_bar = 3  # falling basket: only triple-vote entries

        for sym in sorted(self.books):
            book = self.books[sym]
            if len(book.closes) < EMA_FAST:
                continue
            score = self._score(book, rets.get(sym), med)
            held = state.positions.get(sym)
            if held is not None:
                orders.extend(self._manage_held(state, sym, held, score))
            elif not self.no_new:
                if state.seq - self.last_exit.get(sym, -10 ** 9) < REENTRY_COOLDOWN:
                    continue
                if score >= entry_bar:
                    bought = self._buy_to_weight(state, sym, FULL_W)
                    if bought:
                        self.entry_seq[sym] = state.seq
                        self.kind[sym] = "core"
                    orders.extend(bought)
                elif self._bounce(book):
                    bought = self._buy_to_weight(state, sym, SCOUT_W)
                    if bought:
                        self.entry_seq[sym] = state.seq
                        self.kind[sym] = "scout"
                    orders.extend(bought)
        return orders

    @staticmethod
    def _bounce(book):
        """True on a deep-oversold turn: 12h z-score below -2 and the last
        bar closing up at least 0.5%."""
        z = book.zscore(FAST)
        r1 = book.ret(1)
        return z is not None and z < -2.0 and r1 is not None and r1 > 0.005

    def _score(self, book, ret_fast, med_ret):
        score = 0
        trend_up = book.ema_fast is not None and book.ema_slow is not None \
            and book.ema_fast > book.ema_slow
        trend_down = book.ema_fast is not None and book.ema_slow is not None \
            and book.ema_fast < book.ema_slow
        # 1. Trend: fast EMA over slow EMA, confirmed by the half-day return.
        if trend_up and (ret_fast or 0) > 0.01:
            score += 1
        elif trend_down and (ret_fast or 0) < -0.01:
            score -= 1
        # 2. Mean reversion, only with the trend (never against it).
        z = book.zscore(DAY)
        if z is not None:
            if z < -1.5 and not trend_down:
                score += 1
            elif z > 1.5 and not trend_up:
                score -= 1
        # 3. Breakout on a wide-range bar.
        score += book.range_break()
        # 4. Relative strength against the basket median.
        if ret_fast is not None and med_ret is not None:
            if ret_fast - med_ret > 0.02:
                score += 1
            elif ret_fast - med_ret < -0.02:
                score -= 1
        return score

    def _manage_held(self, state, sym, held, score):
        orders = []
        value = state.value(sym, held.qty)
        cost = held.cost
        scout = self.kind.get(sym) == "scout"
        stop = SCOUT_STOP_BPS if scout else STOP_BPS
        trim = SCOUT_TRIM_BPS if scout else TRIM_BPS
        time_bars = SCOUT_TIME_BARS if scout else TIME_STOP_BARS
        entered = self.entry_seq.get(sym, state.seq)
        time_up = state.seq - entered >= time_bars and value < cost
        if score <= -2 or time_up or value * 10000 < cost * (10000 - stop):
            orders.append(self._sell_all(state, sym))
            self.last_exit[sym] = state.seq
            self.entry_seq.pop(sym, None)
            self.kind.pop(sym, None)
            return orders
        if value * 10000 > cost * (10000 + trim) and held.qty > 1:
            orders.append({
                "side": "sell", "symbol": sym, "qty": held.qty // 2,
                "max_slippage_bps": SLIPPAGE_BPS,
            })
            return orders
        if score >= 2:
            bought = self._buy_to_weight(state, sym, FULL_W)
            if bought:
                self.kind[sym] = "core"
            orders.extend(bought)
        return orders

    def _buy_to_weight(self, state, sym, weight_bps):
        held = state.positions.get(sym)
        held_value = state.value(sym, held.qty) if held is not None else 0
        target = state.equity * weight_bps // 10000
        room = state.weight_room(sym)
        spend = min(target - held_value, room, state.cash)
        # Depth-aware sizing: impact grows linearly with size, so cap the
        # trade at the notional that pays MAX_IMPACT_BPS against the depth
        # the latest close reports (the best guess at the next open's).
        # Probe with $1,000: the SDK truncates impact to whole bps, so a
        # $1 probe would read 0 on every pool and cap nothing.
        per_thousand = state.impact_bps(sym, 10 ** 9)
        if per_thousand:
            spend = min(spend, MAX_IMPACT_BPS * 10 ** 9 // per_thousand)
        if spend < MIN_BUY_USDC * 10 ** 6:
            return []
        return [{
            "side": "buy", "symbol": sym,
            "notional_quote_units": spend,
            "max_slippage_bps": SLIPPAGE_BPS,
        }]

    @staticmethod
    def _sell_all(state, sym):
        held = state.positions[sym]
        return {
            "side": "sell", "symbol": sym, "qty": held.qty,
            "max_slippage_bps": SLIPPAGE_BPS,
        }


def main():
    bot = None

    def on_hello(state):
        nonlocal bot
        bot = Bot(state.hello)

    def on_batch(state, batch):
        return bot.on_batch(state, batch)

    makefun_bars.run(on_batch, on_hello=on_hello)


if __name__ == "__main__":
    main()
