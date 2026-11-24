"""试验票交接演示：转训失答与撤回先胜两条交接。

每条场景从独立的空站库开始，按原入口受理 A（原预估、原预测均为 10 g），
实收原预测并登记实秤 W-A 为 14 g，再登记为试验票。演示用
``trial_exchange_fixture.TrialLink`` 包住训练入口，实际办理交接的延迟、
失答与补送，并展示原截点或原报告及首执。
"""
import json
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory

from admission.roster import admit
from analysis.publication import (
    register_trial,
    request_training,
    sign_trial,
    trial_status,
    withdraw_training,
)
from analysis.retrieval import get
from analysis.workday import initialize
from delivery.dispatch import deliver
from training.feedback import exchange_trial
from training.history import read_cut
from trial_exchange_fixture import TrialLink
from trial_messages import KIND_WITHDRAW
from weighing.intake import weigh
from weighing.units import encode_grams


def _register_trial_a(training_path, scoring_path, link):
    admission = admit(training_path, 'A', 10)
    assert admission.prediction_g == 10, admission
    assert deliver(training_path, scoring_path, admission).state == 'received'
    weigh(scoring_path, 'W-A', 'A', 14)
    status = register_trial(scoring_path, 'A', 'W-A', link)
    assert status['state'] == 'eligible', status
    return status


def _sign_blocked(scoring_path, ticket_id, report_id):
    try:
        sign_trial(scoring_path, ticket_id, report_id)
    except ValueError:
        return True
    return False


def training_reply_lost_demo():
    """转训已提交、评分方失答：重开后从原号重投或撤回，取得原截点 1。"""
    with TemporaryDirectory(prefix='trial-lost-') as folder:
        training_path = Path(folder) / 'training.sqlite3'
        scoring_path = Path(folder) / 'scoring.sqlite3'
        initialize(training_path, scoring_path)
        link = TrialLink(partial(exchange_trial, training_path))
        _register_trial_a(training_path, scoring_path, link)

        link.arm('lose_reply')
        pending = request_training(scoring_path, 'W-A', 'OP-1', link)
        assert pending['state'] == 'pending' and pending['transfer_state'] == 'pending', pending
        cut = read_cut(training_path, 1)
        assert cut is not None and cut.compensation_g == 4, cut
        blocked_before = _sign_blocked(scoring_path, 'W-A', 'V-1')
        assert blocked_before, 'unconfirmed training must block signing'

        initialize(training_path, scoring_path)
        redelivered = request_training(scoring_path, 'W-A', 'OP-1', link)
        assert redelivered['state'] == 'trained' and redelivered['cut_id'] == 1, redelivered
        withdrawn = withdraw_training(scoring_path, 'W-A', 'OP-1', link)
        assert withdrawn['transfer_state'] == 'trained' and withdrawn['cut_id'] == 1, withdrawn
        blocked_after = _sign_blocked(scoring_path, 'W-A', 'V-1')
        assert blocked_after, 'a trained ticket stays unsignable'
        next_prediction = admit(training_path, 'B', 10).prediction_g
        assert next_prediction == 14, next_prediction
        return {
            'scenario': 'training-reply-lost',
            'state_after_loss': pending['state'],
            'sign_blocked': blocked_before and blocked_after,
            'redelivered_cut_id': redelivered['cut_id'],
            'withdraw_redelivery_cut_id': withdrawn['cut_id'],
            'compensation_g': encode_grams(read_cut(training_path, 1).compensation_g),
            'next_prediction_g': encode_grams(next_prediction),
        }


def withdrawal_first_demo():
    """转训未达、撤回先在裁定端生效且答复丢失：核对后签原票，补送不发布截点。"""
    with TemporaryDirectory(prefix='trial-withdraw-') as folder:
        training_path = Path(folder) / 'training.sqlite3'
        scoring_path = Path(folder) / 'scoring.sqlite3'
        initialize(training_path, scoring_path)
        link = TrialLink(partial(exchange_trial, training_path))
        _register_trial_a(training_path, scoring_path, link)

        link.arm('hold')
        pending = request_training(scoring_path, 'W-A', 'OP-1', link)
        assert pending['transfer_state'] == 'pending' and link.held_count == 1, pending
        link.arm('lose_reply', lambda message: message.get('kind') == KIND_WITHDRAW)
        still_pending = withdraw_training(scoring_path, 'W-A', 'OP-1', link)
        assert still_pending['transfer_state'] == 'pending', still_pending
        blocked = _sign_blocked(scoring_path, 'W-A', 'V-1')
        assert blocked, 'an unconfirmed withdrawal must block signing'

        initialize(training_path, scoring_path)
        checked = trial_status(scoring_path, 'W-A', link)
        assert checked['state'] == 'eligible' and checked['transfer_state'] == 'cancelled', checked
        signed = sign_trial(scoring_path, 'W-A', 'V-1')
        row = signed.summary.rows[0]
        assert row.signed_error_g == 4 and signed.summary.mae_g == 4, row
        assert get(scoring_path, 'V-1') == signed

        late_reply = link.deliver_held()
        assert late_reply['transfer_state'] == 'cancelled' and late_reply['cut_id'] is None, late_reply
        cut_1_exists = read_cut(training_path, 1) is not None
        assert not cut_1_exists, 'the late training request must not publish a cut'
        again = request_training(scoring_path, 'W-A', 'OP-1', link)
        assert again['transfer_state'] == 'cancelled', again
        final = trial_status(scoring_path, 'W-A')
        assert final['state'] == 'validated' and final['transfer_state'] == 'cancelled', final
        return {
            'scenario': 'withdrawal-first',
            'sign_blocked': blocked,
            'report_id': signed.report_id,
            'receipt_no': signed.receipt.receipt_no,
            'signed_error_g': encode_grams(row.signed_error_g),
            'mae_g': encode_grams(signed.summary.mae_g),
            'late_train_reply': late_reply['transfer_state'],
            'cut_1_exists': cut_1_exists,
            'redelivery_state': again['transfer_state'],
            'final_state': final['state'],
            'held_requests': link.held_count,
        }


def run_demo():
    """Run both handoff scenarios on isolated station stores and verify them."""
    return {
        'scenarios': [training_reply_lost_demo(), withdrawal_first_demo()],
    }


def main(argv=None):
    print('TRIAL_EXCHANGE ' + json.dumps(run_demo(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
