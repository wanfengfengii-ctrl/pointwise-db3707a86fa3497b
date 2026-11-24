import sqlite3
from contextlib import closing
from fractions import Fraction

from admission.history import read_admission
from admission.roster import admit
from analysis.publication import report
from analysis.workday import initialize, run_demo
from delivery.dispatch import deliver
from station import StationFormatError
from tests.support import StationCase
from training.history import read_cut
from training.store import open_training_store


class WorkdayTests(StationCase):
    def test_demo_runs_the_entire_workday(self):
        scenario = run_demo()
        signed = scenario['actual']['report']
        self.assertEqual(scenario['case_id'], 'T6')
        self.assertTrue(scenario['actual']['initially_unsigned'])
        self.assertEqual([row['prediction_g'] for row in signed['summary']['rows']], ['14', '10'])
        self.assertEqual(signed['summary']['mae_g'], '1')
        self.assertTrue(scenario['downstream_actual']['same_report'])
        self.assertEqual(scenario['downstream_actual']['later_compensation_g'], '2')

    def test_incompatible_station_is_preserved(self):
        alien_path = self.root / 'alien.sqlite3'
        with closing(sqlite3.connect(alien_path)) as db, db:
            db.execute('CREATE TABLE operator_notes (note TEXT NOT NULL)')
            db.execute('INSERT INTO operator_notes VALUES (?)', ('keep this',))
        fresh_training = self.root / 'new-training.sqlite3'
        with self.assertRaises(StationFormatError):
            initialize(fresh_training, alien_path)
        with closing(sqlite3.connect(alien_path)) as db:
            self.assertEqual(db.execute('SELECT note FROM operator_notes').fetchone(), ('keep this',))
            self.assertEqual(db.execute('PRAGMA application_id').fetchone(), (0,))
        initialize(fresh_training, self.root / 'new-scoring.sqlite3')
        self.assertEqual(read_cut(fresh_training, 0).compensation_g, Fraction(0))

        self.complete_piece('A', 10, 11)
        signed = report(self.scoring, 'keep', ['A'])
        with self.assertRaises(ValueError):
            initialize(self.training, self.training)
        with self.assertRaises(StationFormatError):
            initialize(self.scoring, self.training)
        self.assertEqual(report(self.scoring, 'keep', ['A']), signed)

    def test_reinitialize_missing_structure_preserves_existing_records(self):
        original = admit(self.training, 'A', 10)
        with open_training_store(self.training) as db:
            db.execute('DROP TABLE dispatches')
        initialize(self.training, self.scoring)
        self.assertEqual(read_admission(self.training, 'A'), original)
        self.assertEqual(deliver(self.training, self.scoring, original).state, 'received')

    def test_reinitialize_rejects_incompatible_owned_table(self):
        original = admit(self.training, 'A', 10)
        with open_training_store(self.training) as db:
            db.execute('DROP TABLE dispatches')
            db.execute('CREATE TABLE dispatches (legacy_note TEXT)')
            db.execute('INSERT INTO dispatches VALUES (?)', ('operator data',))
        with self.assertRaisesRegex(StationFormatError, 'dispatches'):
            initialize(self.training, self.scoring)
        with open_training_store(self.training) as db:
            self.assertEqual(db.execute('SELECT legacy_note FROM dispatches').fetchone()[0], 'operator data')
        self.assertEqual(read_admission(self.training, 'A'), original)
