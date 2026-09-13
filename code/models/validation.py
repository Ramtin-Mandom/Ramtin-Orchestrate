"""Strict structural validation, without coercion or financial inference."""

import re
from datetime import date


def identifier(name, value):
    if not isinstance(value, str):
        raise TypeError(f"{name} must be text")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value):
        raise ValueError(f"{name} must be a nonempty identifier")


def calendar_date(name, value):
    if type(value) is not date:
        raise TypeError(f"{name} must be a date")


def parse_date(name, value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{name} must use YYYY-MM-DD")
    return date.fromisoformat(value)


def parse_boolean(name, value):
    if value not in ("true", "false", "True", "False"):
        raise ValueError(f"{name} must be true or false")
    return value.lower() == "true"
