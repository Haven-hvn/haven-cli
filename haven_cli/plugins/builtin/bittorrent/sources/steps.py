"""Concrete extraction steps for the web scraping pipeline.

This module provides ready-to-use extraction steps for common web scraping
operations like fetching HTML, selecting elements, extracting attributes,
applying regex transformations, and building magnet links.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote, unquote, urljoin

import httpx

from haven_cli.plugins.builtin.bittorrent.sources.base import MagnetLink
from haven_cli.plugins.builtin.bittorrent.sources.extraction import (
    ExtractionContext,
    ExtractionStep,
)


@dataclass
class FetchHtmlStep(ExtractionStep):
    """Step that fetches HTML content from a URL.
    
    The URL can be a template that includes placeholders like {query}.
    
    Example:
        FetchHtmlStep(url_template="https://example.com/search?q={query}")
        FetchHtmlStep(url_template="https://example.com/page/{page}")
    """
    
    url_template: str
    method: str = "GET"
    headers: Dict[str, str] = field(default_factory=dict)
    timeout: int = 30
    follow_redirects: bool = True
    
    def __post_init__(self) -> None:
        """Set default headers."""
        if not self.headers:
            self.headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
    
    @property
    def name(self) -> str:
        return "fetch_html"
    
    async def execute(self, context: ExtractionContext) -> ExtractionContext:
        """Fetch HTML from the URL template."""
        # Build URL from template
        url = self.url_template.format(
            query=quote(context.query) if context.query else "",
            **context.variables,
        )
        
        # Make HTTP request
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=self.follow_redirects) as client:
            response = await client.request(
                method=self.method,
                url=url,
                headers=self.headers,
            )
            response.raise_for_status()
            context.raw_html = response.text
            context.metadata["url"] = url
            context.metadata["status_code"] = response.status_code
        
        return context
    
    def __repr__(self) -> str:
        return f"FetchHtmlStep(url_template={self.url_template!r})"


@dataclass
class SelectElementsStep(ExtractionStep):
    """Step that selects HTML elements using a CSS selector.
    
    The selected elements are stored in context.elements for further processing.
    
    Example:
        SelectElementsStep(selector=".torrent-item")
        SelectElementsStep(selector="table.torrents tr.torrent-row")
    """
    
    selector: str
    limit: Optional[int] = None
    
    @property
    def name(self) -> str:
        return "select_elements"
    
    async def execute(self, context: ExtractionContext) -> ExtractionContext:
        """Select elements using CSS selector."""
        if not context.soup:
            context.add_error("No HTML content to select from")
            return context
        
        elements = context.soup.select(self.selector)
        
        if self.limit:
            elements = elements[:self.limit]
        
        context.elements = elements
        context.metadata["selected_count"] = len(elements)
        
        return context
    
    def __repr__(self) -> str:
        return f"SelectElementsStep(selector={self.selector!r}, limit={self.limit})"


@dataclass
class ExtractAttributeStep(ExtractionStep):
    """Step that extracts an attribute from the current element.
    
    The extracted value is stored in context.current_data[output_key].
    
    Example:
        ExtractAttributeStep(attribute="href", output_key="magnet_uri")
        ExtractAttributeStep(attribute="data-size", output_key="size")
    """
    
    attribute: str
    output_key: str
    default: Any = None
    
    @property
    def name(self) -> str:
        return "extract_attribute"
    
    async def execute(self, context: ExtractionContext) -> ExtractionContext:
        """Extract attribute from current element."""
        if not context.current_element:
            context.add_error("No current element to extract from")
            return context
        
        value = context.current_element.get(self.attribute, self.default)
        context.current_data[self.output_key] = value
        
        return context
    
    def __repr__(self) -> str:
        return f"ExtractAttributeStep(attribute={self.attribute!r}, output_key={self.output_key!r})"


@dataclass
class ExtractTextStep(ExtractionStep):
    """Step that extracts text content from an element.
    
    Can extract from the current element or a child element selected by selector.
    
    Example:
        ExtractTextStep(output_key="title")
        ExtractTextStep(selector=".title", output_key="title")
    """
    
    output_key: str
    selector: Optional[str] = None
    strip: bool = True
    default: str = ""
    
    @property
    def name(self) -> str:
        return "extract_text"
    
    async def execute(self, context: ExtractionContext) -> ExtractionContext:
        """Extract text from element."""
        element = context.current_element
        
        if self.selector:
            if not context.soup:
                context.add_error("No HTML content to select from")
                return context
            element = context.soup.select_one(self.selector)
        
        if not element:
            context.current_data[self.output_key] = self.default
            return context
        
        text = element.get_text()
        if self.strip:
            text = text.strip()
        
        context.current_data[self.output_key] = text
        
        return context
    
    def __repr__(self) -> str:
        return f"ExtractTextStep(output_key={self.output_key!r}, selector={self.selector!r})"


@dataclass
class RegexStep(ExtractionStep):
    """Step that applies a regex pattern to extract data.
    
    The pattern can extract from a context variable or the current data.
    
    Example:
        RegexStep(pattern=r"btih:([a-fA-F0-9]{40})", input_key="magnet_uri", output_key="infohash")
        RegexStep(pattern=r"Size: ([0-9.]+) (GB|MB)", input_key="size_text", output_key="size")
    """
    
    pattern: str
    output_key: str
    input_key: Optional[str] = None
    flags: int = re.IGNORECASE
    group: int = 1
    
    def __post_init__(self) -> None:
        """Compile the regex pattern."""
        self._compiled_pattern = re.compile(self.pattern, flags=self.flags)
    
    @property
    def name(self) -> str:
        return "regex"
    
    async def execute(self, context: ExtractionContext) -> ExtractionContext:
        """Apply regex pattern and extract match."""
        # Get input text
        if self.input_key:
            text = context.current_data.get(self.input_key, "")
        else:
            text = str(context.current_element) if context.current_element else ""
        
        # Apply regex
        match = self._compiled_pattern.search(text)
        if match:
            context.current_data[self.output_key] = match.group(self.group)
        else:
            context.current_data[self.output_key] = None
        
        return context
    
    def __repr__(self) -> str:
        return f"RegexStep(pattern={self.pattern!r}, output_key={self.output_key!r})"


@dataclass
class TransformStep(ExtractionStep):
    """Step that applies a transformation function to a value.
    
    Useful for converting data types, parsing sizes, etc.
    
    Example:
        TransformStep(input_key="size", output_key="size_bytes", transform=parse_size)
        TransformStep(input_key="seeders", output_key="seeders", transform=int)
    """
    
    input_key: str
    output_key: str
    transform: Callable[[Any], Any]
    default: Any = None
    
    @property
    def name(self) -> str:
        return "transform"
    
    async def execute(self, context: ExtractionContext) -> ExtractionContext:
        """Apply transformation to value."""
        value = context.current_data.get(self.input_key)
        
        if value is None:
            context.current_data[self.output_key] = self.default
            return context
        
        try:
            context.current_data[self.output_key] = self.transform(value)
        except Exception as e:
            context.add_error(f"Transform failed for {self.input_key}: {e}")
            context.current_data[self.output_key] = self.default
        
        return context
    
    def __repr__(self) -> str:
        return f"TransformStep(input_key={self.input_key!r}, output_key={self.output_key!r})"


@dataclass
class SetVariableStep(ExtractionStep):
    """Step that sets a pipeline variable.
    
    Useful for storing values that will be used later in the pipeline.
    
    Example:
        SetVariableStep(key="base_url", value="https://example.com")
        SetVariableStep(key="category", value="video")
    """
    
    key: str
    value: Any
    
    @property
    def name(self) -> str:
        return "set_variable"
    
    async def execute(self, context: ExtractionContext) -> ExtractionContext:
        """Set the variable."""
        context.set_variable(self.key, self.value)
        return context
    
    def __repr__(self) -> str:
        return f"SetVariableStep(key={self.key!r}, value={self.value!r})"


@dataclass
class BuildMagnetLinkStep(ExtractionStep):
    """Step that builds a MagnetLink from extracted data.
    
    This step reads from context.current_data and creates a MagnetLink object.
    
    Required keys in current_data:
        - infohash: The 40-character hex infohash
        - uri: The full magnet URI (or will be built from infohash)
    
    Optional keys:
        - title: Title/name
        - size: Size in bytes
        - seeders: Number of seeders
        - leechers: Number of leechers
        - category: Content category
    """
    
    infohash_key: str = "infohash"
    uri_key: str = "uri"
    title_key: str = "title"
    size_key: str = "size"
    seeders_key: str = "seeders"
    leechers_key: str = "leechers"
    category_key: str = "category"
    source_name: str = ""
    
    @property
    def name(self) -> str:
        return "build_magnet_link"
    
    async def execute(self, context: ExtractionContext) -> ExtractionContext:
        """Build MagnetLink from current_data."""
        data = context.current_data
        
        # Get required fields
        infohash = data.get(self.infohash_key)
        uri = data.get(self.uri_key)
        
        if not infohash:
            context.add_error("Missing infohash for magnet link")
            return context
        
        # Build URI if not provided
        if not uri:
            uri = f"magnet:?xt=urn:btih:{infohash}"
        
        # Get optional fields
        title = data.get(self.title_key, "")
        size = data.get(self.size_key, 0)
        seeders = data.get(self.seeders_key, 0)
        leechers = data.get(self.leechers_key, 0)
        category = data.get(self.category_key, "unknown")
        
        # Build metadata from remaining fields
        metadata = {k: v for k, v in data.items() 
                   if k not in [self.infohash_key, self.uri_key, self.title_key, 
                               self.size_key, self.seeders_key, self.leechers_key, 
                               self.category_key]}
        
        # Create MagnetLink
        try:
            magnet = MagnetLink(
                infohash=infohash,
                uri=uri,
                title=title,
                size=size,
                seeders=seeders,
                leechers=leechers,
                category=category,
                source_name=self.source_name or context.get_variable("source_name", ""),
                metadata=metadata,
            )
            context.magnet_links.append(magnet)
        except ValueError as e:
            context.add_error(f"Failed to create magnet link: {e}")
        
        return context
    
    def __repr__(self) -> str:
        return f"BuildMagnetLinkStep(source_name={self.source_name!r})"


@dataclass
class FilterStep(ExtractionStep):
    """Step that filters magnet links based on criteria.
    
    This step filters the context.magnet_links list.
    
    Example:
        FilterStep(min_size=100*1024*1024)  # At least 100MB
        FilterStep(min_seeders=5)  # At least 5 seeders
        FilterStep(category="video")  # Only video
    """
    
    min_size: Optional[int] = None
    max_size: Optional[int] = None
    min_seeders: Optional[int] = None
    category: Optional[str] = None
    
    @property
    def name(self) -> str:
        return "filter"
    
    async def execute(self, context: ExtractionContext) -> ExtractionContext:
        """Filter magnet links."""
        filtered = []
        
        for magnet in context.magnet_links:
            # Check size
            if self.min_size and magnet.size < self.min_size:
                continue
            if self.max_size and magnet.size > self.max_size:
                continue
            
            # Check seeders
            if self.min_seeders and magnet.seeders < self.min_seeders:
                continue
            
            # Check category
            if self.category and magnet.category != self.category:
                continue
            
            filtered.append(magnet)
        
        context.magnet_links = filtered
        return context
    
    def __repr__(self) -> str:
        return f"FilterStep(min_size={self.min_size}, max_size={self.max_size}, min_seeders={self.min_seeders})"


@dataclass
class LimitStep(ExtractionStep):
    """Step that limits the number of magnet links.
    
    Example:
        LimitStep(limit=10)
    """
    
    limit: int
    
    @property
    def name(self) -> str:
        return "limit"
    
    async def execute(self, context: ExtractionContext) -> ExtractionContext:
        """Limit magnet links."""
        context.magnet_links = context.magnet_links[:self.limit]
        return context
    
    def __repr__(self) -> str:
        return f"LimitStep(limit={self.limit})"


@dataclass
class SortStep(ExtractionStep):
    """Step that sorts magnet links.
    
    Example:
        SortStep(by="seeders", reverse=True)
        SortStep(by="size", reverse=False)
    """
    
    by: str = "seeders"
    reverse: bool = True
    
    @property
    def name(self) -> str:
        return "sort"
    
    async def execute(self, context: ExtractionContext) -> ExtractionContext:
        """Sort magnet links."""
        context.magnet_links.sort(
            key=lambda m: getattr(m, self.by, 0),
            reverse=self.reverse,
        )
        return context
    
    def __repr__(self) -> str:
        return f"SortStep(by={self.by!r}, reverse={self.reverse})"


@dataclass
class ParseSizeStep(ExtractionStep):
    """Step that parses a human-readable size string to bytes.
    
    Example:
        ParseSizeStep(input_key="size_text", output_key="size")
    """
    
    input_key: str
    output_key: str
    default: int = 0
    
    @property
    def name(self) -> str:
        return "parse_size"
    
    async def execute(self, context: ExtractionContext) -> ExtractionContext:
        """Parse size string to bytes."""
        size_text = context.current_data.get(self.input_key, "")
        
        if not size_text:
            context.current_data[self.output_key] = self.default
            return context
        
        try:
            # Parse size like "1.5 GB", "500 MB", etc.
            match = re.search(r"([\d.]+)\s*([KMGT]?B)", size_text, re.IGNORECASE)
            if not match:
                context.current_data[self.output_key] = self.default
                return context
            
            value = float(match.group(1))
            unit = match.group(2).upper()
            
            multipliers = {
                "B": 1,
                "KB": 1024,
                "MB": 1024 ** 2,
                "GB": 1024 ** 3,
                "TB": 1024 ** 4,
            }
            
            context.current_data[self.output_key] = int(value * multipliers.get(unit, 1))
        except Exception as e:
            context.add_error(f"Failed to parse size '{size_text}': {e}")
            context.current_data[self.output_key] = self.default
        
        return context
    
    def __repr__(self) -> str:
        return f"ParseSizeStep(input_key={self.input_key!r}, output_key={self.output_key!r})"


@dataclass
class UrlJoinStep(ExtractionStep):
    """Step that joins a relative URL with a base URL.
    
    Example:
        UrlJoinStep(input_key="href", base_url="https://example.com", output_key="full_url")
    """
    
    input_key: str
    base_url: str
    output_key: str
    
    @property
    def name(self) -> str:
        return "url_join"
    
    async def execute(self, context: ExtractionContext) -> ExtractionContext:
        """Join relative URL with base URL."""
        relative_url = context.current_data.get(self.input_key, "")
        context.current_data[self.output_key] = urljoin(self.base_url, relative_url)
        return context
    
    def __repr__(self) -> str:
        return f"UrlJoinStep(input_key={self.input_key!r}, base_url={self.base_url!r})"
