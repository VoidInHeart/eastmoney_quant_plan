# eastmoney_quant 日常使用说明

这份说明面向日常运行，不是实现方案。重点是避免误删或覆盖已经跑好的全量行情缓存。

## 推荐工作流

第一次已经跑完全量后，后续不要频繁使用 `--force-fetch`。日常每隔几天更新一次时，使用安全增量更新：

```bash
python update_data.py
```

它会做这些事：

1. 读取现有 `data/raw/a_share_daily_hfq.parquet`
2. 从旧缓存最新日期往前回看一小段时间，默认 15 个自然日
3. 只下载这段窗口内的数据
4. 和旧缓存按 `code + date` 合并去重
5. 先写临时文件
6. 备份旧缓存到 `data/raw/backup/`
7. 校验成功后再替换原始缓存
8. 重新生成清洗数据、因子、信号、回测报告和最新预测组合

## 先预览，不改文件

正式更新前可以先看它准备更新什么：

```bash
python update_data.py --dry-run
```

快速测试少量股票：

```bash
python update_data.py --dry-run --limit 20
```

## 安全增量更新

默认使用腾讯历史行情源：

```bash
python update_data.py
```

如果只想先更新少量股票测试：

```bash
python update_data.py --limit 50
```

如果想扩大回看窗口，例如最近 30 天：

```bash
python update_data.py --lookback-days 30
```

如果只更新指定股票：

```bash
python update_data.py --codes 000001 600519 000858
```

## 更新后看预测收益

安全更新默认会重算结果并生成一条新的预测记录。你也可以手动查看历史预测到最新数据的收益：

```bash
python predict.py --track-only
```

生成最新建议持仓：

```bash
python predict.py
```

主要输出：

- `data/result/latest_portfolio.csv`
- `data/result/prediction_log.csv`
- `data/result/prediction_tracking.csv`
- `data/result/prediction_summary.json`

## 只更新原始缓存，不重算策略

如果你只想先补行情，不想立刻重算因子和回测：

```bash
python update_data.py --no-rebuild
```

之后再重算：

```bash
python main.py
```

## 备份与恢复

每次安全更新都会把旧缓存备份到：

```text
data/raw/backup/
```

如果新数据有问题，可以手动把备份文件复制回：

```text
data/raw/a_share_daily_hfq.parquet
```

然后重新跑：

```bash
python main.py
python predict.py --track-only
```

## 谨慎使用全量重建

下面这条命令会重新全量下载，不是增量更新：

```bash
python main.py --force-fetch --history-source tencent
```

现在代码会在替换原始缓存前做备份，但它仍然很慢，也不适合日常几天跑一次。只有在你确认要重建整份历史数据时再使用。

## 常用命令速查

```bash
# 查看增量更新计划，不改文件
python update_data.py --dry-run

# 日常安全更新
python update_data.py

# 只补最近 30 天
python update_data.py --lookback-days 30

# 少量股票测试
python update_data.py --limit 50

# 只查看预测收益跟踪
python predict.py --track-only

# 用已有缓存重算策略报告
python main.py
```

## 风险提示

本项目只用于量化研究和学习，不构成投资建议。增量更新可以降低缓存损坏风险，但不能保证数据源永远稳定。真实投资还需要考虑数据授权、停牌、涨跌停、成交滑点、手续费、冲击成本、仓位管理和合规要求。
