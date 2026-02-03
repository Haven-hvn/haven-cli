"""Plugin system for archiver plugins.

Plugins are data sources that discover and archive media content.
Each plugin implements discover_sources() and archive() methods.
"""

from haven_cli.plugins.base import ArchiverPlugin, PluginCapability, PluginInfo
from haven_cli.plugins.manager import PluginManager, get_plugin_manager
from haven_cli.plugins.registry import PluginRegistry, get_registry
from haven_cli.plugins.builtin import YouTubePlugin

__all__ = [
    "ArchiverPlugin",
    "PluginCapability",
    "PluginInfo",
    "PluginManager",
    "get_plugin_manager",
    "PluginRegistry",
    "get_registry",
    "YouTubePlugin",
]
