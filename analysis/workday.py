import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from admission.roster import admit, initialize_roster
from analysis.publication import PendingReport, initialize_reports, report
from analysis.retrieval import FrozenReport, get
from delivery.dispatch import deliver
from delivery.records import initialize_dispatch
from delivery.store import initialize_receipts
from station import SCORING_SITE, TRAINING_SITE, StationPath, connect_station, initialize_station, transaction
from training.feedback import feedback
from training.ledger import initialize_feedback_ledger
from training.models import initialize_models
from weighing.intake import initialize_actuals, weigh
from weighing.units import encode_grams


_SAMPLES = Path(__file__).resolve().parents[1] / 'samples'


def initialize(training_path: StationPath, scoring_path: StationPath) -> None:
    source_path = Path(training_path).resolve()
    target_path = Path(scoring_path).resolve()
    if source_path == target_path or (
        source_path.exists() and target_path.exists() and source_path.samefile(target_path)
    ):
        raise ValueError('training and scoring require distinct station databases')
    with connect_station(source_path, create=True) as source, connect_station(target_path, create=True) as target:
        with transaction(source, write=True), transaction(target, write=True):
            initialize_station(source, TRAINING_SITE, (
                initialize_models, initialize_feedback_ledger, initialize_roster, initialize_dispatch,
            ))
            initialize_station(target, SCORING_SITE, (
                initialize_receipts, initialize_actuals, initialize_reports,
            ))


def run_demo(
    sample_path: StationPath = _SAMPLES / 'workday.json',
    expected_path: StationPath = _SAMPLES / 'expected-report.json',
) -> dict:
    inputs = json.loads(Path(sample_path).read_text(encoding='utf-8'))
    expected = json.loads(Path(expected_path).read_text(encoding='utf-8'))
    first, second = inputs['pieces']
    report_id = inputs['report_id']
    order = inputs['report_order']
    with TemporaryDirectory(prefix='weight-shadow-') as folder:
        training_path = Path(folder) / 'training.sqlite3'
        scoring_path = Path(folder) / 'scoring.sqlite3'
        initialize(training_path, scoring_path)

        first_receipt = admit(training_path, first['piece_id'], first['estimate_g'])
        feedback(training_path, **inputs['feedback'])
        second_receipt = admit(training_path, second['piece_id'], second['estimate_g'])

        weigh(scoring_path, first['ticket_id'], first['piece_id'], first['actual_g'])
        pending = report(scoring_path, report_id, order)
        initially_unsigned = get(scoring_path, report_id) is None
        if not isinstance(pending, PendingReport):
            raise RuntimeError('demo issued a report before both sets of evidence arrived')
        deliver(training_path, scoring_path, second_receipt)
        weigh(scoring_path, second['ticket_id'], second['piece_id'], second['actual_g'])
        deliver(training_path, scoring_path, first_receipt)

        signed = report(scoring_path, report_id, order)
        if not isinstance(signed, FrozenReport):
            raise RuntimeError(f'demo report still awaits evidence: {signed.missing}')
        later_cut = feedback(training_path, **inputs['later_feedback'])
        initialize(training_path, scoring_path)
        reopened = get(scoring_path, report_id)
        if reopened is None:
            raise RuntimeError('demo report is missing after reopening the stations')

    actual = json.loads(json.dumps({
        'pending_before_delivery': [asdict(item) for item in pending.missing],
        'initially_unsigned': initially_unsigned,
        'report': asdict(signed),
    }, default=encode_grams))
    downstream = {
        'report_id': reopened.report_id,
        'receipt_no': reopened.receipt.receipt_no,
        'same_report': reopened == signed,
        'later_cut_id': later_cut.cut_id,
        'later_compensation_g': encode_grams(later_cut.compensation_g),
    }
    if actual != expected['actual'] or downstream != expected['downstream']:
        observed = json.dumps({'actual': actual, 'downstream': downstream}, ensure_ascii=False, sort_keys=True)
        raise RuntimeError(f'demo differs from the independently calculated sample report: {observed}')

    return {
        'case_id': 'T6',
        'input': inputs,
        'expected': expected['actual'],
        'actual': actual,
        'downstream_expected': expected['downstream'],
        'downstream_actual': downstream,
        'code_paths': [
            'training/feedback.py', 'admission/roster.py', 'delivery/dispatch.py',
            'weighing/intake.py', 'analysis/eligibility.py', 'analysis/errors.py',
            'analysis/publication.py', 'analysis/retrieval.py',
        ],
        'oracle': 'Hand calculation in samples/expected-report.json: B uses 10 + 4, A uses 10 + 0, and (1 + 1) / 2 is 1 g.',
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Packaging line weight shadow evaluation')
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init', help='initialize or reopen the two station stores')
    init.add_argument('training_path', type=Path)
    init.add_argument('scoring_path', type=Path)
    demo = commands.add_parser('demo', help='run and verify an example with two isolated stores')
    demo.add_argument('--sample', type=Path, default=_SAMPLES / 'workday.json')
    demo.add_argument('--expected', type=Path, default=_SAMPLES / 'expected-report.json')
    args = parser.parse_args(argv)
    if args.command == 'init':
        initialize(args.training_path, args.scoring_path)
    else:
        scenario = run_demo(args.sample, args.expected)
        print('POINTWISE_SCENARIO ' + json.dumps(scenario, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
