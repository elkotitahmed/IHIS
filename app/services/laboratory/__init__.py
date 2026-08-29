"""Laboratory service module - business logic and result validation."""


def evaluate_abnormality(value, normal_range):
    """Compare a result value against a normal range string like '4.0-11.0'."""
    if not value or not normal_range or '-' not in normal_range:
        return False
    try:
        low, high = normal_range.split('-')
        numeric = float(str(value).replace(',', '.'))
        return not (float(low) <= numeric <= float(high))
    except (ValueError, TypeError):
        return False
