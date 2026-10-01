"""Validate persisted goal budgets before consumers use numeric/path fields."""
import os

MAX_LIMIT = 999999999
MAX_COUNTER = (1 << 63) - 1
REQUIRED = {"status", "budget_jobs", "budget_seconds", "spent_jobs", "spent_seconds",
            "fuse_trips", "started_epoch", "workdir"}


def validate_workdir(value):
    if (not isinstance(value, str) or not value or not os.path.isabs(value)
            or any(character in value for character in "\x00\t\r\n")):
        raise ValueError("invalid goal budget: workdir must be an absolute path without field separators")


def validate_budget(value):
    """Reject malformed data without coercion or mutation; preserve legacy extras."""
    if not isinstance(value, dict) or not REQUIRED.issubset(value):
        raise ValueError("invalid goal budget: required fields are missing")
    if not isinstance(value["status"], str) or value["status"] not in {"open", "closed"}:
        raise ValueError("invalid goal budget: status")
    for key in ("budget_jobs", "budget_seconds"):
        number = value[key]
        if number is not None and (type(number) is not int or not 1 <= number <= MAX_LIMIT):
            raise ValueError("invalid goal budget: " + key + " must be null or an integer in 1..999999999")
    for key in ("spent_jobs", "spent_seconds", "fuse_trips", "started_epoch", "reserved_jobs", "closed_epoch"):
        if key not in value:  # Optional newer/historical fields stay optional.
            continue
        number = value[key]
        if type(number) is not int or not 0 <= number <= MAX_COUNTER:
            raise ValueError("invalid goal budget: " + key + " must be a bounded nonnegative integer")
    if value["spent_jobs"] + value.get("reserved_jobs", 0) > MAX_COUNTER:
        raise ValueError("invalid goal budget: combined job counters exceed the numeric range")
    validate_workdir(value["workdir"])
    return value
