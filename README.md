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
- 已实现最多 10 只、允许留现金、日频风险减仓的自适应小组合候选；它在 2026 YTD 表现较好，但训练、验证与较长已观察期均相对跑输，**未通过验证且不替换基线**。

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

### 自适应小组合候选（研究专用）

该候选针对“最多持有 5～10 只股票”的账户约束设计，独立于默认 Top20 基线：每日收盘计算广度、排名和回撤，周频进行常规换仓；日频操作只允许在“20 日回撤 ≤ -8%、排名恶化”连续出现时减半，不会因为下跌加仓。它根据趋势广度使用 10 / 8 / 5 只股票与 100% / 75% / 50% 的目标仓位，对单票设置 12.5% 上限，新入选股票只先建半仓。

该规则尚未通过跨期验证，只能用于研究，不应输出为实际交易指令。使用本地缓存复现单段检验：

```powershell
& $quantPython scripts/adaptive_candidate_validation.py --start-date 2026-01-05 --end-date 2026-07-31 --tag adaptive_candidate_ytd
```

更多规则、四段验证结果与否定结论见 [策略验证报告](docs/策略验证报告.md#510-只自适应候选研究专用未替换基线)。

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

在 Windows 上，项目的标准解释器是 `D:\Anaconda\envs\quant_plan\python.exe`。运行脚本、测试和安装依赖时都应显式使用它，避免误用系统全局 Python：

```powershell
$quantPython = 'D:\Anaconda\envs\quant_plan\python.exe'
& $quantPython -m pip install -r requirements.txt
& $quantPython -m pytest -q
& $quantPython update_data.py --dry-run
```

也可以先激活同一 Conda 环境，再使用文档中的 `python` 简写：

```powershell
conda activate quant_plan
python -m pip install -r requirements.txt
```

若未将 Conda 加入命令行路径，继续使用上面的 `$quantPython` 变量；等价的单条命令为：

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

### 空仓首次建仓（20 万元）

`update_data.py` 默认只拉取到最近一个已收盘的 A 股交易日（15:30 前会停在前一交易日；周末和节假日按交易日历回退）。本次从空仓建仓时，先在收盘后运行更新：

```bash
python update_data.py
```

更新完成后会生成 `data/result/open_prices_template.csv`。在下一个交易日开盘后，将其中每只股票的 `open_price` 填入实际开盘价，再生成订单：

```bash
python predict.py --orders-only --open-prices-file data/result/open_prices_template.csv
```

如果腾讯实时行情接口可用，可直接拉取今天的开盘价并生成订单：

```bash
python predict.py --orders-only --fetch-live-opens
```

首次建仓的三步前端位于 `display/trading.html`：`#prescreen` 查看每日 Top20 预筛选，`#opening-prices` 录入或核对开盘价并下载订单，`#holdings` 手动录入当前价查看浮动盈亏。实时开盘价、订单和浏览器内录入的数据均保留来源时间或本地浏览器状态，执行前仍应以券商行情和委托回报为准。

订单写入 `data/result/buy_recommendations.csv`，包含建议限价、整手买入数量、预计金额、佣金和剩余额度。首次建仓总资产默认 200,000 元，按入选股票等额分配，不向用户显示权重。名单会按市场广度在 5 / 6 / 8 / 10 只间切换：趋势广度低于 45% 时为 5 只，45%--60% 为 6 只，60%--70% 为 8 只，只有不低于 70% 才扩至 10 只。

默认限价规则为 `min(开盘价 × 1.003, 前收 × 1.03)`，并向下取到 0.01 元价格档；数量按 100 股整手、预留最低 5 元佣金后向下取整。该限价是机械的风险保护，不保证成交；此流程只适用于空仓首次建仓，后续调仓还需要已有持仓与可用现金数据。

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
- `data/result/open_prices_template.csv`、`buy_recommendations.csv`：空仓首次建仓的开盘价输入模板与整手限价订单；
- `data/result/daily_prescreen.csv`、`live_open_prices.csv`：每日 Top20 候选池与腾讯实时开盘价快照；
- `data/result/prediction_tracking.csv`：历史建议的跟踪收益；
- `data/result/baseline_*.csv`：冻结基线快照；
- `data/result/data_integrity_checks.csv`：数据完整性检查；
- `data/result/factor_rank_ic.csv`：因子诊断；
- `data/result/active_return_attribution.csv`：主动收益归因。
- `data/result/execution_*.csv`：严格订单状态机的可成交性检查；
- `data/result/factor_ablation_*.csv`：预注册 E0～E3 消融实验与训练期 RankIC 结果。
- `data/result/adaptive_candidate_*.csv/json`：5～10 只自适应候选的净值、交易、目标与汇总（研究专用，未通过验证）。

## 已知限制与下一步

- 日线数据无法完整判断涨跌停日的真实成交能力；默认快速回测仍是目标权重近似，不应被视作完整成交模拟。
- 严格执行引擎已实现“挂起、重试、取消和实际成交计费”，但停复牌缓存目前只覆盖抽样日期；在补齐全历史事件前，它只能用于发现执行风险，不能替代完整实盘成交模型。
- 退市清单只能检查现有样本的日期边界，不能替代逐日历史全市场股票池；幸存者偏差尚未完全消除。
- 当前因子在完整诊断中没有显示出稳定、可交易的样本外 Alpha。E0～E3 的验证期优势均未延续到后续已观察研究期；下一步应补齐历史股票池和剩余股本事件，再使用新的未观察数据做滚动 walk-forward 研究。
- 自适应小组合候选同样没有通过训练、验证和较长已观察期；不能因为 2026 YTD 单段较好而启用，必须等待新的未观察数据再作检验。
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
