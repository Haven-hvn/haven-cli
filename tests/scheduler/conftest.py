"""Scheduler test harness: keep executors off the real DB.

Autouse-patches ``get_db_session`` so enqueued pipeline events and saved
executions never touch a developer's production haven.db.
"""
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def mock_db_session():
    with patch("haven_cli.database.connection.get_db_session") as mock_get:
        mock_session = MagicMock()
        mock_get.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_get.return_value.__exit__ = MagicMock(return_value=False)
        yield mock_get
