"""Source tracker for known source deduplication.

The SourceTracker persists known sources for each plugin to enable
the "archive_new" action which only archives sources that haven't
been seen before.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Set

logger = logging.getLogger(__name__)


class SourceTracker:
    """Track known sources for deduplication.
    
    The SourceTracker maintains a persistent cache of source IDs that
    have been archived by each plugin. This enables the "archive_new"
    action to only process new sources.
    
    Sources are stored in JSON files in the data directory, with one
    file per plugin named {plugin_name}_sources.json.
    
    Example:
        tracker = SourceTracker(data_dir=Path("/var/lib/haven"))
        
        # Load known sources for a plugin
        known = tracker.load("YouTubePlugin")
        
        # Mark a source as known after archiving
        tracker.add("YouTubePlugin", "video_12345")
    
    Attributes:
        _data_dir: Directory for storing source cache files
        _cache: In-memory cache of known sources by plugin name
    """
    
    def __init__(self, data_dir: Path) -> None:
        """Initialize the source tracker.
        
        Args:
            data_dir: Directory for storing source cache files.
                     Will be created if it doesn't exist.
        """
        self._data_dir = Path(data_dir)
        self._cache: Dict[str, Set[str]] = {}
        
        # Ensure data directory exists
        self._data_dir.mkdir(parents=True, exist_ok=True)
    
    def load(self, plugin_name: str) -> Set[str]:
        """Load known sources for a plugin.
        
        Loads from in-memory cache if available, otherwise reads
        from the cache file on disk.
        
        Args:
            plugin_name: Name of the plugin to load sources for
            
        Returns:
            Set of known source IDs for the plugin
        """
        # Return from cache if available
        if plugin_name in self._cache:
            return self._cache[plugin_name]
        
        # Try to load from disk
        cache_file = self._data_dir / f"{plugin_name}_sources.json"
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text())
                self._cache[plugin_name] = set(data.get("sources", []))
                logger.debug(
                    f"Loaded {len(self._cache[plugin_name])} known sources "
                    f"for {plugin_name}"
                )
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to load source cache for {plugin_name}: {e}")
                self._cache[plugin_name] = set()
        else:
            self._cache[plugin_name] = set()
        
        return self._cache[plugin_name]
    
    def add(self, plugin_name: str, source_id: str) -> None:
        """Mark a source as known.
        
        Adds the source ID to the known sources set for the plugin
        and persists the updated cache to disk.
        
        Args:
            plugin_name: Name of the plugin
            source_id: ID of the source to mark as known
        """
        known = self.load(plugin_name)
        known.add(source_id)
        self._save(plugin_name)
        logger.debug(f"Marked source {source_id} as known for {plugin_name}")
    
    def add_many(self, plugin_name: str, source_ids: Set[str]) -> None:
        """Mark multiple sources as known.
        
        More efficient than calling add() multiple times for bulk updates.
        
        Args:
            plugin_name: Name of the plugin
            source_ids: Set of source IDs to mark as known
        """
        known = self.load(plugin_name)
        known.update(source_ids)
        self._save(plugin_name)
        logger.debug(f"Marked {len(source_ids)} sources as known for {plugin_name}")
    
    def is_known(self, plugin_name: str, source_id: str) -> bool:
        """Check if a source is known.
        
        Args:
            plugin_name: Name of the plugin
            source_id: ID of the source to check
            
        Returns:
            True if the source is known, False otherwise
        """
        return source_id in self.load(plugin_name)
    
    def filter_new_sources(
        self,
        plugin_name: str,
        source_ids: list,
    ) -> list:
        """Filter a list of sources to only return new ones.
        
        Args:
            plugin_name: Name of the plugin
            source_ids: List of source IDs to filter
            
        Returns:
            List of source IDs that are not yet known
        """
        known = self.load(plugin_name)
        return [sid for sid in source_ids if sid not in known]
    
    def clear(self, plugin_name: str) -> None:
        """Clear known sources for a plugin.
        
        Removes all known sources from cache and deletes the cache file.
        
        Args:
            plugin_name: Name of the plugin to clear
        """
        self._cache.pop(plugin_name, None)
        cache_file = self._data_dir / f"{plugin_name}_sources.json"
        if cache_file.exists():
            try:
                cache_file.unlink()
                logger.info(f"Cleared source cache for {plugin_name}")
            except IOError as e:
                logger.warning(f"Failed to delete cache file for {plugin_name}: {e}")
    
    def get_stats(self, plugin_name: str) -> Dict[str, int]:
        """Get statistics for a plugin's known sources.
        
        Args:
            plugin_name: Name of the plugin
            
        Returns:
            Dictionary with count of known sources
        """
        return {
            "known_count": len(self.load(plugin_name)),
        }
    
    def _save(self, plugin_name: str) -> None:
        """Save known sources to disk.
        
        Writes the current cache for the plugin to a JSON file.
        
        Args:
            plugin_name: Name of the plugin to save
        """
        cache_file = self._data_dir / f"{plugin_name}_sources.json"
        try:
            data = {
                "sources": list(self._cache.get(plugin_name, set())),
                "updated_at": datetime.utcnow().isoformat(),
                "plugin_name": plugin_name,
            }
            cache_file.write_text(json.dumps(data, indent=2))
        except IOError as e:
            logger.error(f"Failed to save source cache for {plugin_name}: {e}")
