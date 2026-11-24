from fractions import Fraction
from functools import partial

from admission.roster import admit
from analysis.publication import (
    register_trial,
    report,
    request_training,
    sign_trial,
    trial_status,
    withdraw_training,
)
from analysis.retrieval import FrozenReport, get
from analysis.workday import initialize
from delivery.dispatch import deliver
from delivery.store import open_scoring_store
from tests.support import InterruptedReply, StationCase, interrupt_at
from training.feedback import exchange_trial, feedback
from training.history import read_cut
from training.store import open_training_store
from trial_exchange_fixture import TrialLink
from trial_messages import KIND_TRAIN, KIND_WITHDRAW
from weighing.intake import weigh


class TrialCase(StationCase):
    def setUp(self):
        super().setUp()
        self.link = TrialLink(partial(exchange_trial, self.training))

    def register_piece(self, piece_id='A', ticket_id='W-A', estimate_g=10, actual_g=14):
        admission = admit(self.training, piece_id, estimate_g)
        self.assertEqual(deliver(self.training, self.scoring, admission).state, 'received')
        weigh(self.scoring, ticket_id, piece_id, actual_g)
        status = register_trial(self.scoring, piece_id, ticket_id, self.link)
        self.assertEqual(status, {
            'piece_id': piece_id,
            'ticket_id': ticket_id,
            'state': 'eligible',
            'operation_id': None,
            'transfer_state': None,
            'report_id': None,
            'receipt_no': None,
            'cut_id': None,
        })
        return admission


class ValidateFirstTests(TrialCase):
    def test_validate_first_signs_and_blocks_training(self):
        self.register_piece()
        signed = sign_trial(self.scoring, 'W-A', 'V-1')
        self.assertIsInstance(signed, FrozenReport)
        self.assertEqual(signed.piece_ids, ('A',))
        row = signed.summary.rows[0]
        self.assertEqual(
            (row.ticket_id, row.prediction_g, row.actual_g),
            ('W-A', Fraction(10), Fraction(14)),
        )
        self.assertEqual(
            (row.signed_error_g, row.absolute_error_g, signed.summary.mae_g),
            (Fraction(4), Fraction(4), Fraction(4)),
        )
        self.assertEqual(signed.receipt.receipt_no, 1)

        status = trial_status(self.scoring, 'W-A')
        self.assertEqual(status['state'], 'validated')
        self.assertEqual((status['report_id'], status['receipt_no']), ('V-1', 1))
        self.assertIsNone(status['cut_id'])

        # the original number re-claims the same first receipt; other numbers refused
        self.assertEqual(sign_trial(self.scoring, 'W-A', 'V-1'), signed)
        self.assertEqual(get(self.scoring, 'V-1'), signed)
        with self.assertRaises(ValueError):
            sign_trial(self.scoring, 'W-A', 'V-2')
        # a validated ticket cannot apply for training or be withdrawn
        with self.assertRaises(ValueError):
            request_training(self.scoring, 'W-A', 'OP-1', self.link)
        with self.assertRaises(ValueError):
            withdraw_training(self.scoring, 'W-A', 'OP-1', self.link)
        # re-registration claims the same status
        self.assertEqual(register_trial(self.scoring, 'A', 'W-A', self.link)['state'], 'validated')
        # no training cut was published; the next piece still predicts 10
        self.assertIsNone(read_cut(self.training, 1))
        self.assertEqual(admit(self.training, 'B', 10).prediction_g, Fraction(10))

    def test_sign_trial_observes_only_before_report_commit(self):
        self.register_piece()
        seen = []
        signed = sign_trial(self.scoring, 'W-A', 'V-1', on_boundary=seen.append)
        self.assertEqual(seen, ['before_report_commit'])
        self.assertEqual(get(self.scoring, 'V-1'), signed)


class TrainingReplyLostTests(TrialCase):
    def begin_lost_training(self):
        self.register_piece()
        self.link.arm('lose_reply')
        pending = request_training(self.scoring, 'W-A', 'OP-1', self.link)
        self.assertEqual(pending['state'], 'pending')
        self.assertEqual(pending['transfer_state'], 'pending')
        self.assertEqual(pending['operation_id'], 'OP-1')
        # the training station actually committed cut 1 with compensation 4 g
        cut = read_cut(self.training, 1)
        self.assertEqual((cut.cut_id, cut.compensation_g), (1, Fraction(4)))
        # unconfirmed: the ticket cannot be signed and stays pending locally
        with self.assertRaises(ValueError):
            sign_trial(self.scoring, 'W-A', 'V-1')
        self.assertIsNone(get(self.scoring, 'V-1'))
        self.assertEqual(trial_status(self.scoring, 'W-A')['state'], 'pending')
        initialize(self.training, self.scoring)

    def test_redeliver_recovers_original_cut(self):
        self.begin_lost_training()
        settled = request_training(self.scoring, 'W-A', 'OP-1', self.link)
        self.assertEqual(
            (settled['state'], settled['transfer_state'], settled['cut_id']),
            ('trained', 'trained', 1),
        )
        # withdrawing the same operation also returns the original cut
        withdrawn = withdraw_training(self.scoring, 'W-A', 'OP-1', self.link)
        self.assertEqual((withdrawn['transfer_state'], withdrawn['cut_id']), ('trained', 1))
        # A stays unsignable; the next piece admitted at 10 g predicts 14 g
        with self.assertRaises(ValueError):
            sign_trial(self.scoring, 'W-A', 'V-1')
        self.assertEqual(admit(self.training, 'B', 10).prediction_g, Fraction(14))

    def test_withdraw_recovers_original_cut(self):
        self.begin_lost_training()
        settled = withdraw_training(self.scoring, 'W-A', 'OP-1', self.link)
        self.assertEqual(
            (settled['state'], settled['transfer_state'], settled['cut_id']),
            ('trained', 'trained', 1),
        )
        with self.assertRaises(ValueError):
            sign_trial(self.scoring, 'W-A', 'V-1')
        self.assertEqual(admit(self.training, 'B', 10).prediction_g, Fraction(14))


class WithdrawalFirstTests(TrialCase):
    def test_withdrawal_wins_before_late_train(self):
        self.register_piece()
        # the training request is held and never answered this time
        self.link.arm('hold')
        pending = request_training(self.scoring, 'W-A', 'OP-1', self.link)
        self.assertEqual(pending['transfer_state'], 'pending')
        self.assertEqual(self.link.held_count, 1)
        self.assertIsNone(read_cut(self.training, 1))
        # the withdrawal reaches the training station first; its reply is lost
        self.link.arm('lose_reply', lambda message: message.get('kind') == KIND_WITHDRAW)
        still_pending = withdraw_training(self.scoring, 'W-A', 'OP-1', self.link)
        self.assertEqual(still_pending['transfer_state'], 'pending')
        with self.assertRaises(ValueError):
            sign_trial(self.scoring, 'W-A', 'V-1')
        # reopen and verify the settled withdrawal before signing
        initialize(self.training, self.scoring)
        checked = trial_status(self.scoring, 'W-A', self.link)
        self.assertEqual((checked['state'], checked['transfer_state']), ('eligible', 'cancelled'))
        signed = sign_trial(self.scoring, 'W-A', 'V-1')
        self.assertEqual(signed.summary.rows[0].signed_error_g, Fraction(4))
        self.assertEqual(get(self.scoring, 'V-1'), signed)
        # the late original training request cannot publish a cut
        late_reply = self.link.deliver_held()
        self.assertEqual(late_reply['transfer_state'], 'cancelled')
        self.assertIsNone(late_reply['cut_id'])
        self.assertIsNone(read_cut(self.training, 1))
        # same-number redelivery keeps the original withdrawal result
        again = request_training(self.scoring, 'W-A', 'OP-1', self.link)
        self.assertEqual(
            (again['state'], again['transfer_state'], again['cut_id']),
            ('validated', 'cancelled', None),
        )
        status = trial_status(self.scoring, 'W-A')
        self.assertEqual((status['state'], status['transfer_state']), ('validated', 'cancelled'))
        self.assertEqual((status['report_id'], status['receipt_no']), ('V-1', 1))


class CommitFailureTests(TrialCase):
    def test_report_commit_failure_rolls_back_and_preserves_old_reports(self):
        self.complete_piece('C', 10, 11)
        old = report(self.scoring, 'old-night', ['C'])
        self.register_piece()
        with self.assertRaisesRegex(InterruptedReply, '^before_report_commit$'):
            sign_trial(
                self.scoring, 'W-A', 'V-1',
                on_boundary=interrupt_at('before_report_commit'),
            )
        self.assertIsNone(get(self.scoring, 'V-1'))
        with open_scoring_store(self.scoring) as db:
            for table in ('reports', 'report_rows', 'publication_receipts'):
                self.assertEqual(
                    db.execute(
                        f'SELECT count(*) FROM {table} WHERE report_id = ?', ('V-1',)
                    ).fetchone()[0],
                    0,
                )
            self.assertIsNone(
                db.execute(
                    'SELECT report_id FROM trial_tickets WHERE ticket_id = ?', ('W-A',)
                ).fetchone()[0]
            )
        # the old signed report stays readable; the ticket keeps its eligibility
        self.assertEqual(get(self.scoring, 'old-night'), old)
        self.assertEqual(trial_status(self.scoring, 'W-A')['state'], 'eligible')
        signed = sign_trial(self.scoring, 'W-A', 'V-1')
        self.assertEqual(signed.summary.mae_g, Fraction(4))
        self.assertEqual(get(self.scoring, 'V-1'), signed)
        self.assertEqual(signed.receipt.receipt_no, old.receipt.receipt_no + 1)

    def test_training_commit_failure_rolls_back_and_allows_retry(self):
        self.register_piece()
        failing = TrialLink(partial(
            exchange_trial, self.training,
            on_boundary=interrupt_at('before_training_commit'),
        ))
        with self.assertRaisesRegex(InterruptedReply, '^before_training_commit$'):
            request_training(self.scoring, 'W-A', 'OP-1', failing)
        # the failed submission left no cut and no formal result
        self.assertIsNone(read_cut(self.training, 1))
        with open_training_store(self.training) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM trial_transfers').fetchone()[0], 0)
        status = trial_status(self.scoring, 'W-A')
        self.assertEqual((status['state'], status['transfer_state']), ('pending', 'pending'))
        # a query finds no decision at the training station and stays pending
        self.assertEqual(trial_status(self.scoring, 'W-A', self.link)['transfer_state'], 'pending')
        # the ticket continues per its persisted state and the retry succeeds once
        settled = request_training(self.scoring, 'W-A', 'OP-1', self.link)
        self.assertEqual((settled['transfer_state'], settled['cut_id']), ('trained', 1))
        self.assertEqual(read_cut(self.training, 1).compensation_g, Fraction(4))
        self.assertIsNone(read_cut(self.training, 2))

    def test_bypass_attempts_leave_no_formal_result(self):
        self.register_piece()
        # wrong or unknown tickets
        for bad_ticket in ('W-Z', '', ' W-A'):
            with self.subTest(bad_ticket=bad_ticket), self.assertRaises(ValueError):
                sign_trial(self.scoring, bad_ticket, 'V-1')
            with self.subTest(bad_ticket=bad_ticket), self.assertRaises(ValueError):
                trial_status(self.scoring, bad_ticket)
        # same report id under another request
        self.complete_piece('C', 10, 11)
        report(self.scoring, 'normal-1', ['C'])
        with self.assertRaises(ValueError):
            sign_trial(self.scoring, 'W-A', 'normal-1')
        # the normal signing entry refuses the registered trial ticket, even mixed
        with self.assertRaises(ValueError):
            report(self.scoring, 'sneak', ['C', 'A'])
        with self.assertRaises(ValueError):
            report(self.scoring, 'sneak', ['A'])
        self.assertIsNone(get(self.scoring, 'sneak'))
        # changed operation numbers do not reopen the ticket
        trained = request_training(self.scoring, 'W-A', 'OP-1', self.link)
        self.assertEqual((trained['state'], trained['cut_id']), ('trained', 1))
        with self.assertRaises(ValueError):
            request_training(self.scoring, 'W-A', 'OP-2', self.link)
        with self.assertRaises(ValueError):
            withdraw_training(self.scoring, 'W-A', 'OP-2', self.link)
        # the refusals produced no new formal result
        self.assertIsNone(get(self.scoring, 'V-1'))
        self.assertIsNone(read_cut(self.training, 2))
        self.assertEqual(trial_status(self.scoring, 'W-A')['state'], 'trained')


class TrialResidualTests(TrialCase):
    def test_training_residual_uses_the_original_estimate(self):
        feedback(self.training, 'R1', 10, 12)
        admission = admit(self.training, 'A', 10)
        self.assertEqual((admission.cut_id, admission.prediction_g), (1, Fraction(12)))
        self.assertEqual(deliver(self.training, self.scoring, admission).state, 'received')
        weigh(self.scoring, 'W-A', 'A', 14)
        self.assertEqual(register_trial(self.scoring, 'A', 'W-A', self.link)['state'], 'eligible')
        trained = request_training(self.scoring, 'W-A', 'OP-1', self.link)
        self.assertEqual((trained['state'], trained['cut_id']), ('trained', 2))
        cut = read_cut(self.training, 2)
        self.assertEqual(
            (cut.feedback_count, cut.residual_total_g, cut.compensation_g),
            (2, Fraction(6), Fraction(3)),
        )
        self.assertEqual(admit(self.training, 'B', 10).prediction_g, Fraction(13))


class TrialRegistrationTests(TrialCase):
    def test_registration_requires_received_prediction_and_same_piece_ticket(self):
        admission = admit(self.training, 'A', 10)
        weigh(self.scoring, 'W-A', 'A', 14)
        # the original prediction has not been received yet
        with self.assertRaises(ValueError):
            register_trial(self.scoring, 'A', 'W-A', self.link)
        deliver(self.training, self.scoring, admission)
        # the piece has no scale ticket
        b = admit(self.training, 'B', 10)
        deliver(self.training, self.scoring, b)
        with self.assertRaises(ValueError):
            register_trial(self.scoring, 'B', 'W-B', self.link)
        # the scale ticket belongs to another piece
        with self.assertRaises(ValueError):
            register_trial(self.scoring, 'B', 'W-A', self.link)
        # success is idempotent per ticket
        first = register_trial(self.scoring, 'A', 'W-A', self.link)
        self.assertEqual(first['state'], 'eligible')
        self.assertEqual(register_trial(self.scoring, 'A', 'W-A', self.link), first)
        # conflicting identities are refused
        with self.assertRaises(ValueError):
            register_trial(self.scoring, 'B', 'W-A', self.link)
        weigh(self.scoring, 'W-B', 'B', 15)
        register_trial(self.scoring, 'B', 'W-B', self.link)
        with self.assertRaises(ValueError):
            register_trial(self.scoring, 'A', 'W-B', self.link)

    def test_ticket_counted_in_a_normal_report_cannot_register(self):
        self.complete_piece('A', 10, 11)
        signed = report(self.scoring, 'night-1', ['A'])
        with self.assertRaises(ValueError):
            register_trial(self.scoring, 'A', 'W-A', self.link)
        # the old report and its retrieval stay as they were
        self.assertEqual(get(self.scoring, 'night-1'), signed)
        self.assertEqual(report(self.scoring, 'night-1', ['A']), signed)


class TrialOperationBindingTests(TrialCase):
    def test_operation_binds_one_ticket_and_one_attempt(self):
        self.register_piece('A', 'W-A')
        self.register_piece('B', 'W-B', actual_g=15)
        with self.assertRaises(ValueError):
            request_training(self.scoring, 'W-Z', 'OP-1', self.link)
        # withdrawal targets an already applied operation
        with self.assertRaises(ValueError):
            withdraw_training(self.scoring, 'W-A', 'OP-1', self.link)
        trained = request_training(self.scoring, 'W-A', 'OP-1', self.link)
        self.assertEqual((trained['state'], trained['cut_id']), ('trained', 1))
        # the operation id cannot be bound to another ticket
        with self.assertRaises(ValueError):
            request_training(self.scoring, 'W-B', 'OP-1', self.link)
        # changing the number does not reopen the ticket
        with self.assertRaises(ValueError):
            request_training(self.scoring, 'W-A', 'OP-2', self.link)
        with self.assertRaises(ValueError):
            withdraw_training(self.scoring, 'W-A', 'OP-2', self.link)
        # the same number claims the original result
        again = request_training(self.scoring, 'W-A', 'OP-1', self.link)
        self.assertEqual((again['transfer_state'], again['cut_id']), ('trained', 1))

    def test_training_boundary_fires_only_on_first_formal_write(self):
        self.register_piece('A', 'W-A')
        self.register_piece('B', 'W-B', actual_g=15)
        seen = []
        link = TrialLink(partial(exchange_trial, self.training, on_boundary=seen.append))
        self.assertEqual(request_training(self.scoring, 'W-A', 'OP-1', link)['cut_id'], 1)
        self.assertEqual(seen, ['before_training_commit'])
        # replays of an established operation write nothing new
        request_training(self.scoring, 'W-A', 'OP-1', link)
        self.assertEqual(seen, ['before_training_commit'])
        # a withdrawal can be the first formal write of its operation
        link.arm('unavailable')
        request_training(self.scoring, 'W-B', 'OP-2', link)
        self.assertEqual(seen, ['before_training_commit'])
        withdrawn = withdraw_training(self.scoring, 'W-B', 'OP-2', link)
        self.assertEqual(withdrawn['transfer_state'], 'cancelled')
        self.assertEqual(seen, ['before_training_commit', 'before_training_commit'])
        withdraw_training(self.scoring, 'W-B', 'OP-2', link)
        self.assertEqual(len(seen), 2)


class ExchangeMessageTests(TrialCase):
    def legal_request(self):
        return {
            'kind': KIND_TRAIN, 'operation_id': 'OP-1', 'ticket_id': 'W-A',
            'piece_id': 'A', 'estimate_g': '10', 'actual_g': '14',
        }

    def test_illegal_exchange_requests_are_rejected(self):
        self.register_piece()
        legal = self.legal_request()
        broken_messages = [
            None, [], 'trial', {},
            {**legal, 'kind': 'trial_hack'},
            {**legal, 'operation_id': ''},
            {**legal, 'ticket_id': ' W-A'},
            {**legal, 'estimate_g': '1/0'},
            {**legal, 'actual_g': '-1'},
            {**legal, 'actual_g': 1.5},
            {key: value for key, value in legal.items() if key != 'piece_id'},
        ]
        for broken in broken_messages:
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                exchange_trial(self.training, broken)
        # nothing was recorded and no cut was published
        self.assertIsNone(read_cut(self.training, 1))
        with open_training_store(self.training) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM trial_transfers').fetchone()[0], 0)

    def test_conflicting_transfer_params_are_rejected(self):
        self.register_piece()
        self.assertEqual(request_training(self.scoring, 'W-A', 'OP-1', self.link)['state'], 'trained')
        base = self.legal_request()
        for changed in (
            {**base, 'ticket_id': 'W-B'},
            {**base, 'piece_id': 'B'},
            {**base, 'estimate_g': '11'},
            {**base, 'actual_g': '15'},
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                exchange_trial(self.training, changed)
        # the original result still replays and no extra cut exists
        reply = exchange_trial(self.training, base)
        self.assertEqual((reply['transfer_state'], reply['cut_id']), ('trained', 1))
        self.assertIsNone(read_cut(self.training, 2))

    def test_wrongly_bound_reply_evidence_is_rejected(self):
        self.register_piece()
        real = partial(exchange_trial, self.training)

        def mismatched(message):
            reply = real(message)
            return {**reply, 'ticket_id': 'W-Z'}

        with self.assertRaises(ValueError):
            request_training(self.scoring, 'W-A', 'OP-1', mismatched)
        # the wrong evidence settled nothing locally; the operation stays pending
        self.assertEqual(trial_status(self.scoring, 'W-A')['transfer_state'], 'pending')
        # a clean query recovers the committed cut
        settled = trial_status(self.scoring, 'W-A', self.link)
        self.assertEqual((settled['transfer_state'], settled['cut_id']), ('trained', 1))


class TrialStatusQueryTests(TrialCase):
    def test_query_does_not_create_or_withdraw(self):
        self.register_piece()
        self.link.arm('unavailable')
        pending = request_training(self.scoring, 'W-A', 'OP-1', self.link)
        self.assertEqual(pending['transfer_state'], 'pending')
        # the message never arrived; a query reports unknown and stays pending
        self.assertEqual(trial_status(self.scoring, 'W-A', self.link)['transfer_state'], 'pending')
        self.assertIsNone(read_cut(self.training, 1))
        with open_training_store(self.training) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM trial_transfers').fetchone()[0], 0)
        # re-registration with a working exchange also claims the pending status
        self.assertEqual(
            register_trial(self.scoring, 'A', 'W-A', self.link)['transfer_state'], 'pending'
        )
        # the actual delivery settles the original operation
        settled = request_training(self.scoring, 'W-A', 'OP-1', self.link)
        self.assertEqual((settled['transfer_state'], settled['cut_id']), ('trained', 1))

    def test_reregister_resolves_pending_through_exchange(self):
        self.register_piece()
        self.link.arm('lose_reply')
        self.assertEqual(
            request_training(self.scoring, 'W-A', 'OP-1', self.link)['transfer_state'], 'pending'
        )
        claimed = register_trial(self.scoring, 'A', 'W-A', self.link)
        self.assertEqual(
            (claimed['state'], claimed['transfer_state'], claimed['cut_id']),
            ('trained', 'trained', 1),
        )


if __name__ == '__main__':
    import unittest
    unittest.main()
