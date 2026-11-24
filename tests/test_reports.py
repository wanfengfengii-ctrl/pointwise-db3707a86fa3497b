import sqlite3
from fractions import Fraction

from admission.history import read_admission
from admission.roster import admit
from analysis.publication import PendingReport, report
from analysis.retrieval import FrozenReport, get
from analysis.workday import initialize
from delivery.dispatch import deliver
from delivery.reconcile import delivery_status
from delivery.store import open_scoring_store
from tests.support import InterruptedReply, StationCase, interrupt_at
from training.feedback import feedback
from training.history import read_cut
from training.store import open_training_store
from weighing.history import read_ticket
from weighing.intake import weigh


class ReportTests(StationCase):
    def test_two_training_cuts(self):
        a = admit(self.training, 'A', 10)
        feedback(self.training, 'R1', 10, 14)
        b = admit(self.training, 'B', 10)
        delivered_a = deliver(self.training, self.scoring, a)
        weigh(self.scoring, 'WB', 'B', 15)
        deliver_b = deliver(self.training, self.scoring, b)
        weigh(self.scoring, 'WA', 'A', 11)
        feedback(self.training, 'R2', 10, 10)
        signed = report(self.scoring, 'night-2', ['B', 'A'])
        self.assertIsInstance(signed, FrozenReport)
        self.assertEqual(signed.piece_ids, ('B', 'A'))
        evidence = [(row.piece_id, row.cut_id, row.prediction_g, row.actual_g, row.ticket_id)
                    for row in signed.summary.rows]
        self.assertEqual(evidence, [('B', 1, 14, 15, 'WB'), ('A', 0, 10, 11, 'WA')])
        self.assertEqual([row.delivery_id for row in signed.summary.rows],
                         [deliver_b.delivery_id, delivered_a.delivery_id])
        self.assertEqual([row.prediction_receipt_no for row in signed.summary.rows],
                         [deliver_b.receipt.receipt_no, delivered_a.receipt.receipt_no])
        self.assertEqual([row.signed_error_g for row in signed.summary.rows], [Fraction(1), Fraction(1)])
        self.assertEqual([row.absolute_error_g for row in signed.summary.rows], [Fraction(1), Fraction(1)])
        self.assertEqual(signed.summary.mae_g, Fraction(1))
        self.assertEqual(get(self.scoring, 'night-2'), signed)

    def test_rational_error_and_no_feedback_report(self):
        self.complete_piece('A', '3/2', '5/4')
        self.complete_piece('B', '1/3', '1/2')
        signed = report(self.scoring, 'fractional', ['B', 'A'])
        self.assertEqual([r.cut_id for r in signed.summary.rows], [0, 0])
        self.assertEqual([r.prediction_g for r in signed.summary.rows], [Fraction(1, 3), Fraction(3, 2)])
        self.assertEqual([r.signed_error_g for r in signed.summary.rows], [Fraction(1, 6), Fraction(-1, 4)])
        self.assertEqual(signed.summary.mae_g, Fraction(5, 24))
        self.assertEqual(get(self.scoring, 'fractional'), signed)
        repeated = report(self.scoring, 'requested-rows', ['A', 'B', 'A'])
        self.assertEqual(repeated.piece_ids, ('A', 'B', 'A'))
        self.assertEqual(repeated.summary.mae_g, Fraction(2, 9))

    def test_invalid_request_and_report_conflict(self):
        self.complete_piece('A', 10, 11)
        for report_id, pieces in (('', ['A']), ('invalid', []), ('invalid', 'A'), ('invalid', [' A'])):
            with self.subTest(report_id=report_id, pieces=pieces), self.assertRaises(ValueError):
                report(self.scoring, report_id, pieces)
        self.assertIsNone(get(self.scoring, 'invalid'))
        self.assertIsInstance(report(self.scoring, 'waiting', ['B']), PendingReport)
        self.assertIsNone(get(self.scoring, 'waiting'))
        signed = report(self.scoring, 'one', ['A'])
        with self.assertRaisesRegex(ValueError, 'conflicting report request'):
            report(self.scoring, 'one', ['B', 'A'])
        self.assertEqual(get(self.scoring, 'one'), signed)
        self.assertEqual(report(self.scoring, 'one', ('A',)), signed)

    def assert_unsigned_in_store(self, report_id):
        self.assertIsNone(get(self.scoring, report_id))
        with open_scoring_store(self.scoring) as db:
            reports = db.execute('SELECT 1 FROM reports WHERE report_id = ?', (report_id,)).fetchall()
            rows = db.execute('SELECT 1 FROM report_rows WHERE report_id = ?', (report_id,)).fetchall()
            receipts = db.execute('SELECT 1 FROM publication_receipts WHERE report_id = ?', (report_id,)).fetchall()
        self.assertEqual((reports, rows, receipts), ([], [], []))

    def test_rollback(self):
        self.complete_piece('A', '10', '11')
        for boundary in ('after_report_write', 'before_report_commit'):
            with self.subTest(boundary=boundary):
                with self.assertRaisesRegex(InterruptedReply, f'^{boundary}$'):
                    report(self.scoring, boundary, ['A'], on_boundary=interrupt_at(boundary))
                initialize(self.training, self.scoring)
                self.assert_unsigned_in_store(boundary)
                signed = report(self.scoring, boundary, ['A'])
                self.assertEqual(signed.summary.mae_g, Fraction(1))
                self.assertEqual(get(self.scoring, boundary), signed)
                self.assertEqual(report(self.scoring, boundary, ['A']), signed)
                with open_scoring_store(self.scoring) as db:
                    count = db.execute(
                        'SELECT count(*) FROM publication_receipts WHERE report_id = ?', (boundary,)
                    ).fetchone()[0]
                    self.assertEqual(count, 1)

    def test_commit_rejection_rolls_back_report_and_success_receipt(self):
        self.complete_piece('A', 10, 11)
        with open_scoring_store(self.scoring) as db:
            db.execute('CREATE TABLE allowed_publications (report_id TEXT PRIMARY KEY)')
            db.execute('''CREATE TABLE publication_guard (
                report_id TEXT REFERENCES allowed_publications (report_id) DEFERRABLE INITIALLY DEFERRED
            )''')
            db.execute('''CREATE TRIGGER check_publication_at_commit AFTER INSERT ON publication_receipts
                BEGIN
                    INSERT INTO publication_guard VALUES (NEW.report_id);
                END''')
        with self.assertRaises(sqlite3.IntegrityError):
            report(self.scoring, 'commit-error', ['A'])
        self.assert_unsigned_in_store('commit-error')
        with open_scoring_store(self.scoring) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM publication_guard').fetchone()[0], 0)
            db.execute('INSERT INTO allowed_publications VALUES (?)', ('commit-error',))
        signed = report(self.scoring, 'commit-error', ['A'])
        self.assertEqual(get(self.scoring, 'commit-error'), signed)
        self.assertEqual(signed.summary.mae_g, Fraction(1))

    def test_reopen_frozen_report(self):
        a = self.complete_piece('A', 10, 11)
        one = feedback(self.training, 'R1', 10, 14)
        b = self.complete_piece('B', 10, 15)
        original_delivery = deliver(self.training, self.scoring, b)
        original = report(self.scoring, 'night-original', ['B', 'A'])
        next_cut = feedback(self.training, 'R2', 0, 9)
        self.assertEqual(next_cut.compensation_g, Fraction(13, 2))

        for _ in range(2):
            initialize(self.training, self.scoring)
        self.assertEqual(read_cut(self.training, 1), one)
        self.assertEqual(read_cut(self.training, 2), next_cut)
        self.assertEqual(read_admission(self.training, 'A'), a)
        self.assertEqual(read_admission(self.training, 'B'), b)
        self.assertEqual(delivery_status(self.training, self.scoring, original_delivery.delivery_id), original_delivery)
        with open_scoring_store(self.scoring) as db:
            self.assertEqual(read_ticket(db, 'W-A').actual_g, Fraction(11))
        with open_training_store(self.training) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM training_feedback').fetchone()[0], 2)
        copied = get(self.scoring, 'night-original')
        self.assertEqual(copied, original)
        self.assertEqual(copied.piece_ids, ('B', 'A'))
        self.assertEqual(copied.receipt, original.receipt)
        self.assertEqual(report(self.scoring, 'night-original', ['B', 'A']), original)
        self.assertIsNone(get(self.scoring, 'unknown'))

        self.complete_piece('C', 10, '33/2')
        new = report(self.scoring, 'next-shift', ['C'])
        self.assertEqual(new.receipt.receipt_no, copied.receipt.receipt_no + 1)
        self.assertEqual((new.summary.mae_g, new.summary.rows[0].prediction_g), (0, Fraction(33, 2)))
