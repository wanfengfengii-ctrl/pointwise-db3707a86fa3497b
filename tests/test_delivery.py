from dataclasses import replace
from fractions import Fraction

from admission.history import read_admission
from admission.roster import admit
from analysis.publication import PendingReport, report
from analysis.retrieval import get
from analysis.workday import initialize
from delivery.dispatch import deliver
from delivery.intake import accept_delivery
from delivery.reconcile import delivery_status
from delivery.records import delivery_id_for, read_delivery, stage_delivery
from delivery.store import find_receipt, open_scoring_store
from tests.support import InterruptedReply, StationCase, interrupt_at
from training.feedback import feedback
from training.store import open_training_store
from weighing.history import read_actual, read_ticket
from weighing.intake import weigh


class DeliveryTests(StationCase):
    def test_acknowledged_delivery(self):
        original = admit(self.training, 'A', 10)
        sent = deliver(self.training, self.scoring, original)
        self.assertEqual(sent.state, 'received')
        self.assertEqual(sent.receipt.admission, original)
        self.assertEqual(deliver(self.training, self.scoring, sent.delivery_id), sent)
        self.assertEqual(delivery_status(self.training, self.scoring, sent.delivery_id), sent)
        with open_scoring_store(self.scoring) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM prediction_receipts').fetchone()[0], 1)
        ticket = weigh(self.scoring, 'WA', 'A', 11)
        signed = report(self.scoring, 'after-delivery', ['A'])
        self.assertEqual(signed.summary.rows[0].ticket_id, ticket.ticket_id)
        self.assertEqual(signed.summary.mae_g, Fraction(1))

    def test_conflicting_delivery_evidence(self):
        original = admit(self.training, 'A', 10)
        altered = replace(original, cut_id=1, compensation_g=Fraction(4), prediction_g=Fraction(14))
        with self.assertRaisesRegex(ValueError, 'committed admission'):
            deliver(self.training, self.scoring, altered)
        with open_training_store(self.training) as db:
            self.assertIsNone(read_delivery(db, delivery_id_for(original)))
        record = stage_delivery(self.training, original)
        with self.assertRaisesRegex(ValueError, 'identity'):
            accept_delivery(self.scoring, replace(record, delivery_id='prediction/admission/B'))
        with self.assertRaisesRegex(ValueError, 'prediction does not match'):
            accept_delivery(self.scoring, replace(record, admission=replace(original, prediction_g=Fraction(9))))
        first = accept_delivery(self.scoring, record)
        with self.assertRaisesRegex(ValueError, 'conflicting prediction'):
            accept_delivery(self.scoring, replace(record, admission=altered))
        self.assertEqual(accept_delivery(self.scoring, record), first)

    def test_lost_reply_during_training(self):
        with self.assertRaisesRegex(InterruptedReply, '^after_admission_commit$'):
            admit(self.training, 'A', '10', on_boundary=interrupt_at('after_admission_commit'))
        weigh(self.scoring, 'WA', 'A', 11)
        pending = report(self.scoring, 'late-a', ['A'])
        self.assertIsInstance(pending, PendingReport)
        self.assertEqual([(m.piece_id, m.prediction_missing, m.actual_missing) for m in pending.missing],
                         [('A', True, False)])
        self.assertIsNone(get(self.scoring, 'late-a'))

        feedback(self.training, 'R1', 10, 14)
        b = admit(self.training, 'B', 10)
        weigh(self.scoring, 'WB', 'B', 15)
        initialize(self.training, self.scoring)
        original_id = 'prediction/admission/A'
        with open_training_store(self.training) as db:
            self.assertIsNone(read_delivery(db, original_id))
        self.assertEqual(delivery_status(self.training, self.scoring, original_id).state, 'pending')
        a = admit(self.training, 'A', '20/2')
        self.assertEqual((a.cut_id, a.prediction_g), (0, Fraction(10)))
        self.assertEqual((b.cut_id, b.prediction_g), (1, Fraction(14)))
        with self.assertRaisesRegex(ValueError, 'conflicting admission'):
            admit(self.training, 'A', '10.01')
        self.assertEqual(read_admission(self.training, 'A'), a)
        self.assertEqual(deliver(self.training, self.scoring, original_id).receipt.admission, a)
        deliver(self.training, self.scoring, b)
        result = report(self.scoring, 'late-a', ['A', 'B'])
        self.assertEqual([(r.piece_id, r.cut_id, r.prediction_g) for r in result.summary.rows],
                         [('A', 0, Fraction(10)), ('B', 1, Fraction(14))])
        self.assertEqual(result.summary.mae_g, Fraction(1))

    def test_receipt_commit_boundaries(self):
        original = admit(self.training, 'A', 10)
        delivery_id = delivery_id_for(original)
        weigh(self.scoring, 'WA', 'A', 11)
        with self.assertRaisesRegex(InterruptedReply, '^before_receipt_commit$'):
            deliver(self.training, self.scoring, original, on_boundary=interrupt_at('before_receipt_commit'))
        initialize(self.training, self.scoring)
        with open_training_store(self.training) as db:
            self.assertEqual(read_delivery(db, delivery_id).admission, original)
        with open_scoring_store(self.scoring) as db:
            self.assertIsNone(find_receipt(db, 'A'))
        status = delivery_status(self.training, self.scoring, delivery_id)
        self.assertEqual((status.state, status.receipt), ('pending', None))
        self.assertIsInstance(report(self.scoring, 'lost-delivery', ['A']), PendingReport)

        with self.assertRaisesRegex(InterruptedReply, '^after_receipt_commit$'):
            deliver(self.training, self.scoring, delivery_id, on_boundary=interrupt_at('after_receipt_commit'))
        with open_scoring_store(self.scoring) as db:
            committed = find_receipt(db, 'A')
        self.assertIsNotNone(committed)
        initialize(self.training, self.scoring)
        observed = delivery_status(self.training, self.scoring, delivery_id)
        self.assertEqual((observed.state, observed.receipt), ('received', committed))
        self.assertEqual(deliver(self.training, self.scoring, delivery_id).receipt, committed)
        with open_scoring_store(self.scoring) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM prediction_receipts').fetchone()[0], 1)
        self.assertEqual(report(self.scoring, 'lost-delivery', ['A']).summary.mae_g, Fraction(1))

    def test_unavailable_target_leaves_delivery_resumable(self):
        original = admit(self.training, 'A', 10)
        unavailable = self.root / 'not-present.sqlite3'
        status = deliver(self.training, unavailable, original)
        self.assertEqual((status.state, status.receipt), ('pending', None))
        self.assertEqual(delivery_status(self.training, unavailable, status.delivery_id), status)
        self.assertFalse(unavailable.exists())
        self.assertEqual(deliver(self.training, self.scoring, status.delivery_id).state, 'received')

    def test_actual_arrives_first(self):
        a = admit(self.training, 'A', 10)
        ticket = weigh(self.scoring, 'WA', 'A', '11.00')
        with open_scoring_store(self.scoring) as db:
            self.assertEqual(read_ticket(db, 'WA'), ticket)
            self.assertIsNone(find_receipt(db, 'A'))
        pending = report(self.scoring, 'early-scale', ['A'])
        self.assertIsInstance(pending, PendingReport)
        self.assertEqual([(m.prediction_missing, m.actual_missing) for m in pending.missing], [(True, False)])
        self.assertIsNone(get(self.scoring, 'early-scale'))
        deliver(self.training, self.scoring, a)
        signed = report(self.scoring, 'early-scale', ['A'])
        row = signed.summary.rows[0]
        self.assertEqual((row.piece_id, row.ticket_id, row.actual_g, row.prediction_g), ('A', 'WA', 11, 10))
        self.assertEqual(signed.summary.mae_g, Fraction(1))

    def test_zero_actual_and_other_piece_are_distinct_from_missing(self):
        a = admit(self.training, 'A', '10')
        b = admit(self.training, 'B', '10')
        ticket_b = weigh(self.scoring, 'WB', 'B', 15)
        deliver(self.training, self.scoring, a)
        pending = report(self.scoring, 'same-piece', ['A'])
        self.assertIsInstance(pending, PendingReport)
        self.assertEqual([(m.piece_id, m.actual_missing) for m in pending.missing], [('A', True)])
        self.assertIsNone(get(self.scoring, 'same-piece'))

        ticket_a = weigh(self.scoring, 'WA', 'A', '0.0')
        self.assertEqual(ticket_a.actual_g, Fraction(0))
        with open_scoring_store(self.scoring) as db:
            self.assertEqual(read_actual(db, 'A'), ticket_a)
            self.assertEqual(read_actual(db, 'B'), ticket_b)
        signed_a = report(self.scoring, 'same-piece', ['A'])
        self.assertEqual((signed_a.summary.rows[0].actual_g, signed_a.summary.mae_g), (0, 10))
        deliver(self.training, self.scoring, b)
        both = report(self.scoring, 'both-pieces', ['B', 'A'])
        self.assertEqual([(r.piece_id, r.ticket_id) for r in both.summary.rows], [('B', 'WB'), ('A', 'WA')])

        c = admit(self.training, 'C', '10')
        weigh(self.scoring, 'WC', 'C', 0)
        missing_prediction = report(self.scoring, 'zero-first', ['C'])
        self.assertIsInstance(missing_prediction, PendingReport)
        self.assertFalse(missing_prediction.missing[0].actual_missing)
        deliver(self.training, self.scoring, c)
        self.assertEqual(report(self.scoring, 'zero-first', ['C']).summary.mae_g, Fraction(10))

    def test_invalid_actual_and_ticket_conflicts(self):
        first = weigh(self.scoring, 'WA', 'A', '1.50')
        self.assertEqual(weigh(self.scoring, 'WA', 'A', '3/2'), first)
        for ticket, piece, actual in (
            ('WA', 'A', '2'), ('WA', 'B', '3/2'), ('other', 'A', '3/2'),
            ('illegal', 'B', '-1'), ('illegal', 'B', ''), ('illegal', 'B', '1/0'),
            ('illegal', 'B', 'inf'), ('illegal', 'B', 1.1), ('illegal', 'B', None),
            ('', 'B', '1'), ('illegal', ' B', '1'),
        ):
            with self.subTest(ticket=ticket, piece=piece, actual=actual), self.assertRaises(ValueError):
                weigh(self.scoring, ticket, piece, actual)
        with open_scoring_store(self.scoring) as db:
            self.assertEqual(read_actual(db, 'A'), first)
            self.assertIsNone(read_actual(db, 'B'))
            self.assertEqual(db.execute('SELECT count(*) FROM actual_weights').fetchone()[0], 1)
