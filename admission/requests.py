from dataclasses import dataclass
from fractions import Fraction

from identifiers import require_identifier
from weighing.units import GramInput, parse_grams


@dataclass(frozen=True, slots=True)
class AdmissionRequest:
    piece_id: str
    estimate_g: Fraction


def normalize_admission(piece_id: str, estimate_g: GramInput) -> AdmissionRequest:
    return AdmissionRequest(require_identifier(piece_id, 'piece_id'), parse_grams(estimate_g))
