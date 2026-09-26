"""Built-in plugins for Haven CLI.

This module contains the built-in plugin implementations that ship with Haven CLI.
"""

from haven_cli.plugins.builtin.bittorrent import BitTorrentPlugin
from haven_cli.plugins.builtin.brightcove import BrightcovePlugin
from haven_cli.plugins.builtin.prowlarr import ProwlarrPlugin
from haven_cli.plugins.builtin.webvideo import WebVideoPlugin
from haven_cli.plugins.builtin.youtube import YouTubePlugin

__all__ = ["YouTubePlugin", "BitTorrentPlugin", "WebVideoPlugin", "BrightcovePlugin", "ProwlarrPlugin"]
