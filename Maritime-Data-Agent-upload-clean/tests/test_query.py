from app.query import validate_sql


def test_allows_select_from_dataset():
    assert validate_sql('SELECT COUNT(*) FROM "sales_123"', 'sales_123').startswith('SELECT')


def test_blocks_write_statement():
    try:
        validate_sql('DELETE FROM "sales_123"', 'sales_123')
        assert False
    except ValueError:
        pass


def test_blocks_other_table():
    try:
        validate_sql('SELECT * FROM users', 'sales_123')
        assert False
    except ValueError:
        pass


def test_allows_cte_over_dataset():
    sql = 'WITH totals AS (SELECT SUM(amount) AS value FROM "sales_123") SELECT * FROM totals'
    assert validate_sql(sql, 'sales_123') == sql


def test_allows_join_within_allowed_tables():
    sql = 'SELECT COUNT(DISTINCT "a"."target_id") FROM "a" JOIN "b" ON "a"."target_id" = "b"."target_id"'
    assert validate_sql(sql, allowed_tables=["a", "b"]) == sql


def test_blocks_join_outside_allowed_tables():
    try:
        validate_sql(
            'SELECT * FROM "a" JOIN "secret_table" ON "a"."id" = "secret_table"."id"',
            allowed_tables=["a", "b"],
        )
        assert False
    except ValueError:
        pass


def test_single_table_mode_still_blocks_join_to_second_table():
    try:
        validate_sql('SELECT * FROM "sales_123" JOIN "users" ON 1 = 1', 'sales_123')
        assert False
    except ValueError:
        pass
