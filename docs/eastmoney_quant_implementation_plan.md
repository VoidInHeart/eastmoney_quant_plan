# 基于东方财富 A 股数据的量化选股系统实现方案

## 1. 项目目标

本项目目标是实现一个基于东方财富 A 股行情数据的量化选股与回测系统。系统主要完成以下工作：

1. 从东方财富相关数据源获取 A 股行情数据；
2. 对原始行情数据进行清洗、标准化和本地缓存；
3. 基于日频行情数据构造多因子选股模型；
4. 按月生成调仓信号；
5. 进行简单但规范的历史回测；
6. 输出收益曲线、交易记录和核心绩效指标。

该项目适合作为课程设计、量化入门项目或后续扩展为更完整量化研究框架的基础版本。

---

## 2. 总体技术路线

推荐采用以下路线：

```text
东方财富数据源
    ↓
数据获取模块 data_fetch.py
    ↓
数据清洗模块 data_clean.py
    ↓
因子计算模块 factor.py
    ↓
选股信号模块 signal.py
    ↓
回测模块 backtest.py
    ↓
绩效分析与报告模块 report.py
```

建议优先使用 `AKShare` 获取东方财富数据。AKShare 已经封装了常用的东方财富行情接口，开发成本低，也更适合快速完成原型系统。

对于进阶版本，可以单独实现一个 `requests` 版本的数据获取模块，直接请求东方财富 JSON 接口。但直接调接口的稳定性较弱，后续东方财富字段或分页规则变化时，需要维护成本。

---

## 3. 推荐项目名称

```text
eastmoney_quant
```

推荐项目主题：

```text
基于东方财富 A 股日频行情数据的多因子选股与月度轮动策略研究
```

---

## 4. 推荐目录结构

```text
eastmoney_quant/
│
├── README.md
├── requirements.txt
├── config.py
├── main.py
│
├── data_fetch.py
├── data_clean.py
├── factor.py
├── signal.py
├── backtest.py
├── report.py
│
├── data/
│   ├── raw/
│   ├── processed/
│   └── result/
│
└── notebooks/
    └── exploratory_analysis.ipynb
```

各文件职责如下：

| 文件 | 作用 |
|---|---|
| `config.py` | 统一配置路径、日期、手续费、调仓频率、股票数量等参数 |
| `data_fetch.py` | 从东方财富或 AKShare 获取行情数据 |
| `data_clean.py` | 清洗字段、转换类型、处理缺失值、保存标准数据 |
| `factor.py` | 计算动量、反转、低波动、流动性等因子 |
| `signal.py` | 根据因子分数生成月度调仓信号 |
| `backtest.py` | 根据调仓信号执行回测 |
| `report.py` | 计算绩效指标并输出结果 |
| `main.py` | 串联完整流程 |
| `requirements.txt` | Python 依赖 |
| `README.md` | 项目说明 |

---

## 5. Python 依赖

`requirements.txt` 建议如下：

```txt
akshare
pandas
numpy
pyarrow
tqdm
matplotlib
openpyxl
requests
```

安装命令：

```bash
pip install -r requirements.txt
```

如果只是完成基础版本，核心依赖是：

```bash
pip install akshare pandas numpy pyarrow tqdm matplotlib
```

---

## 6. 数据获取方案

### 6.1 推荐方案：使用 AKShare

优先使用 AKShare 获取东方财富数据。

获取 A 股实时行情：

```python
import akshare as ak

spot = ak.stock_zh_a_spot_em()
```

常见字段包括：

```text
代码
名称
最新价
涨跌幅
涨跌额
成交量
成交额
振幅
最高
最低
今开
昨收
量比
换手率
市盈率-动态
市净率
总市值
流通市值
```

获取单只股票历史日线：

```python
import akshare as ak

df = ak.stock_zh_a_hist(
    symbol="000001",
    period="daily",
    start_date="20200101",
    end_date="20260705",
    adjust="hfq"
)
```

参数说明：

| 参数 | 含义 |
|---|---|
| `symbol` | 股票代码，例如 `"000001"` |
| `period` | 周期，基础版本使用 `"daily"` |
| `start_date` | 开始日期，格式为 `YYYYMMDD` |
| `end_date` | 结束日期，格式为 `YYYYMMDD` |
| `adjust` | 复权方式，推荐 `"hfq"` 后复权 |

复权建议：

| 复权方式 | 说明 | 是否推荐 |
|---|---|---|
| 不复权 | 保留原始价格，但会出现分红送转导致的价格断层 | 不推荐用于因子回测 |
| 前复权 | 当前价格不变，历史价格调整 | 可用 |
| 后复权 | 初始价格不变，后续价格连续增长 | 推荐用于长期收益率研究 |

本项目推荐使用：

```python
adjust="hfq"
```

---

### 6.2 直接请求东方财富接口的备选方案

如果不用 AKShare，可以用 `requests` 直接请求东方财富的历史 K 线接口。

示例接口：

```text
https://push2his.eastmoney.com/api/qt/stock/kline/get
```

基础请求函数可以设计为：

```python
def fetch_em_kline(
    code: str,
    start: str = "20200101",
    end: str = "20500101",
    period: str = "daily",
    adjust: str = "hfq"
) -> pd.DataFrame:
    ...
```

需要处理的关键点：

1. 股票代码需要转换为东方财富 `secid`；
2. 沪市通常是 `1.xxxxxx`；
3. 深市通常是 `0.xxxxxx`；
4. 北交所和特殊品种不要手写规则，建议交给 AKShare；
5. 返回数据里的 `klines` 通常是字符串数组，需要拆分成 DataFrame；
6. 接口可能变动，必须加入异常处理和字段校验。

基础 `secid` 转换函数：

```python
def to_secid(code: str) -> str:
    code = str(code).zfill(6)

    if code.startswith(("6", "9")):
        return f"1.{code}"
    else:
        return f"0.{code}"
```

直接请求接口只作为进阶方案，不建议作为课程项目的首选实现。

---

## 7. 数据缓存设计

不要每次运行程序都重新下载全部股票历史数据。推荐使用本地缓存。

推荐目录：

```text
data/
├── raw/
│   ├── spot_latest.parquet
│   └── a_share_daily_hfq.parquet
│
├── processed/
│   ├── clean_daily.parquet
│   ├── factor_daily.parquet
│   └── rebalance_signal.parquet
│
└── result/
    ├── backtest_nav.csv
    ├── trades.csv
    ├── performance.json
    └── nav_curve.png
```

推荐使用 `parquet` 格式保存中间数据：

```python
df.to_parquet("data/raw/a_share_daily_hfq.parquet", index=False)
df = pd.read_parquet("data/raw/a_share_daily_hfq.parquet")
```

`parquet` 比 CSV 更适合保存大量股票日线数据，读取速度更快，字段类型也更稳定。

---

## 8. 标准数据字段设计

清洗后的日线数据建议统一成以下字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `date` | datetime | 交易日期 |
| `code` | str | 股票代码 |
| `name` | str | 股票名称 |
| `open` | float | 开盘价 |
| `close` | float | 收盘价 |
| `high` | float | 最高价 |
| `low` | float | 最低价 |
| `volume` | float | 成交量 |
| `amount` | float | 成交额 |
| `amplitude` | float | 振幅 |
| `pct_chg` | float | 涨跌幅 |
| `change` | float | 涨跌额 |
| `turnover` | float | 换手率 |

字段清洗目标：

1. 所有中文字段统一改成英文字段；
2. `date` 转为 `datetime`；
3. `code` 保持 6 位字符串；
4. 价格、成交额、换手率等字段转成数值类型；
5. 删除严重缺失的数据；
6. 按 `code, date` 排序。

---

## 9. 股票池过滤规则

每个调仓日生成股票池时，需要做基础过滤：

```text
1. 剔除 ST 股票；
2. 剔除名称中包含“退”的股票；
3. 剔除上市不足 120 个交易日的股票；
4. 剔除最近 20 日平均成交额过低的股票；
5. 剔除价格缺失的股票；
6. 剔除停牌股票；
7. 剔除调仓日无法买入的股票，例如一字涨停股票。
```

基础版本可以先实现前 5 条。

推荐参数：

```python
MIN_LISTING_DAYS = 120
MIN_AMOUNT_MA20 = 100_000_000
```

表示：

```text
上市时间至少 120 个交易日；
最近 20 日日均成交额至少 1 亿元。
```

---

## 10. 多因子策略设计

本项目采用日频多因子选股策略。

### 10.1 调仓频率

```text
每月调仓一次
```

调仓日选择：

```text
每个月最后一个交易日
```

### 10.2 持仓数量

```text
持有综合评分最高的 20 只股票
```

参数：

```python
TOP_N = 20
```

### 10.3 权重方式

基础版本使用等权持仓：

```text
每只股票权重 = 1 / 持仓股票数量
```

例如选出 20 只股票，则每只股票权重为：

```text
5%
```

---

## 11. 因子设计

本项目设计 4 类基础因子：

1. 中期动量因子；
2. 短期反转因子；
3. 低波动因子；
4. 流动性因子。

---

### 11.1 中期动量因子

定义：

```python
mom_60_20 = close.shift(20) / close.shift(60) - 1
```

含义：

```text
过去 60 日到过去 20 日之间的涨跌幅。
```

设计理由：

1. 避开最近 20 个交易日，减少短期反转噪声；
2. 保留中期趋势信息；
3. 股票中期走势强，未来可能继续保持相对强势。

---

### 11.2 短期反转因子

定义：

```python
rev_5 = -(close / close.shift(5) - 1)
```

含义：

```text
最近 5 日涨幅越高，反转因子得分越低；
最近 5 日涨幅越低，反转因子得分越高。
```

设计理由：

1. A 股短期容易出现过度反应；
2. 短期涨太多的股票可能回调；
3. 在中期趋势因子之外加入短期反转，可以降低追高风险。

---

### 11.3 低波动因子

定义：

```python
vol_20 = -rolling_std(ret, 20)
```

其中：

```python
ret = close.pct_change()
```

含义：

```text
最近 20 日收益率波动越小，因子得分越高。
```

设计理由：

1. 降低组合波动；
2. 避免选入短期剧烈波动的股票；
3. 提高组合稳定性。

---

### 11.4 流动性因子

定义：

```python
liq_20 = log(mean(amount, 20))
```

含义：

```text
最近 20 日平均成交额越高，流动性得分越高。
```

设计理由：

1. 成交额高的股票更容易成交；
2. 减少小盘冷门股带来的冲击成本；
3. 提高回测结果的可执行性。

---

## 12. 因子标准化与综合评分

不同因子量纲不同，需要在每个交易日做横截面标准化。

标准化函数：

```python
def zscore(s):
    return (s - s.mean()) / s.std()
```

对每个交易日、每个因子分别做：

```python
factor_z = groupby(date).transform(zscore)
```

综合评分：

```python
score = (
    0.40 * mom_60_20_z
    + 0.20 * rev_5_z
    + 0.25 * vol_20_z
    + 0.15 * liq_20_z
)
```

权重设计：

| 因子 | 权重 |
|---|---:|
| 中期动量 | 40% |
| 短期反转 | 20% |
| 低波动 | 25% |
| 流动性 | 15% |

权重解释：

1. 中期动量是主因子；
2. 短期反转用于避免追高；
3. 低波动用于控制风险；
4. 流动性用于增强可交易性。

---

## 13. 调仓信号生成

每个月最后一个交易日执行以下流程：

```text
1. 取当日所有股票的因子数据；
2. 过滤无效股票；
3. 按 score 从高到低排序；
4. 选择前 TOP_N 只股票；
5. 对入选股票分配等权权重；
6. 保存调仓信号。
```

信号表字段：

| 字段 | 说明 |
|---|---|
| `date` | 调仓日期 |
| `code` | 股票代码 |
| `score` | 综合评分 |
| `weight` | 目标权重 |

示例：

```text
date        code      score     weight
2024-01-31  600519    2.31      0.05
2024-01-31  000858    2.12      0.05
...
```

---

## 14. 回测设计

### 14.1 基础回测假设

基础版本采用以下假设：

```text
1. 每月最后一个交易日收盘后生成信号；
2. 下一交易日按开盘价买入；
3. 持仓到下一个调仓周期；
4. 组合等权；
5. 考虑手续费；
6. 可以先不考虑滑点，进阶版本再加入。
```

### 14.2 手续费设置

推荐参数：

```python
COMMISSION_RATE = 0.0003
STAMP_TAX_RATE = 0.001
SLIPPAGE_RATE = 0.0005
```

说明：

| 成本 | 含义 |
|---|---|
| 佣金 | 买卖都收 |
| 印花税 | A 股卖出时收 |
| 滑点 | 模拟成交价格不利偏移 |

基础版本可以先只考虑佣金：

```python
COMMISSION_RATE = 0.0003
```

进阶版本再加入印花税和滑点。

---

### 14.3 回测核心逻辑

每个交易日需要计算组合净值：

```text
当日组合收益 = Σ 股票权重 × 股票当日收益率
组合净值 = 前一日净值 × (1 + 当日组合收益 - 当日交易成本)
```

收益率字段：

```python
ret = close / previous_close - 1
```

完整回测应避免未来函数：

```text
调仓日 T 收盘后得到信号；
实际成交日应为 T+1；
持仓收益从 T+1 到下一个调仓周期。
```

---

## 15. 绩效指标

`report.py` 至少输出以下指标：

| 指标 | 说明 |
|---|---|
| 累计收益率 | 总收益 |
| 年化收益率 | 按年折算后的收益 |
| 年化波动率 | 日收益标准差年化 |
| 夏普比率 | 年化收益 / 年化波动 |
| 最大回撤 | 净值从高点到低点的最大跌幅 |
| 胜率 | 正收益交易日比例 |
| 月胜率 | 正收益月份比例 |
| 换手率 | 调仓时持仓变化程度 |

常用计算方式：

```python
annual_return = nav.iloc[-1] ** (252 / len(nav)) - 1
annual_vol = daily_ret.std() * np.sqrt(252)
sharpe = annual_return / annual_vol
max_drawdown = (nav / nav.cummax() - 1).min()
```

---

## 16. 各模块建议接口

### 16.1 config.py

```python
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"

RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
RESULT_DIR = DATA_DIR / "result"

START_DATE = "20200101"
END_DATE = "20260705"

ADJUST = "hfq"

MIN_LISTING_DAYS = 120
MIN_AMOUNT_MA20 = 100_000_000

TOP_N = 20

COMMISSION_RATE = 0.0003
STAMP_TAX_RATE = 0.001
SLIPPAGE_RATE = 0.0005

INITIAL_CASH = 1_000_000
```

---

### 16.2 data_fetch.py

建议函数：

```python
def fetch_stock_list() -> pd.DataFrame:
    """
    获取 A 股实时行情列表。
    返回字段至少包括 code, name, latest_price, amount, turnover。
    """
```

```python
def fetch_one_stock_history(
    code: str,
    start_date: str,
    end_date: str,
    adjust: str = "hfq"
) -> pd.DataFrame:
    """
    获取单只股票历史行情。
    """
```

```python
def fetch_all_stock_history(
    start_date: str,
    end_date: str,
    adjust: str = "hfq"
) -> pd.DataFrame:
    """
    批量获取全部股票历史行情。
    需要加入异常处理、进度条和 sleep 控制频率。
    """
```

---

### 16.3 data_clean.py

建议函数：

```python
def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    将 AKShare 返回的中文字段统一改成英文字段。
    """
```

```python
def clean_daily_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    清洗日线数据：
    - 转换日期；
    - 转换数值；
    - 统一 code；
    - 去除严重缺失；
    - 排序。
    """
```

```python
def save_clean_data(df: pd.DataFrame) -> None:
    """
    保存清洗后的数据。
    """
```

---

### 16.4 factor.py

建议函数：

```python
def add_basic_returns(df: pd.DataFrame) -> pd.DataFrame:
    """
    添加日收益率 ret。
    """
```

```python
def build_factors(df: pd.DataFrame) -> pd.DataFrame:
    """
    计算 mom_60_20、rev_5、vol_20、liq_20。
    """
```

```python
def cross_sectional_zscore(df: pd.DataFrame, factor_cols: list[str]) -> pd.DataFrame:
    """
    对每个交易日的每个因子做横截面 zscore。
    """
```

```python
def build_score(df: pd.DataFrame) -> pd.DataFrame:
    """
    根据因子权重计算综合 score。
    """
```

---

### 16.5 signal.py

建议函数：

```python
def get_rebalance_dates(df: pd.DataFrame) -> list[pd.Timestamp]:
    """
    获取每个月最后一个交易日。
    """
```

```python
def filter_stock_pool(df: pd.DataFrame) -> pd.DataFrame:
    """
    根据上市天数、成交额、ST、缺失值等条件过滤股票池。
    """
```

```python
def build_monthly_signal(df: pd.DataFrame, top_n: int = 20) -> pd.DataFrame:
    """
    在每个调仓日选择 score 最高的 top_n 只股票并分配等权权重。
    """
```

---

### 16.6 backtest.py

建议函数：

```python
def prepare_daily_returns(df: pd.DataFrame) -> pd.DataFrame:
    """
    准备每日收益率数据。
    """
```

```python
def expand_positions(signal: pd.DataFrame, trade_dates: list[pd.Timestamp]) -> pd.DataFrame:
    """
    将月度调仓信号展开为每日持仓。
    """
```

```python
def calculate_turnover(prev_position: pd.DataFrame, new_position: pd.DataFrame) -> float:
    """
    计算调仓换手率。
    """
```

```python
def run_backtest(
    daily_data: pd.DataFrame,
    signal: pd.DataFrame,
    initial_cash: float = 1_000_000
) -> dict:
    """
    执行回测，返回净值、交易记录、绩效指标。
    """
```

---

### 16.7 report.py

建议函数：

```python
def calc_performance(nav: pd.DataFrame) -> dict:
    """
    计算累计收益、年化收益、夏普比率、最大回撤等指标。
    """
```

```python
def plot_nav(nav: pd.DataFrame) -> None:
    """
    绘制净值曲线。
    """
```

```python
def export_report(result: dict) -> None:
    """
    保存 nav、trades、performance 等结果。
    """
```

---

## 17. 主流程 main.py

推荐 `main.py`：

```python
from config import START_DATE, END_DATE, ADJUST
from data_fetch import fetch_all_stock_history
from data_clean import clean_daily_data
from factor import build_factors
from signal import build_monthly_signal
from backtest import run_backtest
from report import export_report


def main():
    raw_data = fetch_all_stock_history(
        start_date=START_DATE,
        end_date=END_DATE,
        adjust=ADJUST
    )

    clean_data = clean_daily_data(raw_data)

    factor_data = build_factors(clean_data)

    signal = build_monthly_signal(factor_data)

    result = run_backtest(factor_data, signal)

    export_report(result)


if __name__ == "__main__":
    main()
```

---

## 18. 实现顺序建议

建议按以下顺序实现，不要一上来就写完整回测：

```text
第一步：实现 data_fetch.py，能拿到单只股票历史数据；
第二步：实现批量下载，并保存 parquet；
第三步：实现 data_clean.py，统一字段；
第四步：实现 factor.py，检查因子计算结果；
第五步：实现 signal.py，生成每月调仓股票列表；
第六步：实现简化版 backtest.py；
第七步：实现 report.py；
第八步：加入手续费、滑点、涨跌停、停牌等细节。
```

---

## 19. Codex 代码生成提示词

可以把下面这段直接发给 Codex：

```text
请根据我提供的 Markdown 方案，帮我实现一个 Python 项目 eastmoney_quant。

要求：
1. 使用 AKShare 获取东方财富 A 股日频行情数据；
2. 使用 pandas / numpy 完成数据清洗、因子计算、信号生成和回测；
3. 项目结构包括：
   - config.py
   - data_fetch.py
   - data_clean.py
   - factor.py
   - signal.py
   - backtest.py
   - report.py
   - main.py
   - requirements.txt
   - README.md
4. 数据保存为 parquet；
5. 策略为月度调仓多因子选股策略；
6. 因子包括：
   - mom_60_20
   - rev_5
   - vol_20
   - liq_20
7. 综合评分：
   score = 0.40 * mom_60_20_z
         + 0.20 * rev_5_z
         + 0.25 * vol_20_z
         + 0.15 * liq_20_z
8. 每月最后一个交易日调仓，选择 score 最高的 20 只股票，等权持仓；
9. 回测要避免未来函数：调仓日 T 生成信号，T+1 执行交易；
10. 输出：
    - 净值曲线 CSV
    - 交易记录 CSV
    - 绩效指标 JSON
    - 净值曲线 PNG
11. 代码要有异常处理、日志输出和必要注释；
12. 批量下载数据时要加入 sleep，避免高频请求。
```

---

## 20. 需要特别避免的问题

### 20.1 未来函数

错误写法：

```text
当天收盘后计算因子，又假设当天收盘价买入。
```

正确逻辑：

```text
T 日收盘后生成信号；
T+1 日开盘或收盘成交。
```

---

### 20.2 未复权价格

如果使用未复权价格，分红送转会导致价格断层，收益率和因子会失真。

推荐：

```python
adjust="hfq"
```

---

### 20.3 直接用涨跌幅字段回测

不同数据源的涨跌幅字段可能已经经过四舍五入，最好自己用收盘价计算收益率：

```python
ret = close / close.shift(1) - 1
```

---

### 20.4 忽略停牌和涨跌停

基础版本可以先不处理，但在报告中需要说明局限性。

进阶版本应该加入：

```text
停牌不能交易；
涨停不能买入；
跌停不能卖出。
```

---

### 20.5 股票代码类型错误

股票代码不能存成整数，否则前导 0 会丢失。

错误：

```python
code = 1
```

正确：

```python
code = "000001"
```

---

## 21. 可选扩展方向

如果基础版本完成，可以继续扩展：

### 21.1 加入基准指数

例如使用沪深 300、中证 500 或万得全 A 作为基准，比较策略超额收益。

输出：

```text
策略净值
基准净值
超额收益净值
```

---

### 21.2 加入行业中性

避免组合集中在单一行业。

简单做法：

```text
每个行业最多选 N 只股票。
```

---

### 21.3 加入止损规则

例如：

```text
单只股票从买入价下跌超过 10%，则卖出。
```

但止损规则容易引入更多交易成本，必须回测验证。

---

### 21.4 加入机器学习模型

可以把因子作为特征，预测未来 20 日收益率。

示例特征：

```text
mom_60_20
rev_5
vol_20
liq_20
turnover
amount_ma20
```

示例模型：

```text
线性回归
岭回归
随机森林
XGBoost
LightGBM
```

课程项目中，多因子规则模型已经足够；机器学习版本更复杂，需要防止过拟合。

---

## 22. 最终交付物

项目最终至少应包含：

```text
1. 完整 Python 源代码；
2. requirements.txt；
3. README.md；
4. 原始行情数据缓存；
5. 因子数据文件；
6. 调仓信号文件；
7. 回测净值文件；
8. 交易记录文件；
9. 绩效指标文件；
10. 净值曲线图片。
```

推荐 README 中展示：

```text
项目简介
数据来源
策略逻辑
运行方法
结果展示
风险提示
后续改进方向
```

---

## 23. 风险提示

本项目仅用于学习、课程设计和研究原型，不构成投资建议。东方财富数据接口可能变化，数据也可能存在延迟、缺失或字段调整。真实投资需要考虑数据授权、交易规则、滑点、手续费、冲击成本、风控、资金容量和合规要求。
