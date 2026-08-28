# Strategy Symbol And Timeframe Guide

These are starting hypotheses for backtesting, not guarantees. Rank each strategy by CAGR, MAR, trade count, slippage sensitivity, and equity-curve correlation to your existing book.

## Symbol Groups

- Equity index: `SPY`, `/ES`, `/NQ`, `/RTY`, `/YM`
- Rates: `/ZB`, `/ZN`, `/ZF`, `/ZT`
- Currencies: `/6E`, `/6J`, `/6B`, `/6A`, `/6C`, `/6S`
- Energy: `/CL`, `/NG`, `/RB`, `/HO`
- Metals: `/GC`, `/SI`, `/HG`, `/PL`
- Agriculture: `/ZC`, `/ZS`, `/ZM`, `/ZL`, `/ZW`, `/LE`, `/HE`
- Softs: `/KC`, `/SB`, `/CT`, `/CC`
- Crypto: `/BTC`, `/ETH`, `/MBT`, `/MET`, `/SOL`, `/XRP`

## Practical Ranking

For your stated goal, start with scripts `0032`-`0051` on energy and metals first. Then test agriculture and softs. Use equity-index symbols mainly as a correlation check, not the primary target.

Short-term systems are much more sensitive to commissions, slippage, session templates, and data quality than slower trend systems. Validate fills with realistic futures costs before trusting CAGR or MAR.

| # | Strategy | Best first symbols | Best timeframe | Secondary tests | Why |
|---|---|---|---|---|---|
| 0001 | 2-Period RSI Mean Reversion | `SPY`, `/ES`, `/NQ`, `/CL`, `/GC` | Daily | 60min after retuning | Simple RSI(2) mean-reversion baseline with a long-term trend filter and short holding period. |
| 0002 | Moving Average Crossover | `/CL`, `/GC`, `/ES`, `/NQ`, `/ZN` | Daily | 60min on `/CL`, `/GC` | Broad baseline trend system; usually slower and correlated with other trend systems. |
| 0003 | Donchian Breakout | `/CL`, `/GC`, `/NG`, `/ZC`, `/ZS` | Daily | 60min on energy/metals | Classic futures breakout; strongest first test on commodities with episodic moves. |
| 0004 | Turtle Trading | `/CL`, `/GC`, `/SI`, `/HG`, `/ZC`, `/ZS` | Daily | Daily portfolio across all futures | Designed for diversified futures trend following. |
| 0005 | MACD Trend Following | `/GC`, `/CL`, `/6E`, `/ES`, `/NQ` | Daily | 60min | Good baseline momentum/trend regime, moderate overlap with MA systems. |
| 0006 | ADX Breakout | `/CL`, `/NG`, `/GC`, `/HG`, `/RB`, `/HO` | Daily | 60min | ADX can filter commodity chop and focus on directional bursts. |
| 0007 | Bollinger Breakout | `/GC`, `/SI`, `/CL`, `/NG`, `/BTC`, `/ETH` | Daily | 60min | Volatility breakout; useful on markets with compression/expansion cycles. |
| 0008 | Keltner ATR Channel | `/CL`, `/GC`, `/NG`, `/RB`, `/HO` | Daily | 60min | ATR channel adapts well to commodity volatility. |
| 0009 | SuperTrend | `/CL`, `/GC`, `/SI`, `/ES`, `/NQ` | Daily | 60min | ATR trailing regime system, easy benchmark for stop-driven trends. |
| 0010 | Parabolic SAR | `/GC`, `/CL`, `/6E`, `/NQ` | Daily | 60min | Faster reversal style; more turnover than slow breakouts. |
| 0011 | Linear Regression Slope | `/GC`, `/CL`, `/ZN`, `/6E`, `/BTC` | Daily | Daily diversified futures | Slope signal can differ from channel breakouts. |
| 0012 | Chandelier Breakout | `/CL`, `/GC`, `/NG`, `/HG`, `/ZC` | Daily | 60min | Breakout with adaptive exit; good MAR candidate when stops reduce deep giveback. |
| 0013 | Volatility Contraction Breakout | `/CL`, `/GC`, `/SI`, `/NG`, `/KC` | Daily | 60min | Targets compression before expansion; often less correlated with pure momentum. |
| 0014 | 12M Time-Series Momentum | All liquid futures, especially `/CL`, `/GC`, `/ZN`, `/6E`, `/ZC` | Daily | Weekly via database compression | Slow diversified trend signal; not short-term. |
| 0015 | Dual Lookback Momentum | All liquid futures | Daily | Weekly | Multi-horizon momentum; use as diversified allocation baseline. |
| 0016 | Weekly Trend Daily Pullback | `/CL`, `/GC`, `/SI`, `/ZC`, `/ZS`, `/6E` | Daily | 60min with adjusted lengths | Pullback timing can reduce correlation to breakout systems. |
| 0017 | Monthly Channel Breakout | All liquid futures | Daily | Weekly/monthly analysis | Very slow; portfolio diversifier, not a short-term candidate. |
| 0018 | Heikin-Ashi Trend | `/CL`, `/GC`, `/ES`, `/NQ`, `/BTC` | Daily | 60min | Smooth trend state; useful as a comparison to SuperTrend. |
| 0019 | CCI Trend Pullback | `/CL`, `/GC`, `/SI`, `/HG`, `/6E` | Daily | 60min | Pullback-in-trend; lower overlap with channel breakout entries. |
| 0020 | RSI Trend Pullback | `/CL`, `/GC`, `/NG`, `/SI`, `/ZC` | Daily | 60min | Shorter entry timing inside longer trend. |
| 0021 | Stochastic Trend Continuation | `/CL`, `/GC`, `/SI`, `/6E`, `/NQ` | Daily | 60min | Oscillator continuation; expect more trades than slow trend systems. |
| 0022 | ATR Expansion Momentum | `/CL`, `/NG`, `/GC`, `/RB`, `/HO` | Daily | 60min | Best on markets where volatility expansion accompanies directional moves. |
| 0023 | MA Distance Reentry | `/CL`, `/GC`, `/SI`, `/NQ`, `/BTC` | Daily | 60min | Pullback/reentry system; less correlated with fresh breakout entries. |
| 0024 | Close-Only Channel Breakout | `/CL`, `/GC`, `/ZC`, `/ZS`, `/6E` | Daily | Daily diversified futures | Reduces noise from intraday high/low spikes. |
| 0025 | Volatility-Adjusted Momentum | `/CL`, `/GC`, `/ZN`, `/6E`, `/ZC`, `/ZS` | Daily | Weekly | Momentum adjusted for volatility; useful cross-market ranking candidate. |
| 0026 | Higher-High Sequence | `/CL`, `/GC`, `/SI`, `/HG`, `/NQ` | Daily | 60min | Pattern-based short/mid trend entry. |
| 0027 | Range Expansion Follow-Through | `/CL`, `/NG`, `/GC`, `/RB`, `/HO` | Daily | 60min | Commodity-friendly thrust/follow-through logic. |
| 0028 | EMA Crossover Slope Filter | `/CL`, `/GC`, `/6E`, `/ZN`, `/NQ` | Daily | 60min | Trend filter reduces some crossover whipsaws. |
| 0029 | Triangular MA Trend | `/GC`, `/CL`, `/ZN`, `/6E` | Daily | Weekly | Smoother, slower trend baseline. |
| 0030 | Relative Strength Trend | `/CL`, `/GC`, `/DBC`, `/GLD`, `/USO` if available | Daily | Daily with `SPY` benchmark | Directly checks commodity strength versus equities. |
| 0031 | IBS Trend Breakout | `/CL`, `/GC`, `/SI`, `/NG`, `/NQ` | Daily | 60min | Pullback plus breakout trigger; can diversify away from plain trend. |
| 0032 | Short-Term Commodity Breakout | `/CL`, `/GC`, `/SI`, `/NG`, `/RB`, `/HO` | Daily | 60min | Under-5-bar breakout designed for energy/metals. |
| 0033 | Two-Day Pullback Snapback | `/CL`, `/GC`, `/SI`, `/HG`, `/ZC`, `/ZS` | Daily | 60min | Mean-reverting entry inside trend, short hold. |
| 0034 | NR4 / Inside-Day Breakout | `/CL`, `/GC`, `/SI`, `/NG`, `/KC`, `/SB` | Daily | 60min | Compression breakout with short holding period. |
| 0035 | ATR Thrust Follow-Through | `/CL`, `/NG`, `/RB`, `/HO`, `/GC` | Daily | 60min | Captures large commodity impulse bars. |
| 0036 | Five-Bar Reversal Break | `/CL`, `/GC`, `/SI`, `/ZC`, `/ZS`, `/KC` | Daily | 60min | Reversal entry after short-term exhaustion. |
| 0037 | RSI(2) Commodity Snapback | `/CL`, `/GC`, `/SI`, `/HG`, `/NG` | Daily | 60min | Short-hold snapback; test carefully for slippage. |
| 0038 | Gap Reversal Commodity | `/CL`, `/NG`, `/RB`, `/HO`, `/GC` | Daily | 60min if session data supports gaps | Gap fade; depends heavily on session template. |
| 0039 | Gap Continuation Commodity | `/CL`, `/NG`, `/RB`, `/HO`, `/GC` | Daily | 60min if session data supports gaps | Tests whether commodity gaps continue instead of fade. |
| 0040 | Three-Bar Range Compression | `/CL`, `/GC`, `/SI`, `/NG`, `/KC` | Daily | 60min | Short compression/expansion setup. |
| 0041 | Fast MACD Commodity Swing | `/CL`, `/GC`, `/SI`, `/HG`, `/6E` | Daily | 60min | Fast momentum swing; likely higher turnover. |
| 0042 | Intraday Opening Range Breakout | `/CL`, `/GC`, `/NG`, `/RB`, `/HO`, `/SI` | 60min or 5min | 30min if imported | Intraday only; best on liquid commodities with defined sessions. |
| 0043 | Prior-Day Pivot Breakout | `/CL`, `/GC`, `/SI`, `/NG`, `/RB`, `/HO` | 60min or 5min | Daily as fallback | Intraday pivot breakout; good for short commodity bursts. |
| 0044 | Short-Term ADX Burst | `/CL`, `/NG`, `/GC`, `/RB`, `/HO`, `/HG` | Daily | 60min | Directional-strength filter for short bursts. |
| 0045 | Fast Stochastic Reversal | `/CL`, `/GC`, `/SI`, `/ZC`, `/ZS` | Daily | 60min | Short-term oscillator reversal inside trend. |
| 0046 | Short-Term Keltner Reversal | `/CL`, `/GC`, `/SI`, `/NG`, `/HG` | Daily | 60min | Band excursion reversal with short hold. |
| 0047 | Bollinger Squeeze Pop | `/CL`, `/GC`, `/SI`, `/NG`, `/KC` | Daily | 60min | Volatility squeeze expansion; commodity-friendly. |
| 0048 | Short-Hold SuperTrend Swing | `/CL`, `/GC`, `/SI`, `/NG`, `/NQ` | Daily | 60min | Fast ATR trend reversal with max hold. |
| 0049 | CCI Zero-Line Burst | `/CL`, `/GC`, `/SI`, `/HG`, `/6E` | Daily | 60min | Momentum burst from CCI regime shift. |
| 0050 | Commodity vs SPY Rotation | `/CL`, `/GC`, `/SI`, `/HG`, `/DBC`, `/GLD`, `/USO` if available | Daily | Daily only | Explicitly aims to diversify away from equity exposure. |
| 0051 | Calendar-Day Commodity Bias | `/CL`, `/GC`, `/NG`, `/RB`, `/HO`, `/ZC` | Daily | 60min after retuning | Tests short-hold day-of-week effects; validate robustness carefully. |
| 0052 | Pinocchio Bar Long Reversal | `/CL`, `/GC`, `/SI`, `/NG`, `/HG`, `/ZC`, `/ZS`, `/KC` | Daily | 60min on `/CL`, `/GC`, `/SI`, `/NG` | Long lower-wick rejection after a large range bar; best tested on volatile commodities where failed breakdowns can snap back. |
| 0053 | Engulfing Reversal Short-Hold | `/CL`, `/GC`, `/SI`, `/NG`, `/HG`, `/ZC`, `/ZS` | Daily | 60min on `/CL`, `/GC`, `/SI` | Objective engulfing pattern with trend filter and short max hold. |
| 0054 | Hammer / Shooting Star Reversal | `/CL`, `/GC`, `/SI`, `/HG`, `/NG`, `/KC` | Daily | 60min | Wick rejection pattern; best on volatile markets with clean rejection bars. |
| 0055 | Outside Bar Failure | `/CL`, `/GC`, `/SI`, `/NG`, `/RB`, `/HO` | Daily | 60min | Failed outside-bar continuation can create short snapback trades. |
| 0056 | Failed Breakdown / Breakout Reclaim | `/CL`, `/GC`, `/SI`, `/HG`, `/ZC`, `/ZS`, `/KC` | Daily | 60min | Tests false moves through recent highs/lows, often useful in choppy commodities. |
| 0057 | Three-Bar Morning / Evening Reversal | `/CL`, `/GC`, `/SI`, `/ZC`, `/ZS`, `/6E` | Daily | 60min after retuning | Three-candle reversal template with trend filter. |
| 0058 | Exhaustion Gap Fade | `/CL`, `/NG`, `/RB`, `/HO`, `/GC`, `/SI` | Daily | 60min if session data supports gaps | Mean-reversion gap fade after a short-term stretch. |
| 0059 | Opening Drive Gap-And-Go | `/CL`, `/GC`, `/NG`, `/RB`, `/HO`, `/SI` | 5min or 60min | 30min if imported | Intraday gap continuation/opening drive; needs clean session data. |
| 0060 | Opening Range Failure Reversal | `/CL`, `/GC`, `/NG`, `/RB`, `/HO`, `/SI` | 5min or 60min | 30min if imported | Intraday failed opening-range breakout; explicitly mean-reverting. |
| 0061 | Bollinger + RSI Mean Reversion | `/CL`, `/GC`, `/SI`, `/NG`, `/HG`, `/ZC`, `/ZS` | Daily | 60min | Simple band/oscillator mean reversion with short max hold. |
| 0062 | IBS Mean Reversion | `/CL`, `/GC`, `/SI`, `/NG`, `/ZC`, `/ZS`, `/KC` | Daily | 60min | Pure internal-bar-strength reversion with trend context; good low-correlation candidate. |
| 0063 | Connors-Style Pullback | `/CL`, `/GC`, `/SI`, `/HG`, `/ZC`, `/ZS` | Daily | 60min | RSI plus consecutive-close pullback; tests short-term exhaustion. |
| 0064 | Turtle Soup False Breakout | `/CL`, `/GC`, `/SI`, `/NG`, `/KC`, `/ZC`, `/ZS` | Daily | 60min | Classic false breakout/reclaim setup; strong fit for short-term commodity reversals. |
| 0065 | Intraday VWAP Reversion | `/CL`, `/GC`, `/SI`, `/NG`, `/RB`, `/HO` | 5min | 15min or 30min if imported | Intraday mean reversion to session VWAP; requires volume and clean session settings. |
| 0066 | Opening Range Pullback Continuation | `/CL`, `/GC`, `/NG`, `/RB`, `/HO`, `/SI` | 5min or 60min | 30min if imported | Continuation after breakout and retest of opening range boundary. |
