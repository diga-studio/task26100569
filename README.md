# attribution

日粒度时间衰减归因库。把每笔转化金额（整数分）按半衰期 1 天的指数衰减权重，
精确分配到 90 天窗口内的触点，再汇总为渠道贡献。原始数据与运行结果均保存在 SQLite。

## 安装与依赖

- Python >= 3.10，依赖 `numpy`（SQLite 使用标准库 `sqlite3`）。
- 直接在项目根目录 `import attribution` 即可，或 `pip install .`。

## 快速开始

```python
import attribution as A

conn = A.connect("attribution.db")          # 打开/创建数据库并建表

# 导入触点：(id, customer, channel, day)
A.import_touchpoints(conn, [
    (1, "c1", "paid_search", "2026-03-10"),
    (2, "c1", "email", "2026-03-08"),
    (3, "c1", "email", "2026-03-08"),
])

# 导入转化：(id, customer, day, amount_cents)
A.import_conversions(conn, [(100, "c1", "2026-03-10", 2)])

# 对 [start, end] 区间内的转化执行归因（两端包含，只筛选转化，不截断历史触点）
run_id = A.run_attribution(conn, "2026-03-10", "2026-03-10")

A.get_conversion_result(conn, run_id, 100)  # 单笔转化的渠道金额
# {'paid_search': 2, 'email': 0, ..., 'unattributed': 0}

A.get_run_summary(conn, run_id)             # 整次运行的渠道汇总（含 unattributed）
A.iter_conversion_results(conn, run_id)     # 逐笔遍历全部转化结果
```

日期参数接受 `datetime.date`、`YYYY-MM-DD` 字符串或整数（`date.toordinal()`）。
渠道固定为 `paid_search`、`organic_search`、`email`、`social`、`direct`、
`referral`、`display_ad`、`webinar`、`content`。

## 归因规则

- 每笔转化仅使用同一客户、转化当日及此前 89 天的触点（共 90 个自然日）。
- 当日触点权重为 1，每早 1 天权重减半（半衰期 1 天）。
- 理想分账 = 金额 × 权重 / 权重和，全程用整数精确运算（权重放大为 2 的幂）。
- 先向下取整到分；剩余金额按理想值小数部分从大到小逐个补 1 分；
  小数部分相等时按触点 ID 升序；每个触点最多补 1 分。
- 渠道金额 = 该渠道全部触点分币后的金额之和（不先汇总权重再分币）。
- 无有效触点的转化，全额记入 `unattributed`（不归入 `direct`）。
- 结果与导入顺序无关，相同数据重复运行结果完全一致。

## 存储结构

- `touchpoints` / `conversions`：导入的原始数据。
- `runs`：每次归因运行（首尾日期、创建时间）。
- `conversion_results`：每笔转化的 9 渠道金额 + `unattributed`，逐分守恒。
- `run_channel_totals`：整次运行的渠道汇总（含 `unattributed`）。

## 测试

```bash
python3 -m unittest discover -s tests -v           # 全部测试
python3 -m unittest tests.test_attribution -v      # 正确性（含 Fraction 精确参考对照）
python3 -m unittest tests.test_performance -v      # 性能：100 万触点 / 10 万转化 / 80% 单客倾斜
```

性能用例会打印导入耗时、归因+保存耗时与进程峰值 RSS，并校验逐笔守恒与汇总一致。
