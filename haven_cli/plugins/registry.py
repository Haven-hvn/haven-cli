"""Plugin registry for discovering and loading plugins.

The PluginRegistry handles plugin discovery from various sources:
- Built-in plugins
- Entry points (installed packages)
- Plugin directories
"""

import importlib
import importlib.util
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

from haven_cli.plugins.base import ArchiverPlugin, PluginInfo


class PluginRegistry:
    """Registry for discovering and loading plugins.
    
    The PluginRegistry provides plugin discovery from multiple sources:
    - Built-in plugins bundled with Haven
    - Entry points from installed packages
    - Plugin directories for custom plugins
    
    Example:
        registry = PluginRegistry()
        
        # Discover all available plugins
        plugins = registry.discover_all()
        
        # Load a specific plugin
        plugin_class = registry.load("YouTubePlugin")
    """
    
    # Entry point group for Haven plugins
    ENTRY_POINT_GROUP = "haven_cli.plugins"
    
    # Built-in plugin module path
    BUILTIN_MODULE = "haven_cli.plugins.builtin"
    
    def __init__(
        self,
        plugin_dirs: Optional[List[Path]] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Initialize the plugin registry.
        
        Args:
            plugin_dirs: Additional directories to search for plugins
            config: Registry configuration
        """
        self._plugin_dirs = plugin_dirs or []
        self._config = config or {}
        self._discovered: Dict[str, Type[ArchiverPlugin]] = {}
        self._plugin_info: Dict[str, PluginInfo] = {}
    
    def discover_all(self) -> Dict[str, Type[ArchiverPlugin]]:
        """Discover all available plugins from all sources.
        
        Returns:
            Dictionary mapping plugin names to plugin classes
        """
        self._discovered.clear()
        self._plugin_info.clear()
        
        # Discover from each source
        self._discover_builtin()
        self._discover_entry_points()
        self._discover_directories()
        
        return self._discovered.copy()
    
    def _discover_builtin(self) -> None:
        """Discover built-in plugins."""
        try:
            # Try to import builtin module
            builtin = importlib.import_module(self.BUILTIN_MODULE)
            
            # Look for plugin classes
            for name in dir(builtin):
                obj = getattr(builtin, name)
                if self._is_plugin_class(obj):
                    self._register_discovered(obj)
                    
        except ImportError:
            # No builtin plugins module
            pass
    
    def _discover_entry_points(self) -> None:
        """Discover plugins from entry points."""
        try:
            # Python 3.10+ has importlib.metadata
            from importlib.metadata import entry_points
            
            # Get entry points for our group
            eps = entry_points(group=self.ENTRY_POINT_GROUP)
            
            for ep in eps:
                try:
                    plugin_class = ep.load()
                    if self._is_plugin_class(plugin_class):
                        self._register_discovered(plugin_class)
                except Exception:
                    # Skip plugins that fail to load
                    pass
                    
        except ImportError:
            # importlib.metadata not available
            pass
    
    def _discover_directories(self) -> None:
        """Discover plugins from plugin directories."""
        for plugin_dir in self._plugin_dirs:
            if not plugin_dir.exists():
                continue
            
            # Look for Python files
            for py_file in plugin_dir.glob("*.py"):
                if py_file.name.startswith("_"):
                    continue
                
                try:
                    plugin_class = self._load_from_file(py_file)
                    if plugin_class:
                        self._register_discovered(plugin_class)
                except Exception:
                    # Skip files that fail to load
                    pass
    
    def _load_from_file(self, path: Path) -> Optional[Type[ArchiverPlugin]]:
        """Load a plugin class from a Python file.
        
        Args:
            path: Path to the Python file
            
        Returns:
            Plugin class or None
        """
        # Create module spec
        spec = importlib.util.spec_from_file_location(
            path.stem,
            path,
        )
        
        if not spec or not spec.loader:
            return None
        
        # Load module
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        # Find plugin class in module
        for name in dir(module):
            obj = getattr(module, name)
            if self._is_plugin_class(obj):
                return obj
        
        return None
    
    def _is_plugin_class(self, obj: Any) -> bool:
        """Check if an object is a plugin class.
        
        Args:
            obj: Object to check
            
        Returns:
            True if obj is a plugin class
        """
        return (
            isinstance(obj, type)
            and issubclass(obj, ArchiverPlugin)
            and obj is not ArchiverPlugin
            and not getattr(obj, "__abstract__", False)
        )
    
    def _register_discovered(self, plugin_class: Type[ArchiverPlugin]) -> None:
        """Register a discovered plugin class.
        
        Args:
            plugin_class: The plugin class to register
        """
        # Instantiate temporarily to get info
        try:
            temp_instance = plugin_class()
            info = temp_instance.info
            name = info.name
            
            self._discovered[name] = plugin_class
            self._plugin_info[name] = info
        except Exception:
            # Skip plugins that fail to instantiate
            pass
    
    def load(self, plugin_name: str) -> Optional[Type[ArchiverPlugin]]:
        """Load a specific plugin by name.
        
        Args:
            plugin_name: Name of the plugin to load
            
        Returns:
            Plugin class or None
        """
        # Check if already discovered
        if plugin_name in self._discovered:
            return self._discovered[plugin_name]
        
        # Try to discover
        self.discover_all()
        
        return self._discovered.get(plugin_name)
    
    def get_info(self, plugin_name: str) -> Optional[PluginInfo]:
        """Get information about a plugin.
        
        Args:
            plugin_name: Name of the plugin
            
        Returns:
            PluginInfo or None
        """
        if plugin_name not in self._plugin_info:
            self.discover_all()
        
        return self._plugin_info.get(plugin_name)
    
    def get_all_info(self) -> List[PluginInfo]:
        """Get information about all discovered plugins.
        
        Returns:
            List of PluginInfo objects
        """
        if not self._plugin_info:
            self.discover_all()
        
        return list(self._plugin_info.values())
    
    @property
    def available_plugins(self) -> List[str]:
        """Get names of all available plugins.
        
        Returns:
            List of plugin names
        """
        if not self._discovered:
            self.discover_all()
        
        return list(self._discovered.keys())
    
    def add_plugin_directory(self, path: Path) -> None:
        """Add a directory to search for plugins.
        
        Args:
            path: Directory path to add
        """
        if path not in self._plugin_dirs:
            self._plugin_dirs.append(path)
    
    def remove_plugin_directory(self, path: Path) -> bool:
        """Remove a plugin directory.
        
        Args:
            path: Directory path to remove
            
        Returns:
            True if directory was removed
        """
        if path in self._plugin_dirs:
            self._plugin_dirs.remove(path)
            return True
        return False


# Global registry instance
_default_registry: Optional[PluginRegistry] = None


def get_registry() -> PluginRegistry:
    """Get the default plugin registry.
    
    Returns:
        The singleton PluginRegistry instance
    """
    global _default_registry
    if _default_registry is None:
        _default_registry = PluginRegistry()
    return _default_registry


def reset_registry() -> None:
    """Reset the default plugin registry."""
    global _default_registry
    _default_registry = None
