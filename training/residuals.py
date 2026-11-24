from fractions import Fraction


def training_residual(estimate_g: Fraction, actual_g: Fraction) -> Fraction:
    return actual_g - estimate_g


def mean_residual(total_g: Fraction, feedback_count: int) -> Fraction:
    if feedback_count == 0:
        return Fraction(0)
    return total_g / feedback_count
