"""Plugin manager for loading and managing plugins.

The PluginManager handles plugin lifecycle, including loading,
initialization, and execution coordination.
"""

import logging
from typing import Any, Dict, List, Optional, Set, Type

from haven_cli.plugins.base import (
    ArchiverPlugin,
    ArchiveResult,
    MediaSource,
    PluginCapability,
    PluginInfo,
)

# Built-in plugins
from haven_cli.plugins.builtin import YouTubePlugin

logger = logging.getLogger(__name__)


class PluginManager:
    """Manages plugin lifecycle and execution.
    
    The PluginManager is responsible for:
    - Loading and initializing plugins
    - Managing plugin configuration
    - Coordinating plugin execution
    - Health checking plugins
    - Separating control plane and data plane plugins
    
    Control plane plugins run in the main process.
    Data plane plugins (heavy I/O) can run in worker processes.
    
    Example:
        manager = PluginManager()
        
        # Register plugins
        manager.register(YouTubePlugin)
        manager.register(BitTorrentPlugin)
        
        # Set data plane plugins (run in workers)
        manager.set_worker_plugins(["BitTorrentPlugin"])
        
        # Initialize all plugins
        await manager.initialize_all()
        
        # Discover sources from a plugin
        sources = await manager.discover_sources("YouTubePlugin")
    """
    
    def __init__(
        self,
        max_workers: int = 4,
        config: Optional[Dict[str, Any]] = None,
        load_builtin: bool = True,
    ) -> None:
        """Initialize the plugin manager.
        
        Args:
            max_workers: Maximum worker processes for data plane plugins
            config: Global plugin configuration
            load_builtin: Whether to automatically load built-in plugins
        """
        self._plugins: Dict[str, ArchiverPlugin] = {}
        self._plugin_classes: Dict[str, Type[ArchiverPlugin]] = {}
        self._worker_plugins: Set[str] = set()
        self._max_workers = max_workers
        self._config = config or {}
        self._initialized = False
        
        if load_builtin:
            self._load_builtin_plugins()
    
    @property
    def plugins(self) -> List[ArchiverPlugin]:
        """Get all registered plugin instances."""
        return list(self._plugins.values())
    
    @property
    def plugin_names(self) -> List[str]:
        """Get names of all registered plugins."""
        return list(self._plugins.keys())
    
    @property
    def enabled_plugins(self) -> List[ArchiverPlugin]:
        """Get all enabled plugins."""
        return [p for p in self._plugins.values() if p.enabled]
    
    def register(
        self,
        plugin_class: Type[ArchiverPlugin],
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Register a plugin class.
        
        Args:
            plugin_class: The plugin class to register
            config: Plugin-specific configuration
        """
        # Merge global and plugin-specific config
        plugin_config = {**self._config, **(config or {})}
        
        # Instantiate plugin
        plugin = plugin_class(config=plugin_config)
        
        # Store by name
        self._plugins[plugin.name] = plugin
        self._plugin_classes[plugin.name] = plugin_class
    
    def register_instance(self, plugin: ArchiverPlugin) -> None:
        """Register an already-instantiated plugin.
        
        Args:
            plugin: The plugin instance to register
        """
        self._plugins[plugin.name] = plugin
    
    def unregister(self, plugin_name: str) -> bool:
        """Unregister a plugin.
        
        Args:
            plugin_name: Name of the plugin to unregister
            
        Returns:
            True if plugin was unregistered
        """
        if plugin_name not in self._plugins:
            return False
        
        del self._plugins[plugin_name]
        self._plugin_classes.pop(plugin_name, None)
        self._worker_plugins.discard(plugin_name)
        return True
    
    def get_plugin(self, name: str) -> Optional[ArchiverPlugin]:
        """Get a plugin by name.
        
        Args:
            name: Plugin name
            
        Returns:
            Plugin instance or None
        """
        return self._plugins.get(name)
    
    def set_worker_plugins(self, plugin_names: List[str]) -> None:
        """Set which plugins should run in worker processes.
        
        Data plane plugins with heavy I/O should run in workers
        to avoid blocking the control plane.
        
        Args:
            plugin_names: Names of plugins to run in workers
        """
        self._worker_plugins = set(plugin_names)
    
    def is_worker_plugin(self, plugin_name: str) -> bool:
        """Check if a plugin runs in a worker process.
        
        Args:
            plugin_name: Name of the plugin
            
        Returns:
            True if plugin runs in worker
        """
        return plugin_name in self._worker_plugins
    
    async def initialize_all(self) -> Dict[str, bool]:
        """Initialize all registered plugins.
        
        Returns:
            Dictionary mapping plugin names to initialization success
        """
        results: Dict[str, bool] = {}
        
        for name, plugin in self._plugins.items():
            try:
                await plugin.initialize()
                results[name] = True
            except Exception as e:
                logger.error(f"Failed to initialize plugin {name}: {e}")
                results[name] = False
        
        self._initialized = True
        return results
    
    async def shutdown_all(self) -> None:
        """Shutdown all plugins."""
        for plugin in self._plugins.values():
            try:
                await plugin.shutdown()
            except Exception as e:
                logger.error(f"Error shutting down plugin: {e}")
        
        self._initialized = False
    
    async def discover_sources(self, plugin_name: str) -> List[MediaSource]:
        """Discover sources using a specific plugin.
        
        Args:
            plugin_name: Name of the plugin to use
            
        Returns:
            List of discovered media sources
        """
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            raise ValueError(f"Plugin not found: {plugin_name}")
        
        if not plugin.enabled:
            return []
        
        if not plugin.has_capability(PluginCapability.DISCOVER):
            raise ValueError(f"Plugin {plugin_name} does not support discovery")
        
        return await plugin.discover_sources()
    
    async def archive(
        self,
        plugin_name: str,
        source: MediaSource,
    ) -> ArchiveResult:
        """Archive a source using a specific plugin.
        
        Args:
            plugin_name: Name of the plugin to use
            source: The media source to archive
            
        Returns:
            Archive result
        """
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            raise ValueError(f"Plugin not found: {plugin_name}")
        
        if not plugin.enabled:
            return ArchiveResult(
                success=False,
                error=f"Plugin {plugin_name} is disabled",
            )
        
        if not plugin.has_capability(PluginCapability.ARCHIVE):
            return ArchiveResult(
                success=False,
                error=f"Plugin {plugin_name} does not support archiving",
            )
        
        return await plugin.archive(source)
    
    async def health_check_all(self) -> Dict[str, bool]:
        """Health check all plugins.
        
        Returns:
            Dictionary mapping plugin names to health status
        """
        results: Dict[str, bool] = {}
        
        for name, plugin in self._plugins.items():
            try:
                results[name] = await plugin.health_check()
            except Exception as e:
                logger.error(f"Health check failed for {name}: {e}")
                results[name] = False
        
        return results
    
    async def health_check(self, plugin_name: str) -> bool:
        """Health check a specific plugin.
        
        Args:
            plugin_name: Name of the plugin
            
        Returns:
            True if plugin is healthy
        """
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            return False
        
        try:
            return await plugin.health_check()
        except Exception as e:
            logger.error(f"Health check failed for {plugin_name}: {e}")
            return False
    
    def enable_plugin(self, plugin_name: str) -> bool:
        """Enable a plugin.
        
        Args:
            plugin_name: Name of the plugin
            
        Returns:
            True if plugin was enabled
        """
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            return False
        
        plugin.enabled = True
        return True
    
    def disable_plugin(self, plugin_name: str) -> bool:
        """Disable a plugin.
        
        Args:
            plugin_name: Name of the plugin
            
        Returns:
            True if plugin was disabled
        """
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            return False
        
        plugin.enabled = False
        return True
    
    def configure_plugin(
        self,
        plugin_name: str,
        config: Dict[str, Any],
    ) -> bool:
        """Update plugin configuration.
        
        Args:
            plugin_name: Name of the plugin
            config: Configuration to merge
            
        Returns:
            True if plugin was configured
        """
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            return False
        
        plugin.configure(config)
        return True
    
    def get_plugin_info(self, plugin_name: str) -> Optional[PluginInfo]:
        """Get information about a plugin.
        
        Args:
            plugin_name: Name of the plugin
            
        Returns:
            PluginInfo or None
        """
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            return None
        
        return plugin.info
    
    def get_all_plugin_info(self) -> List[PluginInfo]:
        """Get information about all plugins.
        
        Returns:
            List of PluginInfo objects
        """
        return [p.info for p in self._plugins.values()]
    
    def _load_builtin_plugins(self) -> None:
        """Load built-in plugins.
        
        This method automatically registers all built-in plugins that
        ship with Haven CLI.
        """
        # Register YouTube plugin
        try:
            self.register(YouTubePlugin)
            logger.debug("Loaded built-in plugin: YouTubePlugin")
        except Exception as e:
            logger.warning(f"Failed to load YouTubePlugin: {e}")
    
    async def load_builtin_plugins(self) -> Dict[str, bool]:
        """Load and initialize all built-in plugins.
        
        Returns:
            Dictionary mapping plugin names to initialization success
        """
        self._load_builtin_plugins()
        return await self.initialize_all()


# Global plugin manager instance
_default_manager: Optional[PluginManager] = None


def get_plugin_manager() -> PluginManager:
    """Get the default plugin manager instance.
    
    Returns:
        The singleton PluginManager instance
    """
    global _default_manager
    if _default_manager is None:
        _default_manager = PluginManager()
    return _default_manager


def reset_plugin_manager() -> None:
    """Reset the default plugin manager instance."""
    global _default_manager
    _default_manager = None
