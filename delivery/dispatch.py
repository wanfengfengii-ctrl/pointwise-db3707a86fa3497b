import sqlite3

from admission.predictions import AdmissionReceipt
from delivery.intake import accept_delivery
from delivery.reconcile import DeliveryStatus
from delivery.records import stage_delivery
from station import Boundary, StationPath, temporarily_unavailable


def deliver(
    training_path: StationPath,
    scoring_path: StationPath,
    source: AdmissionReceipt | str,
    *,
    on_boundary: Boundary | None = None,
) -> DeliveryStatus:
    record = stage_delivery(training_path, source)
    try:
        receipt = accept_delivery(scoring_path, record, on_boundary=on_boundary)
    except sqlite3.OperationalError as error:
        if not temporarily_unavailable(error):
            raise
        return DeliveryStatus(record.delivery_id)
    return DeliveryStatus(record.delivery_id, receipt)
