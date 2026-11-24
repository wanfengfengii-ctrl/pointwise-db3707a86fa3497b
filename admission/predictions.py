from dataclasses import dataclass
from fractions import Fraction

from admission.requests import AdmissionRequest
from training.models import ModelCut


ADMISSION_PREFIX = 'admission/'


@dataclass(frozen=True, slots=True)
class AdmissionReceipt:
    piece_id: str
    estimate_g: Fraction
    cut_id: int
    compensation_g: Fraction
    prediction_g: Fraction

    @property
    def admission_id(self) -> str:
        return ADMISSION_PREFIX + self.piece_id


def make_prediction(request: AdmissionRequest, cut: ModelCut) -> AdmissionReceipt:
    return AdmissionReceipt(
        piece_id=request.piece_id,
        estimate_g=request.estimate_g,
        cut_id=cut.cut_id,
        compensation_g=cut.compensation_g,
        prediction_g=request.estimate_g + cut.compensation_g,
    )
