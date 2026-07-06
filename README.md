# eastmoney_quant

基于东方财富 A 股日频行情数据的多因子选股与月度轮动回测原型。

本项目按 `docs/eastmoney_quant_implementation_plan.md` 的初始方案实现，目标是提供一个课程设计或量化入门研究可用的第一版工程骨架：数据获取、清洗、因子计算、月度选股、回测和结果导出。

## 功能

- 通过 AKShare 获取东方财富 A 股行情数据
- 将原始数据、清洗数据、因子数据和调仓信号保存为 parquet
- 计算 4 个基础因子：`mom_60_20`、`rev_5`、`vol_20`、`liq_20`
- 每月最后一个交易日选取综合得分最高的股票并等权持有
- 回测中使用 T 日收盘后信号、T+1 生效，避免直接使用当日成交的未来函数
- 导出净值曲线、交易记录、绩效指标和净值图

## 项目结构

```text
eastmoney_quant_plan/
  eastmoney_quant/
    config.py
    data_fetch.py
    data_clean.py
    factor.py
    signal.py
    backtest.py
    report.py
  data/
    raw/
    processed/
    result/
  docs/
  notebooks/
  tests/
  main.py
  requirements.txt
```

## 安装

```bash
python -m pip install -r requirements.txt
```

## 运行

先用离线示例数据跑通完整流程：

```bash
python main.py --sample
```

使用 AKShare 下载真实行情并回测：

```bash
python main.py
```

如果本机代理会拦截东方财富接口，程序会默认让 `eastmoney.com` 相关请求绕过代理。若仍然看到 `ProxyError`，请先关闭命令行中的 `HTTP_PROXY` / `HTTPS_PROXY`，或在终端里设置：

```bash
set NO_PROXY=eastmoney.com,.eastmoney.com,push2.eastmoney.com,push2his.eastmoney.com,82.push2.eastmoney.com
```

强制重新下载真实行情：

```bash
python main.py --force-fetch
```

快速检查真实接口时，建议先跑少量股票：

```bash
python main.py --force-fetch --codes 000001 600519 000858 --top-n 2
```

如果东方财富接口一直断开，直接指定腾讯历史行情源：

```bash
python main.py --force-fetch --history-source tencent
```

或只下载列表前 N 只：

```bash
python main.py --force-fetch --history-source tencent --limit 50
```

## 输出

运行后会生成：

- `data/processed/clean_daily.parquet`
- `data/processed/factor_daily.parquet`
- `data/processed/rebalance_signal.parquet`
- `data/result/backtest_nav.csv`
- `data/result/trades.csv`
- `data/result/performance.json`
- `data/result/nav_curve.png`

## 策略说明

综合得分：

```text
score = 0.40 * mom_60_20_z
      + 0.20 * rev_5_z
      + 0.25 * vol_20_z
      + 0.15 * liq_20_z
```

基础股票池过滤：

- 剔除 ST 和名称包含“退”的股票
- 剔除上市不足 120 个交易日的股票
- 剔除最近 20 日平均成交额低于 1 亿元的股票
- 剔除价格缺失或无效的股票

## 风险提示

本项目仅用于学习、课程设计和研究原型，不构成投资建议。真实投资还需要考虑数据授权、停牌、涨跌停、滑点、冲击成本、税费、风控、资金容量和合规要求。
