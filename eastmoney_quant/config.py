from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
RESULT_DIR = DATA_DIR / "result"

SPOT_CACHE_PATH = RAW_DIR / "spot_latest.parquet"
RAW_DAILY_PATH = RAW_DIR / "a_share_daily_hfq.parquet"
CLEAN_DAILY_PATH = PROCESSED_DIR / "clean_daily.parquet"
FACTOR_DAILY_PATH = PROCESSED_DIR / "factor_daily.parquet"
SIGNAL_PATH = PROCESSED_DIR / "rebalance_signal.parquet"

NAV_PATH = RESULT_DIR / "backtest_nav.csv"
TRADES_PATH = RESULT_DIR / "trades.csv"
PERFORMANCE_PATH = RESULT_DIR / "performance.json"
NAV_PLOT_PATH = RESULT_DIR / "nav_curve.png"

START_DATE = "20200101"
END_DATE = "20260705"
ADJUST = "hfq"
HISTORY_SOURCE = "auto"  # auto, eastmoney, or tencent

MIN_LISTING_DAYS = 120
MIN_AMOUNT_MA20 = 100_000_000
TOP_N = 20

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
