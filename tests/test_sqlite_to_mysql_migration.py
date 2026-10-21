import pytest

from scripts.migrate_sqlite_to_mysql import _quoted, plan_columns


def test_plan_columns_preserves_source_order_and_allows_new_target_columns():
    assert plan_columns(["id", "name"], ["name", "created_at", "id"]) == ["id", "name"]


def test_plan_columns_rejects_data_loss_from_missing_target_column():
    with pytest.raises(ValueError, match="missing source columns: token"):
        plan_columns(["id", "token"], ["id"])


def test_quoted_rejects_unsafe_identifier():
    with pytest.raises(ValueError, match="Unsafe database identifier"):
        _quoted("users; DROP TABLE users")
