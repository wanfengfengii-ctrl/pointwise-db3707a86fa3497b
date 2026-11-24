def require_identifier(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or not value.isprintable()
    ):
        raise ValueError(f'{field} must be a nonempty printable identifier without outer whitespace')
    return value
