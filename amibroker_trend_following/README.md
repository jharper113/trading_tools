# AmiBroker Trend-Following AFL Scripts

This folder contains standalone AFL formulas for backtesting trend-following and complementary mean-reversion ideas in AmiBroker.

Strategy `0001` is a simple RSI(2) mean-reversion baseline. Strategies `0002`-`0011` cover common trend-following baselines. Strategies `0012`-`0031` are designed as higher-MAR candidates: they try to reduce drawdown or overlap by changing entry timing, exit mechanics, volatility filters, timeframes, or signal families. Strategies `0032`-`0051` focus on short-term commodity trades with default max-hold settings of five bars or less. None of that guarantees a high MAR; use the backtests to rank them by CAGR / max drawdown and compare their equity-curve correlations.

## How To Use

1. Import your normalized market data into AmiBroker.
2. Open AmiBroker Analysis.
3. Load one `.afl` file at a time.
4. Set the symbol universe/watchlist you want to test.
5. Run Backtest or Optimize.

Each script is long/short by default and uses configurable parameters through AmiBroker's `Param()` controls. Position sizing defaults to a percentage of equity. For futures, confirm your AmiBroker database has the correct symbol point value, margin, and tick settings.

See `STRATEGY_GUIDE.md` for suggested symbols and timeframes for every strategy.

## Scripts

- `0001_2RSI.afl`: Two-period RSI mean reversion with a long-term trend filter.
- `0002_moving_average_crossover.afl`: Fast/slow moving average trend crossover.
- `0003_donchian_breakout.afl`: Classic channel breakout with channel exit.
- `0004_turtle_trading.afl`: Turtle-style breakout with ATR stop and shorter exit.
- `0005_macd_trend_following.afl`: MACD trend regime with optional long-term filter.
- `0006_adx_breakout.afl`: Donchian breakout filtered by ADX trend strength.
- `0007_bollinger_breakout.afl`: Close outside Bollinger Bands, exit at moving average.
- `0008_keltner_atr_channel.afl`: EMA plus ATR channel breakout.
- `0009_supertrend.afl`: ATR trailing trend line reversal system.
- `0010_parabolic_sar.afl`: Parabolic SAR reversal strategy.
- `0011_linear_regression_slope.afl`: Linear regression slope trend system.
- `0012_chandelier_breakout.afl`: Channel breakout with Chandelier-style ATR exits.
- `0013_volatility_contraction_breakout.afl`: Breakout after narrow range and quiet ATR conditions.
- `0014_time_series_momentum_12m.afl`: 12-month absolute momentum with shorter exit momentum.
- `0015_dual_lookback_momentum.afl`: Composite 3/6/12-month momentum signal.
- `0016_weekly_trend_daily_pullback.afl`: Weekly trend filter with daily RSI pullback entry.
- `0017_monthly_channel_breakout.afl`: Slow monthly channel breakout to reduce turnover.
- `0018_heikin_ashi_trend.afl`: Heikin-Ashi trend confirmation with EMA filter.
- `0019_cci_trend_pullback.afl`: CCI pullback entry inside a long-term trend.
- `0020_rsi_trend_pullback.afl`: RSI pullback and recovery inside a long-term trend.
- `0021_stochastic_trend_continuation.afl`: Stochastic continuation entry with EMA trend filter.
- `0022_atr_expansion_momentum.afl`: Breakout only when ATR is expanding.
- `0023_ma_distance_reentry.afl`: Reentry after stretched pullbacks toward a moving average.
- `0024_close_only_channel_breakout.afl`: Close-only channel breakout for less intraday noise.
- `0025_volatility_adjusted_momentum.afl`: Momentum divided by ATR percentage.
- `0026_higher_high_sequence.afl`: Higher-high/higher-low sequence trend entry.
- `0027_range_expansion_followthrough.afl`: Large range expansion followed by next-bar confirmation.
- `0028_ema_crossover_slope_filter.afl`: EMA crossover confirmed by regression slope.
- `0029_triangular_ma_trend.afl`: Smoother triangular moving-average trend system.
- `0030_relative_strength_trend.afl`: Price trend plus relative strength versus a benchmark.
- `0031_ibs_trend_breakout.afl`: Pullback-style internal bar strength setup with breakout trigger.
- `0032_short_term_commodity_breakout.afl`: Five-bar commodity breakout with max-hold exit.
- `0033_two_day_pullback_snapback.afl`: Two-day pullback and snapback continuation.
- `0034_nr4_inside_day_breakout.afl`: Narrow-range or inside-day breakout.
- `0035_atr_thrust_followthrough.afl`: Large ATR body thrust followed by continuation.
- `0036_five_bar_reversal_break.afl`: Short-term reversal break from a five-bar extreme.
- `0037_rsi2_commodity_snapback.afl`: RSI(2) snapback inside a commodity trend.
- `0038_gap_reversal_commodity.afl`: Commodity gap reversal with short hold.
- `0039_gap_continuation_commodity.afl`: Commodity gap continuation with short hold.
- `0040_three_bar_range_compression.afl`: Three-bar compression breakout.
- `0041_fast_macd_commodity_swing.afl`: Fast MACD swing system for commodities.
- `0042_intraday_opening_range_breakout.afl`: Opening range breakout for intraday bars.
- `0043_prior_day_pivot_breakout.afl`: Intraday prior-day high/low pivot breakout.
- `0044_short_adx_burst.afl`: Short-term ADX directional burst.
- `0045_fast_stochastic_reversal.afl`: Fast stochastic short-term reversal.
- `0046_short_keltner_reversal.afl`: Keltner band reversal with max-hold exit.
- `0047_bollinger_squeeze_pop.afl`: Bollinger squeeze breakout pop.
- `0048_short_supertrend_swing.afl`: Fast SuperTrend-style short-hold swing.
- `0049_cci_zero_burst.afl`: CCI zero-line momentum burst.
- `0050_commodity_vs_spy_rotation.afl`: Commodity relative strength versus SPY.
- `0051_calendar_day_commodity_bias.afl`: Short-hold day-of-week commodity bias.
- `0052_pinocchio_bar_long_reversal.afl`: Long lower-wick rejection bar with body in the top half.
- `0053_engulfing_reversal_short_hold.afl`: Bullish/bearish engulfing reversal with short max hold.
- `0054_hammer_shooting_star_reversal.afl`: Hammer and shooting-star rejection pattern.
- `0055_outside_bar_failure.afl`: Outside-bar failure and reversal setup.
- `0056_failed_breakdown_reclaim.afl`: Failed breakdown/breakout reclaim setup.
- `0057_three_bar_morning_evening_reversal.afl`: Three-bar morning/evening reversal pattern.
- `0058_exhaustion_gap_fade.afl`: Gap fade after short-term stretch.
- `0059_opening_drive_gap_and_go.afl`: Intraday gap-and-go opening drive.
- `0060_opening_range_failure_reversal.afl`: Intraday opening-range failure reversal.
- `0061_bollinger_rsi_mean_reversion.afl`: Bollinger Band plus RSI mean reversion.
- `0062_ibs_mean_reversion.afl`: Internal bar strength mean reversion.
- `0063_connors_style_pullback.afl`: Connors-style RSI and streak pullback.
- `0064_turtle_soup_false_breakout.afl`: Classic false breakout through a prior extreme.
- `0065_intraday_vwap_reversion.afl`: Intraday VWAP deviation mean reversion.
- `0066_opening_range_pullback_continuation.afl`: Opening-range breakout retest/continuation.

See `TEST_SEQUENCE.md` for a prioritized testing queue.

## Ranking Workflow

For each strategy, export AmiBroker backtest metrics and equity curves. Shortlist by MAR first, then remove duplicates by equity-curve correlation. A practical starting filter is:

- MAR above your portfolio median.
- Max drawdown tolerable at your intended position size.
- Equity-curve correlation below about 0.60 versus strategies you already run.
- Trade count high enough to avoid judging a system from a tiny sample.
- Rules simple enough to execute with your actual broker, contract specs, slippage, and liquidity.

These are research templates, not trading advice. Validate contract settings, session times, slippage, commissions, and portfolio constraints before trusting any result.
