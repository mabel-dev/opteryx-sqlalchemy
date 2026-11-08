import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy_dialect.functions import FUNCTION_NAMES


def test_function_names_are_sorted_unique_upper_case():
    assert list(FUNCTION_NAMES) == sorted(set(FUNCTION_NAMES))
    assert all(name == name.upper() and not name.startswith("_") for name in FUNCTION_NAMES)


def test_function_names_cover_each_kind():
    names = set(FUNCTION_NAMES)
    # scalar, alias, aggregate, window, and a planner-handled function
    assert {"UPPER", "SUBSTR", "COUNT", "ROW_NUMBER", "DATE_TRUNC", "TIME_BUCKET"} <= names
