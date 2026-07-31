# eastmoney_quant

面向 A 股日频数据的多因子选股研究原型。项目覆盖数据更新、因子计算、受控调仓、T+1 开盘回测、预测跟踪、静态看板与策略验证。

> 当前策略是研究基线，**未通过稳健性验证，不应用于实盘或作为投资建议**。完整结论见 [策略验证报告](docs/策略验证报告.md)。

## 当前状态

- 当前基线：最近约 1 年、周频调仓、持有 20 只股票、等权重。
- 已实现换手控制：排名缓冲区、最短持有期、单次最多换仓数和评分替换阈值。
- 已做交易成本压力测试、训练/验证/锁定测试切分、5 年延长回测及参数稳定性检查。
- 锁定测试期在单边 20 bp 成本下未实现正的相对收益，因此当前结果不能视为已验证的 Alpha。
- 已冻结 `baseline_weekly_v1`，后续研究应与该基线比较，而不是回头按测试期结果调参。
- 已新增严格订单状态机；它会将不可交易订单挂起或取消，并只对实际成交计费，独立结果不覆盖基线。
- E0～E3 消融已在完整停复牌数据下运行；低波动/反转虽在验证期表现较好，但在后续已观察研究期均未维持正超额，当前不推进为 V2 策略。

## 策略概览

信号只使用 T 日收盘前可获得的数据，在 T+1 开盘模拟成交，并按开盘到开盘的持有期收益计价。

综合评分由当日横截面排名后的五个因子加权得到：

```text
score = 0.30 × 动量(120, 跳过最近20日)
      + 0.20 × 120日趋势
      + 0.15 × 10日反转
      + 0.25 × 60日低波动
      + 0.10 × 120日回撤
```

当前默认交易规则：

- 周频调仓，最近约 252 个交易日作为回测窗口；
- 选取 20 只股票并等权配置；
- 持仓落在前 70 名（`20 + 50` 的排名缓冲区）内时优先保留；
- 单次最多替换 2 只，最短持有 15 个交易日；
- 新股票评分至少比可替换旧持仓高 `0.05` 才会换入；
- 过滤 ST、退市标识、上市不足 180 日、流动性不足、趋势/回撤过弱和高波动股票；
- 成本模型包含双边佣金与滑点、卖出印花税。参数见 `eastmoney_quant/config.py`。

等权重是目前的基线选择。此前的排名倾斜和逆波动权重没有改善成本后表现，且会带来额外换手；在未证明稳定选股信号前，不继续为权重复杂化调参。

## 数据与 AKShare 元数据

历史日行情通过 AKShare 获取，支持东方财富与腾讯历史行情源。为补齐日线中缺失的关键交易与证券状态字段，新增了按时间缓存的元数据层：

- 终止上市清单：用于检查退市后是否仍有价格记录；
- 停复牌事件：用于识别执行日不可成交情形；
- 历史行业变更：为后续行业暴露/行业中性化准备；
- 历史股本变动：为后续按时点构造历史市值准备。

行业和股本使用“事件生效日”数据，而不是把当前行业或当前市值快照回填到历史。实时行情快照也不应直接用于历史回测。停复牌与行业事件已补齐本地股票池；仍有少量股本事件接口异常，市值中性化保持关闭。

元数据按需、分批更新，避免无控制地请求全市场历史接口：

```bash
# 刷新退市清单
python update_metadata.py --delisting

# 获取指定执行日的停复牌信息
python update_metadata.py --suspension-dates 20260601 20260622

# 从本地交易日历分批回填停复牌；默认每次最多 20 天，可安全重复执行
python update_metadata.py --suspension-from-clean --suspension-limit 20

# 检查停复牌、行业与股本元数据的历史覆盖率
python scripts/metadata_coverage.py

# 预览全量行业变更、股本变动回填计划（不联网）
python backfill_cninfo_metadata.py --dry-run

# 一条命令顺序回填所有剩余股票；默认每 50 只落盘、每次请求间隔 0.5 秒
# 中断后再次执行同一条命令即可续跑
python backfill_cninfo_metadata.py

# 对指定股票补充行业和股本事件
python update_metadata.py --industry-codes 002848 600777 --share-codes 002848 600777 --start-date 20200101 --end-date 20260712
```

元数据缓存在 `data/raw/metadata/`。停复牌与行业回填已覆盖本地股票池，股本回填仍有少量异常代码；行业/市值中性化在股本事件完整前不会参与策略选股或调参。

## 环境安装

推荐使用项目的 Conda 环境：

```powershell
conda activate quant_plan
python -m pip install -r requirements.txt
```

若未将 Conda 加入命令行路径，也可以直接使用：

```powershell
D:\Anaconda\envs\quant_plan\python.exe -m pip install -r requirements.txt
```

运行测试：

```powershell
python -m pytest -q
```

## 运行方式

先用离线样例跑通流程：

```bash
python main.py --sample
```

使用已有缓存重算因子、信号和回测：

```bash
python main.py
```

首次获取或重建真实历史行情：

```bash
python main.py --force-fetch --history-source tencent
```

快速验证少量股票：

```bash
python main.py --force-fetch --codes 000001 600519 000858 --top-n 2
```

若东方财富历史接口不稳定，可显式使用腾讯数据源：

```bash
python main.py --force-fetch --history-source tencent
```

## 日常更新与预测

日常更新不要使用 `--force-fetch`。安全增量更新会回看最近一段日期、合并去重、备份旧缓存并重建策略结果：

```bash
# 先预览更新范围，不修改文件
python update_data.py --dry-run

# 日常安全更新
python update_data.py

# 扩大回看窗口
python update_data.py --lookback-days 30
```

生成最新建议组合并同步更新本地看板：

```bash
python predict.py
```

仅跟踪已记录预测的后续表现：

```bash
python predict.py --track-only
```

## 查看看板与结果

运行 `main.py` 或 `predict.py` 后，直接用浏览器打开 `display/index.html`，即可查看净值、基准、近期调仓和最新建议持仓。看板读取本地生成的 `display/data.js`，不依赖后端服务。

主要结果文件：

- `data/result/performance.json`：策略、基准和相对绩效；
- `data/result/backtest_nav.csv`、`benchmark_nav.csv`：策略与基准净值；
- `data/result/trades.csv`：调仓、换手与成本；
- `data/result/latest_portfolio.csv`：最新建议组合；
- `data/result/prediction_tracking.csv`：历史建议的跟踪收益；
- `data/result/baseline_*.csv`：冻结基线快照；
- `data/result/data_integrity_checks.csv`：数据完整性检查；
- `data/result/factor_rank_ic.csv`：因子诊断；
- `data/result/active_return_attribution.csv`：主动收益归因。
- `data/result/execution_*.csv`：严格订单状态机的可成交性检查；
- `data/result/factor_ablation_*.csv`：预注册 E0～E3 消融实验与训练期 RankIC 结果。

## 已知限制与下一步

- 日线数据无法完整判断涨跌停日的真实成交能力；默认快速回测仍是目标权重近似，不应被视作完整成交模拟。
- 严格执行引擎已实现“挂起、重试、取消和实际成交计费”，但停复牌缓存目前只覆盖抽样日期；在补齐全历史事件前，它只能用于发现执行风险，不能替代完整实盘成交模型。
- 退市清单只能检查现有样本的日期边界，不能替代逐日历史全市场股票池；幸存者偏差尚未完全消除。
- 当前因子在完整诊断中没有显示出稳定、可交易的样本外 Alpha。E0～E3 的验证期优势均未延续到后续已观察研究期；下一步应补齐历史股票池和剩余股本事件，再使用新的未观察数据做滚动 walk-forward 研究。
- 在此之前不启动深度学习或 GPU 训练；复杂模型不能修复数据泄漏、因子方向错误或成交可行性缺失。

## 项目结构

```text
eastmoney_quant_plan/
  eastmoney_quant/       # 数据、因子、信号、回测、预测与元数据模块
  scripts/               # 基线冻结、稳健性验证、因子诊断、消融与执行检查
  data/                  # 原始、处理后与结果数据（默认不提交）
  display/               # 静态策略看板
  docs/                  # 使用说明与策略验证报告
  tests/                 # 单元测试
  main.py                # 主回测入口
  update_data.py         # 日常安全增量更新
  update_metadata.py     # AKShare 元数据更新
  backfill_cninfo_metadata.py # CNInfo 行业/股本慢速可恢复回填
  predict.py             # 最新组合与预测跟踪
```

## 风险提示

本项目仅用于学习和研究，不构成投资建议。真实投资还需考虑数据授权、停复牌、涨跌停、订单簿、滑点、冲击成本、税费、资金容量、风险控制与合规要求。
