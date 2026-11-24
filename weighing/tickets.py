from dataclasses import dataclass
from fractions import Fraction

from identifiers import require_identifier
from weighing.units import GramInput, parse_grams


@dataclass(frozen=True, slots=True)
class WeighTicket:
    ticket_id: str
    piece_id: str
    actual_g: Fraction


def normalize_ticket(ticket_id: str, piece_id: str, actual_g: GramInput) -> WeighTicket:
    return WeighTicket(
        require_identifier(ticket_id, 'ticket_id'),
        require_identifier(piece_id, 'piece_id'),
        parse_grams(actual_g),
    )
