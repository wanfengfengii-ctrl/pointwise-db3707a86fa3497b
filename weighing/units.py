from decimal import Decimal
from fractions import Fraction


GramInput = int | str | Decimal | Fraction


def parse_grams(value: GramInput) -> Fraction:
    """Parse a physical input in grams with exact, nonnegative semantics."""
    if isinstance(value, bool) or not isinstance(value, (int, str, Decimal, Fraction)):
        raise ValueError('grams must be an integer, Decimal, Fraction, or exact numeric text')
    try:
        grams = Fraction(value)
    except (ValueError, ZeroDivisionError, OverflowError) as error:
        raise ValueError('invalid exact gram value') from error
    if grams < 0:
        raise ValueError('physical grams must be nonnegative')
    return grams


def encode_grams(value: Fraction) -> str:
    if not isinstance(value, Fraction):
        raise TypeError('stored grams must be a Fraction')
    return str(value)


def decode_grams(value: str) -> Fraction:
    """Decode a stored quantity, including signed predictions and residuals."""
    if not isinstance(value, str):
        raise ValueError('stored grams must be text')
    return Fraction(value)
