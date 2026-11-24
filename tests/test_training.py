from decimal import Decimal
from fractions import Fraction

from admission.history import read_admission
from admission.roster import admit
from tests.support import StationCase
from training.feedback import feedback
from training.history import read_cut
from training.ledger import find_feedback
from training.models import current_cut
from training.store import open_training_store


class TrainingTests(StationCase):
    def test_initial_and_first_feedback(self):
        zero = read_cut(self.training, 0)
        self.assertEqual((zero.feedback_count, zero.residual_total_g, zero.compensation_g), (0, 0, 0))
        a = admit(self.training, 'A', '10')
        cut = feedback(self.training, 'R1', '10', '14')
        b = admit(self.training, 'B', 10)
        self.assertEqual((cut.cut_id, cut.feedback_count, cut.compensation_g), (1, 1, 4))
        self.assertEqual((a.cut_id, a.prediction_g), (0, Fraction(10)))
        self.assertEqual((b.cut_id, b.prediction_g), (1, Fraction(14)))
        self.assertEqual(read_cut(self.training, 0), zero)
        self.assertEqual(read_cut(self.training, 1), cut)
        self.assertIsNone(read_cut(self.training, 2))

    def test_fractional_residuals(self):
        feedback(self.training, 'R1', 10, 14)
        cut = feedback(self.training, 'R2', Decimal('1.5'), '5/2')
        self.assertEqual(cut.residual_total_g, Fraction(5))
        self.assertEqual(cut.compensation_g, Fraction(5, 2))
        self.assertEqual(read_cut(self.training, 2).compensation_g, Fraction(5, 2))
        self.assertEqual(admit(self.training, 'A', '10.0').prediction_g, Fraction(25, 2))

    def test_negative_residual_and_prediction_are_exact(self):
        cut = feedback(self.training, 'R1', '3/2', 0)
        self.assertEqual(cut.compensation_g, Fraction(-3, 2))
        self.assertEqual(admit(self.training, 'A', '1/4').prediction_g, Fraction(-5, 4))

    def test_conflicting_feedback(self):
        one = feedback(self.training, 'R1', 10, 14)
        with self.assertRaisesRegex(ValueError, 'conflicting feedback'):
            feedback(self.training, 'R1', 10, 15)
        for illegal in ('-1', '1/0', 'NaN', Decimal('Infinity'), None, True):
            with self.subTest(illegal=illegal), self.assertRaises(ValueError):
                feedback(self.training, 'invalid', 10, illegal)
        with open_training_store(self.training) as db:
            self.assertEqual(current_cut(db), one)
            self.assertEqual(find_feedback(db, 'R1').actual_g, Fraction(14))
            self.assertIsNone(find_feedback(db, 'invalid'))
        self.assertIsNone(read_cut(self.training, 2))

    def test_same_input_feedback_replays_original_cut(self):
        one = feedback(self.training, 'R1', '10.0', 14)
        self.assertEqual(feedback(self.training, 'R1', Fraction(10), '28/2'), one)
        two = feedback(self.training, 'R2', 9, 10)
        self.assertEqual(feedback(self.training, 'R1', 10, 14), one)
        self.assertEqual((two.cut_id, two.compensation_g), (2, Fraction(5, 2)))
        with open_training_store(self.training) as db:
            self.assertEqual(current_cut(db), two)
            self.assertEqual(db.execute('SELECT count(*) FROM training_feedback').fetchone()[0], 2)

    def test_admission_replay(self):
        original = admit(self.training, 'A', '10.0')
        feedback(self.training, 'R1', 0, 4)
        self.assertEqual(admit(self.training, 'A', '20/2'), original)
        self.assertEqual(read_admission(self.training, 'A'), original)
        with self.assertRaises(ValueError):
            admit(self.training, 'bad', -1)
        with self.assertRaises(ValueError):
            admit(self.training, ' ', 10)
        self.assertIsNone(read_admission(self.training, 'bad'))


if __name__ == '__main__':
    import unittest
    unittest.main()
