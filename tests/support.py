import tempfile
import unittest
from pathlib import Path

from admission.roster import admit
from analysis.workday import initialize
from delivery.dispatch import deliver
from weighing.intake import weigh


class InterruptedReply(RuntimeError):
    pass


def interrupt_at(target):
    def interrupt(boundary):
        if boundary == target:
            raise InterruptedReply(boundary)
    return interrupt


class StationCase(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix='weight-shadow-test-')
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.training = self.root / 'training.sqlite3'
        self.scoring = self.root / 'scoring.sqlite3'
        initialize(self.training, self.scoring)

    def complete_piece(self, piece_id, estimate_g, actual_g):
        admission = admit(self.training, piece_id, estimate_g)
        self.assertEqual(deliver(self.training, self.scoring, admission).state, 'received')
        weigh(self.scoring, f'W-{piece_id}', piece_id, actual_g)
        return admission
