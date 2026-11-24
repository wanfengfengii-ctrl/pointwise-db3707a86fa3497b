# 包装线重量影子评估

袋装食品厂的质量班用签发报告评估重量补偿。班头从包装线预估记录登记验证件，训练员以独立标准秤的反馈更新补偿，异地秤台提交验证件实重，夜班核账，次班按报告号复查。

## 运行

从项目根目录用 Python 3.11 运行，无第三方依赖。

```sh
python3 -B check_project.py
python3 -B -m analysis.workday demo
```

`check_project.py` 是控制台提供的完整回归入口。演示用临时站库处理 `samples/workday.json`，再与独立手算的 `samples/expected-report.json` 核对。核对通过后，`POINTWISE_SCENARIO` 一行包含实际输入、两行误差、平均绝对误差和重开领取结果。演示库随运行清理。

初始化自己的站库时使用两个独立路径，父目录应已存在。

```sh
python3 -B -m analysis.workday init training.sqlite3 scoring.sqlite3
```

初始化建立训练站的零截点和两边缺失的 V1 表，保留已有业务数据、编号和回执。站库角色、版本和预期表结构不兼容时显式报错。两站各自提交，初始化可重试。站库路径共同标识一套工作台账，件号和交件身份在该台账内有效。

## 一个班次

1. 班头打开两份站库。空训练库的截点为 0，补偿为 0 g。
2. 班头受理 A，原预估为 10 g，固定原预测 10 g 和截点 0。
3. 训练员提交标准秤反馈，预估 10 g、实重 14 g。训练站发布补偿为 4 g 的截点 1。随后受理同样预估 10 g 的 B，原预测为 14 g。
4. 秤台先登记 A 的实重 11 g。夜班此时查验得到待齐，报告号尚未签发。
5. B 可以先实收预测，再登记 15 g 实测。A 按原受理凭证或交件号补交，沿用已登记的实秤票。
6. 夜班按 B、A 核账。两件分别为 14/15 g 和 10/11 g，逐件误差均为 1 g，平均绝对误差为 1 g。签报和成功回执一起入账。
7. 后续训练发布新截点。次班重开并按号领取原报告、明细顺序和回执，供质量班复核。

## Python 业务接口

| 入口 | 参数和结果 |
| --- | --- |
| `analysis.workday.initialize` | `(training_path, scoring_path)`。训练站持有训练反馈、截点、受理名册及待送记录。评分站持有实收预测、实秤票、冻结报告及回执。 |
| `training.feedback.feedback` | `(training_path, feedback_id, estimate_g, actual_g)`。返回已提交的 `ModelCut`，包含截点号、反馈数、累计残差和补偿。同号同参返还原截点。`training.history.read_cut` 按截点号查历史。 |
| `admission.roster.admit` | `(training_path, piece_id, estimate_g)`。返回 `AdmissionReceipt`，包含原预测、补偿及截点。与反馈共用训练站写入顺序。`admission.history.read_admission` 按件号读取原凭证。 |
| `delivery.dispatch.deliver` | `(training_path, scoring_path, admission_or_delivery_id)`。返回 `DeliveryStatus`，状态为 `received` 或 `pending`。交件号可由 `delivery.records.delivery_id_for` 从原凭证取得。 |
| `delivery.reconcile.delivery_status` | `(training_path, scoring_path, delivery_id)`。核对评分站已经提交的原实收。目标暂不可核对时保持 `pending`，可继续尝试。 |
| `weighing.intake.weigh` | `(scoring_path, ticket_id, piece_id, actual_g)`。返回原 `ActualWeight`，可先于预测入站。相同票号和内容返回原记录，每件最多接受一张有效票。 |
| `analysis.publication.report` | `(scoring_path, report_id, piece_ids)`。返回 `FrozenReport` 或带缺项明细的 `PendingReport`。件号清单非空，保持请求顺序，重复件号按请求位置逐行计入。 |
| `analysis.retrieval.get` | `(scoring_path, report_id)`。返回原 `FrozenReport`，未知号返回 `None`。领取读取冻结明细及原成功回执。 |

所有物理输入用 g，可传递整数、有限 `Decimal`、`Fraction` 或精确数值文本，如 `10.25`、`41/4`。空值、浮点输入、非有限值、零分母和负物理重量均拒绝。实测 0 g 有效。标识必须是非空的可打印文本，无首尾空白。非法请求、同号异参和冲突证据以 `ValueError` 拒绝。站库不兼容以 `StationFormatError` 报告。真实存储失败由 SQLite 异常反映，暂不可核对的目标站交接可以续办。

训练残差等于标准秤实重减训练预估。补偿为已提交且去重的残差总和除以反馈数，空前缀为零。预测等于原预估加首受理补偿。签报行给出实重减原预测的有符号误差，绝对误差的算术平均为报告的 `mae_g`。全程使用 `Fraction`，持久克重以规范有理文本保存。验证票进入评分库，训练通过具名标准秤反馈推进。

## 交接和失败边界

首受理把原预估、训练出处和原预测固定在同一次写入中。投递先核对已提交名册，再持久登记待送，最后由评分站事务实收。源站待送只表示可以发起或续办。评分站核对交件身份、预测口径及同件重投证据，实收凭据对应原提交。待送尚未创建时可凭原交件号从名册恢复。两站分别提交，失答后以可查询的持久结果判断。站间调用由 `deliver` 负责，站内 `accept_delivery` 接收它已从源名册核对的交件。

核账只读评分站的实收预测和同件秤重票。待齐请求可重试。签发事务一并保存报告头、有序明细和成功回执。已签报告同号同请求回放，同号改清单拒绝。公开 `admit`、`deliver` 和 `report` 接受可选关键字参数 `on_boundary`，用于确定性观察事务位置。受理事件为 `after_admission_commit`，实收事件为 `before_receipt_commit` 和 `after_receipt_commit`，签发事件为 `after_report_write` 和 `before_report_commit`。这些回调发出的异常会传给调用者。提交前的异常回滚该次本地写入，提交后的异常可由原号查回结果。

## 回归

- `tests/test_training.py` 覆盖 T1–T2 的零截点、精确分数、反馈重放与冲突、受理重放。
- `tests/test_delivery.py` 覆盖 T3–T5 的原实收、交件冲突、首受理和实收失答、实测先到、零值、错件与冲突票。
- `tests/test_reports.py` 覆盖 T6–T8 的跨截点有序证据、精确 MAE、非法请求、提交回滚、原号重试和重开领取。提交失败回归包含 SQLite 延迟约束导致的实际提交拒绝。
- `tests/test_workday.py` 沿真实接口执行整日样例，核对不兼容库保护与可重入初始化。
