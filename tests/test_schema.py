from sqlalchemy import inspect

EXPECTED = {
    "instruments", "daily_bars", "corporate_actions", "index_membership", "trading_calendar",
    "option_contracts", "option_snapshots", "kite_session", "ingestion_runs", "data_quality_issues",
    "backtest_runs", "backtest_trades", "backtest_equity", "backtest_index_signals",
}


def test_migration_creates_every_table(engine):
    assert EXPECTED <= set(inspect(engine).get_table_names())


def test_daily_bars_primary_key_includes_source(engine):
    pk = inspect(engine).get_pk_constraint("daily_bars")["constrained_columns"]
    assert pk == ["instrument_id", "trade_date", "source"]
