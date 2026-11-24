# 试验秤票转训工单

包装线的标准秤反馈和验证实秤原先各走一条链路。质量班现在可以把个别正式登记的试验票转作补偿训练。训练采用原受理的预估，验证采用同次受理的原预测。秤票上的实重用于所选的一种用途。各站继续使用各自的 SQLite 库，站间通过交接调用传送证据。

## 调用

评分站在 `analysis.publication` 提供以下调用。

```python
register_trial(scoring_path, piece_id, ticket_id, exchange)
request_training(scoring_path, ticket_id, operation_id, exchange)
withdraw_training(scoring_path, ticket_id, operation_id, exchange)
trial_status(scoring_path, ticket_id, exchange=None)
sign_trial(scoring_path, ticket_id, report_id, *, on_boundary=None)
```

训练站在 `training.feedback` 提供 `exchange_trial(training_path, message, *, on_boundary=None)`。交接调用 `exchange(message)` 接收 JSON 对象并返回 JSON 对象。训练入口只访问训练库，评分入口只访问评分库。按实现说明交接消息与答复的格式及业务含义。交接方是同套台账中的业务站点，可能延迟、重投或丢失答复。`ConnectionError` 表示本次调用未交回事务结果。评分方须按待决处理尚未证实的转训或撤回，查通后可核对实际结果。可用 `functools.partial(exchange_trial, training_path)` 装配正常调用。

登记时要有指定件的正式预测实收和同件原票，两者身份随票固定。同一票可以重复登记以领取状态。票已被普通报告计入时拒绝登记。普通 `report` 拒绝新签含已登记试验票的请求，包括与其他票混签。普通票的报告规则、旧报告及领取保持原样。标准秤反馈是独立输入，原反馈号继续使用原有规则。转训与其共用实际补偿及截点序列，分别按办理号与反馈号识别。

前四个评分调用返回字典，含 `piece_id`、`ticket_id`、`state`、`operation_id`、`transfer_state`、`report_id`、`receipt_no`、`cut_id`。`state` 为 `eligible`、`pending`、`validated` 或 `trained`。`eligible` 可签，`pending` 不可签。初次登记在正常交接下应返回可签。`operation_id` 为该票已申请的办理号，尚未申请时为空。`transfer_state` 随该办理分别为 `pending`、`cancelled`、`trained`，尚未申请时为空。`report_id` 与 `receipt_no` 仅在已验证时有值，`cut_id` 仅在已转训时有值。空字段用 `None`。

办理号在一套台账内绑定一张试验票。已经签发验证的票不可首次申请转训。每票仅可申请一次，撤回后原号可续查，换号不重开。撤回操作针对已申请的同一办理。`trial_status` 无交接调用时读评分站已知值，提供调用时可核对该次办理，查询本身不发起或撤回一次转训。重投原办理须沿用已确定的转训结果，或者沿用撤回终局。撤回后再签验证时，票状态为 `validated`，原办理仍为 `cancelled`。

`sign_trial` 使用原单件误差及首执口径，成功返回 `FrozenReport`。沿用原号同参复领和同号异参拒绝规则，`analysis.retrieval.get` 可按报告号领取成功报告。一张试验票成功验证后，其他报告号不得再签。非法参数、缺原据、错票、冲突身份、改参或非法用途操作报 `ValueError`。未确认的转训期间调用签报也报 `ValueError`。非法交接请求或错绑的返回证据不得开放验证或产生新的正式用途结果。

验证消费与报告、首执同次生效。转训消费与原票反馈、真实新截点同次生效。撤回若先成立，迟到的原转训请求也不能再发布截点，评分站核对撤回结果后可签原票。若训练已提交，撤回和重投只可返回原训练截点，哪怕训练站的回复曾经丢失。失败提交不留本次的新正式结果。仍用 `analysis.workday.initialize` 初始化或重开原有两站库。

`exchange_trial` 在首次写入转训正式结果之后、提交之前可观察 `on_boundary('before_training_commit')`。`sign_trial` 在报告及首执写入后、提交前可观察 `on_boundary('before_report_commit')`。这两个边界抛出的异常向上传递，并回滚本次写入。提交成功后的交接失答可由夹具控制。

## 联调样例

每条主场景从独立的空站库开始。按原入口受理 A，原预估和原预测均为 10 g，实收原预测并登记实秤 W-A 为 14 g，再登记为试验票。`trial_exchange_fixture.TrialLink` 可包住训练入口。`arm('hold')` 保存下一条匹配消息且当次不给答复，`deliver_held()` 补送最早的延迟请求并把实际答复返回给演示驱动。`arm('lose_reply')` 让训练端办理后丢掉应答。`arm('unavailable')` 使该条消息未送达。`arm` 可传消息判定函数，以选中交接中的特定调用。

- 验证先签。A 的验证误差为 4 g，可按原号领相同首执。转训拒办，下一件以 10 g 预估受理仍预测 10 g。
- 转训已提交、评分方失答。办理号的训练发布为截点 1，补偿为 4 g。评分方尚未查明时不可签 A。重开从原号重投或撤回，取得原截点 1。A 保持不可签，下一件以 10 g 预估受理预测 14 g。
- 转训消息未达，撤回先在裁定端生效，且撤回答复丢失。评分方查回终局前仍待决。重开并核对后可签 A，误差为 4 g。此时补送原转训请求，截点 1 仍不存在，同号重投仍为原撤回结果。
- 分别在报告与转训的提交前使写入失败。已签旧报告保持可读，本次报告或新截点不存在，原票按已持久化的资格继续办理。另用错票、同号异参和普通签报入口尝试绕行，核对拒办后没有新正式结果。

若 A 受理前已有一条残差为 2 g 的标准秤反馈，A 的原预估仍为 10 g，原预测变为 12 g。实秤为 14 g 时转训残差应为 4 g，下一截点的两条反馈平均补偿为 3 g，下一件以 10 g 预估受理预测 13 g。

## 运行

保留 `python3 -B check_project.py` 和 `python3 -B -m analysis.workday demo`。接通根目录的 `.pointwise-runtime.json`，沿用当前版本、类型和安装命令列表格式。将核验及演示命令各写成参数数组。核验包含原回归及试验票业务断言，断言失败须退出非零。演示用独立站库实际办理转训失答与撤回先胜两条交接，展示原截点或原报告及首执，以及补送结果。旧工作日演示仍可按原命令单独运行。
