from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
RESULT_DIR = DATA_DIR / "result"
DISPLAY_DIR = ROOT_DIR / "display"

SPOT_CACHE_PATH = RAW_DIR / "spot_latest.parquet"
RAW_DAILY_PATH = RAW_DIR / "a_share_daily_hfq.parquet"
CLEAN_DAILY_PATH = PROCESSED_DIR / "clean_daily.parquet"
FACTOR_DAILY_PATH = PROCESSED_DIR / "factor_daily.parquet"
SIGNAL_PATH = PROCESSED_DIR / "rebalance_signal.parquet"

NAV_PATH = RESULT_DIR / "backtest_nav.csv"
BENCHMARK_NAV_PATH = RESULT_DIR / "benchmark_nav.csv"
TRADES_PATH = RESULT_DIR / "trades.csv"
PERFORMANCE_PATH = RESULT_DIR / "performance.json"
NAV_PLOT_PATH = RESULT_DIR / "nav_curve.png"
LATEST_PORTFOLIO_PATH = RESULT_DIR / "latest_portfolio.csv"
PREDICTION_LOG_PATH = RESULT_DIR / "prediction_log.csv"
PREDICTION_TRACKING_PATH = RESULT_DIR / "prediction_tracking.csv"
PREDICTION_SUMMARY_PATH = RESULT_DIR / "prediction_summary.json"
DASHBOARD_DATA_PATH = DISPLAY_DIR / "data.js"

START_DATE = "20200101"
END_DATE = "20260705"
ADJUST = "hfq"
HISTORY_SOURCE = "auto"  # auto, eastmoney, or tencent
TENCENT_REPAIR_AMOUNT = True

MIN_LISTING_DAYS = 180
MIN_AMOUNT_MA20 = 20_000_000
MAX_VOL_QUANTILE = 0.25
MIN_TREND_120 = -0.15
MIN_DRAWDOWN_120 = -0.45
TOP_N = 20

# Daily strategy settings.  Factor data still uses a longer history for
# lookbacks; only the reported backtest window is limited to one trading year.
REBALANCE_FREQUENCY = "weekly"
BACKTEST_TRADING_DAYS = 252
HOLDING_RANK_BUFFER = 50
MAX_REPLACEMENTS_PER_REBALANCE = 2
MIN_HOLDING_DAYS = 15
MIN_SCORE_IMPROVEMENT = 0.05
POSITION_WEIGHT_METHOD = "equal"  # equal, rank_tilt, or inverse_volatility
RANK_WEIGHT_BLEND = 0.5

COMMISSION_RATE = 0.0003
STAMP_TAX_RATE = 0.001
SLIPPAGE_RATE = 0.0005
INITIAL_CASH = 1_000_000

FETCH_SLEEP_SECONDS = 0.2
MAX_FETCH_RETRY = 3


def ensure_directories() -> None:
    """Create project data directories if they do not exist."""
    for path in (RAW_DIR, PROCESSED_DIR, RESULT_DIR):
        path.mkdir(parents=True, exist_ok=True)
