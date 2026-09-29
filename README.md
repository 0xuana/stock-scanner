# A 股全市场 MACD Scanner

一个确定性的 Python MVP：从 AkShare 获取 A 股日 K，按股票缓存为 Parquet，在本地生成日、周、月和 45 交易日 K，计算 MACD(10, 20, 8)，最后执行独立筛选规则。

## 安装

建议使用 Python 3.11 或更高版本：

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

## 运行

先用少量股票冒烟测试：

```bash
.venv/bin/a-share-macd-scan --limit 10
```

扫描全市场：

```bash
./run-stock-scanner.sh
```

Shell 脚本会自动切换到项目目录，并透传所有命令行参数，例如：

```bash
./run-stock-scanner.sh --limit 10 --workers 2
```

常用参数：

```text
--start 2018-01-01   首次下载的起始日
--end 2026-09-23     数据截止日
--workers 24         股票处理线程数
--timeout 20         单次历史行情请求超时秒数
--retries 5          单只股票请求尝试次数
--rate 12            所有线程合计每秒最大请求数
--data-source tencent 日 K 数据源，可改为 akshare
--include-st          包含 ST/*ST，默认排除
--log-file PATH      日志文件，默认 logs/scanner.log
```

扫描器使用多线程并发下载、缓存和计算。默认 24 线程、12 请求/秒。若失败股票明显增多，应降低 `--rate`，而不是继续增加线程。

所有运行信息、AkShare 输出、进度、警告和错误都追加到日志文件，终端不直接打印。查看进度：

```bash
tail -f logs/scanner.log
```

单股连续重试失败时，日志只记录一行错误，不输出 traceback；其他股票继续扫描。失败的股票代码、底层异常类型和消息保存在 `results/failures.csv`。

## 扫描流程与数据源

1. 通过 AkShare 一次获取当前全 A 股代码和名称。
2. 默认在请求任何 K 线前，从股票表排除名称含 `ST` 或 `*ST` 的股票。
3. 将非 ST 股票表提交给线程池，各线程检查本地 Parquet 缓存并只请求缺失日期。
4. 沪深日 K 默认使用 `a-stock-data` Skill 推荐的腾讯 K 线，三个入口轮换与冷却。
5. 腾讯不支持北交所历史区间；北交所或腾讯三入口失败时降级到 AkShare，降级请求最多 3 RPS。
6. 从日 K 生成四个 timeframe、计算 MACD、执行筛选并写出结果。

腾讯历史 K 线没有成交额，因此新增腾讯数据的 `amount` 列保留为 null；既有 AkShare 缓存中的成交额不会丢失。MACD 只使用收盘价，不受影响。

腾讯属于低封 IP 风险数据源，但仍有临时限流，不存在可保证“永不限流”的免费公开接口。若要强制使用原 AkShare 数据源：

```bash
./run-stock-scanner.sh --data-source akshare --rate 3
```

## 运行耗时预估

以约 5,364 只非 ST 股票、24 线程、12 请求/秒为参考：

| 场景 | 预计耗时 |
|---|---:|
| 首次运行，从 2010 年回填 | 1～2 小时 |
| 首次运行且发生较多限流或数据源降级 | 2～4 小时 |
| 已有历史缓存，只更新最近交易日 | 8～20 分钟 |
| 缓存已完全最新，只读取 Parquet 并计算筛选 | 2～10 分钟 |

腾讯历史 K 线每次覆盖约 700 个自然日。从 2010 年开始时，每只股票约需 9 次请求：

```text
5,364 只 × 9 次 ≈ 48,000 次请求
48,000 ÷ 12 RPS ≈ 67 分钟理论下限
```

实际还需加上网络延迟、腾讯入口冷却、降级重试、Parquet 读写和 MACD 计算时间。数据目录位于机械硬盘或外置存储时，本地读写阶段可能更慢。

若不需要 2010 年以来的历史，可从 2018 年开始：

```bash
./run-stock-scanner.sh --start 2018-01-01 --workers 24 --rate 12
```

首次运行通常可缩短至约 50～90 分钟。缓存完成后，后续使用相同或更晚的起始日期不会重复下载已缓存的历史。

查看实时进度：

```bash
tail -f logs/scanner.log
```

扫描器每完成 100 只股票会写入一条进度日志：

```text
processed 100/5364 stocks; failures=0
```

可用下列方式粗略估算剩余时间：

```text
预计剩余时间 = 已耗时 ÷ 已完成股票数 × 剩余股票数
```

缓存默认位于项目目录下的 `data/`，每只股票一个 Parquet 文件。可在 `.env` 中设置 `DATA_STORAGE_DIR` 使用其他位置。已有缓存时，仅请求缓存边界之外缺失的前缀或后缀日期范围。若当天不是交易日，AkShare 可能返回空结果，下次运行仍会再次检查该日期范围。

完整结果写入 `results/latest.parquet` 和 `results/latest.csv`；符合默认规则的结果写入 `results/matched.*`；失败股票及错误写入 `results/failures.csv`。单只股票失败不会终止其他股票。

每次扫描还会保留一份带本地生成时间的完整 CSV 归档，例如 `results/latest_20260923-140506.csv`。`latest.csv` 始终指向最新结果，带时间的文件用于保留历次扫描快照。

金叉组合输出：

```text
results/golden_cross_combinations.csv
results/golden_cross_any.csv
results/golden_cross_all_four.csv
results/golden_cross_daily_weekly_monthly_only.csv
results/golden_cross_weekly_monthly_45d_only.csv
```

汇总文件通过 `match_type` 标识分类。“仅日+周+月”要求 45D 未金叉；“仅周+月+45D”要求日线未金叉。

`golden_cross_any.csv` 包含在日线、周线、月线或 45D 任一周期出现最新金叉的股票。列为 `symbol`、`name`、`daily`、`weekly`、`monthly`、`45d`；金叉写 `TRUE`，非金叉留空。

`monthly_golden_weekly_cross_0_4_YYYYMMDD.html` 包含“最新月线柱金叉，周线最近 5 根柱内发生过金叉，且最新价较近三个自然月最低价的涨幅不超过 40%”的股票。文件名、浏览器标题和页面主标题都会显示生成当天的日期。点击任意中文列名可在升序和降序之间切换；点击股票名称会在新标签页打开对应的东方财富全屏行情图。生成报表时，扫描器会请求 `https://quote.eastmoney.com/unify/r/{market}.{code}`，跟随 302 后在最终地址追加 `#fullScreenChart`；单只股票解析失败时回退到统一入口，不会中断报表。“周线金叉距今周数”为 0～4，0 表示当前周柱，4 表示前四根周柱。

近三月价格条件为：

```text
three_month_low_distance_pct = (最新收盘价 - 近三月最低 low) / 近三月最低 low × 100
three_month_low_distance_pct <= 40
```

文件在股票名称后依次输出涨幅百分比、最新收盘价、近三月最低价和该最低价最近一次发生日期。当前月线和周线的 DIF、DEA、`DEA-DIF` 均保留四位小数。`daily_cross_days_before_weekly` 表示日线金叉早于对应周线金叉的自然日数，同一天为 0。

该 HTML 初始先按近三月最低价距离百分比升序排序，再按周线金叉距今周数升序排序，最后按股票代码排序以保证结果稳定。

文件导出集中在 `stock_scanner/export.py`。`export_csv` 负责 CSV，`export_html` 负责可排序网页，`export_scan_outputs` 通过单一接口导出一次扫描的全部文件。

## 默认筛选规则

规则位于 `stock_scanner/filters.py`，当前为：日线金叉，并且周线、月线、45D 的 HIST 都大于零。修改 `DEFAULT_CONDITIONS` 即可组合其他已生成状态。

45D 当前定义为从该股票首条缓存记录开始，每 45 个交易日形成一个非重叠 K 线。实现独立位于 `timeframe.to_n_trading_days`，以后可替换定义，不影响 MACD。

## 每日更新

交易日收盘后手动更新：

```bash
cd /path/to/stock-scanner
./run-stock-scanner.sh
```

脚本会只获取 Parquet 缓存边界之外的缺失日期，再重新计算和导出所有结果。查看进度：

```bash
tail -f logs/scanner.log
```

要在每个工作日 18:30 自动更新，执行 `crontab -e`，加入：

```cron
30 18 * * 1-5 /path/to/stock-scanner/run-stock-scanner.sh
```

检查定时任务：

```bash
crontab -l
```

Shell 脚本会自动切换项目目录，cron 无需额外执行 `cd`。法定休市日仍会启动，但不会产生新交易 K 线。每日更新后主要查看：

```text
results/golden_cross_any.csv
results/monthly_golden_weekly_cross_0_4_YYYYMMDD.html
results/latest.csv
results/latest_YYYYMMDD-HHMMSS.csv
results/failures.csv
```

## 测试

```bash
.venv/bin/pytest
```
