"""Integration tests for PipelineInterface with real database.

These tests verify that the PipelineInterface works correctly with a real
SQLite database, testing the full data flow from interface to database.
"""

import asyncio
import os
import tempfile
from datetime import datetime, timezone
from typing import List

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

# Ensure we can import from the project
import sys
sys.path.insert(0, '/home/tower/Documents/workspace/haven-cli')

from haven_tui.core.pipeline_interface import (
    PipelineInterface,
    UnifiedDownload,
    DownloadStats,
    RetryResult,
)
from haven_cli.database.models import (
    Base,
    Video,
    Download,
    TorrentDownload,
    PipelineSnapshot,
)
from haven_cli.pipeline.events import EventType, Event, EventBus, get_event_bus, reset_event_bus


@pytest.fixture
def temp_db_path():
    """Create a temporary database file."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.fixture
def database_engine(temp_db_path):
    """Create a database engine with all tables."""
    engine = create_engine(f"sqlite:///{temp_db_path}")
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture
def event_bus():
    """Get a fresh event bus for testing."""
    reset_event_bus()
    bus = get_event_bus()
    yield bus
    reset_event_bus()


class TestPipelineInterfaceIntegration:
    """Integration tests using real database and event bus."""
    
    
    @pytest.mark.asyncio
    async def test_event_subscription_integration(self, database_engine, temp_db_path):
        """Test event subscription with real event bus."""
        event_bus = get_event_bus()
        interface = PipelineInterface(
            database_path=temp_db_path,
            event_bus=event_bus,
        )
        
        # Set up session
        SessionLocal = sessionmaker(bind=database_engine)
        session = SessionLocal()
        interface._db_session = session
        
        events_received = []
        
        def sync_handler(event: Event):
            events_received.append(("sync", event.event_type))
        
        async def async_handler(event: Event):
            events_received.append(("async", event.event_type))
        
        try:
            # Subscribe handlers
            interface.on_event(EventType.DOWNLOAD_PROGRESS, sync_handler)
            interface.on_event(EventType.UPLOAD_PROGRESS, async_handler)
            interface.on_any_event(sync_handler)
            
            # Publish test events
            await event_bus.publish(Event(
                event_type=EventType.DOWNLOAD_PROGRESS,
                payload={"progress": 50},
            ))
            
            await event_bus.publish(Event(
                event_type=EventType.UPLOAD_PROGRESS,
                payload={"progress": 75},
            ))
            
            # Allow events to be processed
            await asyncio.sleep(0.1)
            
            # Verify events were received
            assert len(events_received) >= 2
            
        finally:
            session.close()
            reset_event_bus()
    
    


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
