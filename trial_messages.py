"""试验票转训的站间交接消息协议。

评分站与训练站各自持有独立的 SQLite 库，站间只通过本模块定义的 JSON
对象传送证据。交接方是同套台账中的业务站点，消息可能延迟、重投或丢失
答复；本模块只规定格式与校验，投递可靠性由调用方（如
``trial_exchange_fixture.TrialLink`` 或真实链路）体现。

请求消息（评分站 → 训练站）是一个 JSON 对象，字段如下：

- ``kind``: ``trial_train``（申请转训）、``trial_withdraw``（撤回办理）
  或 ``trial_query``（核对办理，不发起也不撤回转训）。
- ``operation_id``: 办理号。一套台账内绑定一张试验票，与标准秤反馈号
  分别识别，但共用同一条实际补偿及截点序列。
- ``ticket_id`` / ``piece_id``: 试验票及其固定的交件身份，随票固定。
- ``estimate_g`` / ``actual_g``: 原受理预估与同件秤票实重的规范有理
  文本。转训残差按原预估计算（实重减预估），验证则另用原预测。

答复消息（训练站 → 评分站）回显 ``kind`` 与三项身份，并给出：

- ``transfer_state``: ``trained``（转训已提交，``cut_id`` 为真实新截
  点）、``cancelled``（撤回终局，无截点）或 ``unknown``（仅查询答复，
  该办理号在训练站尚无正式结果，查询本身不产生任何写入）。
- ``cut_id``: 仅在 ``trained`` 时为正整数截点号，其余为 ``None``。

业务含义：办理号在训练站首次成立（转训提交或撤回先胜）后即不可更改；
同号同参的重投或迟到请求只领回已确定的终局，同号异参或错绑身份以
``ValueError`` 拒办。评分方对尚未证实的转训或撤回按待决处理，查通后
以答复终局为准更新本地台账；错绑的返回证据不得生效。
"""
from dataclasses import dataclass
from fractions import Fraction

from identifiers import require_identifier
from weighing.units import encode_grams, parse_grams


KIND_TRAIN = 'trial_train'
KIND_WITHDRAW = 'trial_withdraw'
KIND_QUERY = 'trial_query'
REQUEST_KINDS = (KIND_TRAIN, KIND_WITHDRAW, KIND_QUERY)

STATE_TRAINED = 'trained'
STATE_CANCELLED = 'cancelled'
STATE_UNKNOWN = 'unknown'


@dataclass(frozen=True, slots=True)
class TrialMessage:
    """A validated trial exchange request."""

    kind: str
    operation_id: str
    ticket_id: str
    piece_id: str
    estimate_g: Fraction
    actual_g: Fraction


def build_request(
    kind: str,
    operation_id: str,
    ticket_id: str,
    piece_id: str,
    estimate_g: Fraction,
    actual_g: Fraction,
) -> dict:
    """Build the JSON request object for one trial transfer call."""
    if kind not in REQUEST_KINDS:
        raise ValueError(f'unknown trial exchange kind: {kind!r}')
    return {
        'kind': kind,
        'operation_id': require_identifier(operation_id, 'operation_id'),
        'ticket_id': require_identifier(ticket_id, 'ticket_id'),
        'piece_id': require_identifier(piece_id, 'piece_id'),
        'estimate_g': encode_grams(estimate_g),
        'actual_g': encode_grams(actual_g),
    }


def parse_request(message: object) -> TrialMessage:
    """Validate an incoming request object, rejecting illegal exchange calls."""
    if not isinstance(message, dict):
        raise ValueError('trial exchange request must be a JSON object')
    kind = message.get('kind')
    if kind not in REQUEST_KINDS:
        raise ValueError(f'unknown trial exchange kind: {kind!r}')
    return TrialMessage(
        kind=kind,
        operation_id=require_identifier(message.get('operation_id'), 'operation_id'),
        ticket_id=require_identifier(message.get('ticket_id'), 'ticket_id'),
        piece_id=require_identifier(message.get('piece_id'), 'piece_id'),
        estimate_g=parse_grams(message.get('estimate_g')),
        actual_g=parse_grams(message.get('actual_g')),
    )


def build_reply(
    kind: str,
    operation_id: str,
    ticket_id: str,
    piece_id: str,
    transfer_state: str,
    cut_id: int | None,
) -> dict:
    """Build the JSON reply object reporting one operation's transfer state."""
    if kind not in REQUEST_KINDS:
        raise ValueError(f'unknown trial exchange kind: {kind!r}')
    if transfer_state not in (STATE_TRAINED, STATE_CANCELLED, STATE_UNKNOWN):
        raise ValueError(f'unknown trial transfer state: {transfer_state!r}')
    if (transfer_state == STATE_TRAINED) != (cut_id is not None):
        raise ValueError('a trained reply requires a cut and other states forbid one')
    return {
        'kind': kind,
        'operation_id': operation_id,
        'ticket_id': ticket_id,
        'piece_id': piece_id,
        'transfer_state': transfer_state,
        'cut_id': cut_id,
    }


def check_reply(request: dict, reply: object, *, allow_unknown: bool) -> tuple[str, int | None]:
    """Match a reply against its request and return ``(transfer_state, cut_id)``.

    Wrongly bound return evidence is rejected with ``ValueError`` so it can
    neither open validation nor record a new formal use on the scoring side.
    """
    if not isinstance(reply, dict):
        raise ValueError('trial exchange reply must be a JSON object')
    if reply.get('kind') != request.get('kind'):
        raise ValueError('trial exchange reply does not answer the request kind')
    for field in ('operation_id', 'ticket_id', 'piece_id'):
        if reply.get(field) != request.get(field):
            raise ValueError(f'trial exchange reply is bound to another {field}')
    transfer_state = reply.get('transfer_state')
    allowed = (STATE_TRAINED, STATE_CANCELLED, STATE_UNKNOWN) if allow_unknown else (
        STATE_TRAINED, STATE_CANCELLED)
    if transfer_state not in allowed:
        raise ValueError(f'trial exchange reply has an invalid transfer state: {transfer_state!r}')
    cut_id = reply.get('cut_id')
    if transfer_state == STATE_TRAINED:
        if type(cut_id) is not int or not 0 < cut_id < (1 << 63):
            raise ValueError('a trained reply requires a positive integer cut id')
    elif cut_id is not None:
        raise ValueError('an untrained reply must not carry a cut id')
    return transfer_state, cut_id
