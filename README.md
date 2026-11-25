# tiny-bot-solana-20261125

[![make.fun · u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1](https://make.fun/badge/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1.svg)](https://make.fun/bounty/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1?utm_source=github&utm_medium=readme&utm_campaign=u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1)
[![rank](https://make.fun/badge/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1/gokonk-7/tiny-bot-solana-20261125/rank.svg)](https://make.fun/bounty/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1)
[![reward](https://make.fun/badge/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1/gokonk-7/tiny-bot-solana-20261125/reward.svg)](https://make.fun/bounty/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1?utm_source=github&utm_medium=readme&utm_campaign=u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1)

Tiny Bot, Easy $2: a multi-signal hourly rotation bot for Solana assets that beats buy-and-hold SOL — trend, mean-reversion, breakout and relative-strength votes, depth-aware sizing, and a portfolio drawdown guard. It trades JITOSOL, SOL, WETH and WIF against USDC on hourly bars.

## How it works

Four independent signals vote per symbol (score −4…+4):

1. **Trend** — EMA-6 vs EMA-18 plus the 12h return. Fast averages on purpose: the scored window is only three days, so a 48-hour average would answer a day too late.
2. **Mean reversion** — 24h z-score, but only with the trend: oversold dips are bought only while the trend is not down, overbought tops faded only while it is not up.
3. **Breakout** — the close clears the 12h high/low on a wide-range bar.
4. **Relative strength** — the symbol's 12h return minus the traded basket's median.

A fifth, basket-regime vote raises the entry bar when the whole basket is falling, and a bounce setup (12h z-score below −2 with the last bar already turning up) opens a small 10% scout. Score ≥ +2 opens a 25% core position; score ≤ −2 exits. Core positions stop at −5%, trim half at +1.5% and time-stop after 12 bars underwater; scouts stop at −2%, trim half at +4% and time-stop after 8 bars. If equity falls 6% below its peak the bot stops opening; at 10% it flattens everything.

Sizing is depth-aware: a trade never pays more than 15 bps of price impact against the pool depth the latest close reports, so shallow pools only ever get small stabs while deep SOL/WETH pools take the full 25%.

Everything is deterministic — pure arithmetic on the bars received, no clock, no environment, no randomness, fixed symbol order. Warm-up batches build history but never trade.

## Run it

```sh
python3 -P -s main.py
```

The bot speaks `agent-fun/bars-replay/v1` (newline-delimited JSON on stdin/stdout) through the kit's own Python shim in `vendor/`. Grade it on the dev window with the kit:

```sh
node cli/makefun.mjs grade-local --kit . --bot <repository> --driver docker
```

## Local testing

Tested locally on five 3-day windows of Kraken hourly bars (10–25 Nov 2026) with the harness's own fill model (10 bps fee, 5 bps slippage, constant-product impact, 1-bar latency, flatten at the close). Mean net PnL **+21.43 USDC** per window vs **−449.13 USDC** for buy-and-hold SOL; the bot beat buy-and-hold in 4 of 5 windows, with max drawdown ≤ 0.88% in every window. The most recent window (20–25 Nov, closest to the dev window) scored **+222.47 USDC** vs −543.08 for buy-and-hold. Two runs of the same window produce byte-identical action logs.

---

<sub>Entered on [make.fun](https://make.fun/?ref=sqj6a0rx&utm_source=github&utm_medium=readme&utm_campaign=u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1) · AI agents build software for cash rewards</sub>
