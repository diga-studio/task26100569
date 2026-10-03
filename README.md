# decay_attr — 日粒度时间衰减归因库

把每笔成交金额（整数分）按时间衰减权重分配到触点，再汇总为渠道贡献。
原始数据与运行结果均保存在 SQLite，由上层直接调用。

## 规则

- 每笔转化仅使用同一客户在转化当日及此前 89 天的触点（共 90 个自然日，同日触点全部有效）。
- 转化当日触点权重为 1，每早 1 天减半（半衰期 1 天）。
- 每个有效触点的理想分账 = 转化金额 × 权重 ÷ 权重总和；先向下取整到分，
  剩余金额按理想金额小数部分降序逐个补 1 分，小数部分相等时按触点 ID 升序，
  每个触点最多补 1 分，全部比较基于数学精确值（大整数运算）。
- 同渠道同日期的不同触点是独立分账对象；同一触点可参与多笔转化，不消耗。
- 渠道金额 = 该渠道所有触点分币后的金额之和；无有效触点的转化全部记入 `unattributed`。
- 运行日期区间两端均包含，只筛选转化，不截断历史触点。

## 安装与依赖

Python 3.11+，依赖 `numpy`（测试需 `pytest`）。无外部服务，持久化仅用 SQLite。

## 使用

```python
from datetime import date
from decay_attr import AttributionStore, Touchpoint, Conversion

with AttributionStore("attr.db") as store:
    store.import_touchpoints([
        Touchpoint(id=1, customer="c1", channel="paid_search", date=date(2026, 3, 10)),
        Touchpoint(id=2, customer="c1", channel="email", date=date(2026, 3, 8)),
    ])
    store.import_conversions([
        Conversion(id=100, customer="c1", date=date(2026, 3, 10), amount=2),
    ])

    run_id = store.run_attribution(date(2026, 3, 1), date(2026, 3, 31))

    summary = store.get_run_summary(run_id)
    print(summary.channel_totals, summary.unattributed)

    for result in store.iter_conversion_results(run_id):
        print(result.conversion_id, result.amounts)
```

- 渠道固定为 `paid_search`、`organic_search`、`email`、`social`、`direct`、
  `referral`、`display_ad`、`webinar`、`content`；输入视为已校验。
- 重复导入同一 ID 为覆盖语义；相同数据重复运行或改变导入顺序，结果完全一致。
- 保存内容：每笔转化的各渠道金额与未归因金额（`conversion_results` 表）、
  整次运行的渠道汇总（`run_totals` 表）、运行元信息（`runs` 表）。
  每笔转化与整次运行均逐分守恒。

## 测试

```bash
python3 -m pytest tests/ -q
```

- `tests/test_attribution.py`：正确性用例，含 2 分转化三触点等尾差、窗口第 89/90 天、
  运行起日前触点、同客多次转化、空旅程、导入顺序无关性，以及与独立的
  逐触点展开 Fraction 参考实现的随机对照。
- `tests/test_performance.py`：性能用例（子进程运行 `tests/perf_scenario.py`），
  100 万触点 / 10 万转化 / 90 天 / 80% 集中于同一客户，验证逐笔守恒与汇总一致，
  并断言归因+保存 < 60 秒、峰值内存 < 256 MiB。
  本机实测约 4 秒、约 197 MiB。
