# Suggested Test Sequence

This sequence prioritizes your constraints: short holding periods, commodities first, high potential MAR, and low correlation to equity exposure. Treat it as a queue, not a verdict.

## Phase 1: Highest Priority Short-Term Commodity Tests

Start here on `/CL`, `/GC`, `/SI`, `/NG`, `/RB`, and `/HO`.

1. `0064_turtle_soup_false_breakout.afl` on daily bars.
2. `0062_ibs_mean_reversion.afl` on daily bars.
3. `0037_rsi2_commodity_snapback.afl` on daily bars.
4. `0061_bollinger_rsi_mean_reversion.afl` on daily bars.
5. `0034_nr4_inside_day_breakout.afl` on daily bars.
6. `0056_failed_breakdown_reclaim.afl` on daily bars.
7. `0047_bollinger_squeeze_pop.afl` on daily and 60-minute bars.
8. `0032_short_term_commodity_breakout.afl` on daily and 60-minute bars.
9. `0035_atr_thrust_followthrough.afl` on daily bars.
10. `0044_short_adx_burst.afl` on daily and 60-minute bars.

## Phase 2: Intraday Commodity Tests

Use clean intraday session settings. Start with `/CL`, `/GC`, `/SI`, and `/NG`.

1. `0060_opening_range_failure_reversal.afl` on 5-minute and 60-minute bars.
2. `0066_opening_range_pullback_continuation.afl` on 5-minute and 60-minute bars.
3. `0042_intraday_opening_range_breakout.afl` on 5-minute and 60-minute bars.
4. `0065_intraday_vwap_reversion.afl` on 5-minute bars.
5. `0059_opening_drive_gap_and_go.afl` on 5-minute bars.
6. `0043_prior_day_pivot_breakout.afl` on 5-minute and 60-minute bars.

## Phase 3: Pattern Reversal Tests

These are more candle-pattern-like, so require extra skepticism and realistic costs.

1. `0052_pinocchio_bar_long_reversal.afl` on daily and 60-minute bars.
2. `0054_hammer_shooting_star_reversal.afl` on daily bars.
3. `0055_outside_bar_failure.afl` on daily bars.
4. `0053_engulfing_reversal_short_hold.afl` on daily bars.
5. `0057_three_bar_morning_evening_reversal.afl` on daily bars.
6. `0058_exhaustion_gap_fade.afl` on daily bars.

## Phase 4: Lower-Correlation Complements

Run these after you have candidates from phases 1-3. Keep only those with low equity-curve correlation to the shortlist.

1. `0050_commodity_vs_spy_rotation.afl` on daily bars.
2. `0030_relative_strength_trend.afl` on daily bars.
3. `0025_volatility_adjusted_momentum.afl` on daily bars.
4. `0013_volatility_contraction_breakout.afl` on daily bars.
5. `0022_atr_expansion_momentum.afl` on daily bars.
6. `0001_2RSI.afl` on `SPY` and `/ES` daily bars as a mean-reversion benchmark.

## Keep / Reject Rules

- Keep only strategies with enough trades to judge.
- Prefer MAR over raw CAGR when two strategies are similar.
- Penalize results that depend on one symbol or one short date window.
- Penalize high turnover unless slippage and commissions are explicitly modeled.
- Drop candidates whose equity curves correlate above about `0.60` with a better strategy already selected.
