def normalize_odds(val):
    """Convert American odds (abs >= 100, whole number) to decimal.
    Decimal odds pass through unchanged.
    Examples: -230 → 1.43, +150 → 2.50, 1.85 → 1.85
    """
    if val is None:
        return val
    try:
        v = float(val)
    except (ValueError, TypeError):
        return val
    if abs(v) >= 100 and v == int(v):
        return round((v / 100) + 1, 2) if v > 0 else round((100 / abs(v)) + 1, 2)
    return v
