"""Forum-based magnet link source.

This module provides ForumScraperSource, which extracts magnet links from
forum-based torrent sites. It's designed to work with PHPBB-style forums
that follow a common pattern.

The workflow:
1. Fetch forum listing page (e.g., thread0806.php?fid=26)
2. Extract thread URLs from the listing
3. Fetch each thread page
4. Extract infohash from "特徵全碼" field
5. Optionally fetch full magnet link from rmdown.com (includes trackers)
6. Build magnet link from infohash or use rmdown magnet link

Configuration:
    domain: Forum domain (e.g., "sample.com")
    forum_id: Forum ID (fid parameter)
    max_threads: Maximum number of threads to fetch
    listing_url_template: Template for listing page URL
    thread_url_template: Template for thread page URL
    infohash_pattern: Regex pattern to extract infohash
    title_pattern: Regex pattern to extract title
    size_pattern: Regex pattern to extract size
    use_rmdown: Whether to fetch full magnet link from rmdown.com (includes trackers)
    rmdown_url_template: Template for rmdown.com URL
"""

from __future__ import annotations

import asyncio
import functools
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, TypeVar

import httpx

from haven_cli.plugins.builtin.bittorrent.sources.base import (
    MagnetLink,
    MagnetSource,
    SourceConfig,
    SourceHealth,
    SourceHealthStatus,
)
from haven_cli.plugins.builtin.bittorrent.sources.extraction import (
    ExtractionContext,
    ExtractionPipeline,
)
from haven_cli.plugins.builtin.bittorrent.sources.steps import (
    BuildMagnetLinkStep,
    ExtractTextStep,
    FetchHtmlStep,
    RegexStep,
    SelectElementsStep,
)

T = TypeVar("T")


def with_exponential_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable_status_codes: tuple[int, ...] = (429, 503, 502, 504),
    retryable_exceptions: tuple[type[Exception], ...] = (httpx.HTTPStatusError,),
):
    """Decorator that adds exponential backoff retry logic to async functions.
    
    This decorator catches HTTP 429 (Too Many Requests) and other retryable errors,
    then retries the function with exponentially increasing delays.
    
    Args:
        max_retries: Maximum number of retry attempts (default: 3)
        base_delay: Initial delay in seconds (default: 1.0)
        max_delay: Maximum delay between retries in seconds (default: 30.0)
        retryable_status_codes: HTTP status codes that trigger a retry
        retryable_exceptions: Exception types that trigger a retry
        
    Returns:
        Decorated function with retry logic
        
    Example:
        @with_exponential_backoff(max_retries=4, base_delay=1.0)
        async def fetch_data(url: str) -> str:
            # This will retry up to 4 times with delays: 1s, 2s, 4s, 8s
            response = await client.get(url)
            return response.text
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            last_exception: Optional[Exception] = None
            
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exception = e
                    
                    # Check if this is a retryable HTTP error
                    should_retry = False
                    if isinstance(e, httpx.HTTPStatusError):
                        should_retry = e.response.status_code in retryable_status_codes
                    
                    if not should_retry:
                        raise
                    
                    if attempt < max_retries:
                        # Calculate exponential backoff delay
                        delay = min(base_delay * (2 ** attempt), max_delay)
                        print(
                            f"Rate limited (HTTP {e.response.status_code}), "
                            f"retrying in {delay}s (attempt {attempt + 1}/{max_retries})..."
                        )
                        await asyncio.sleep(delay)
                    else:
                        print(
                            f"Max retries ({max_retries}) exceeded for "
                            f"HTTP {e.response.status_code} error"
                        )
                        raise
                except Exception:
                    # Non-retryable exceptions are raised immediately
                    raise
            
            # This should never be reached, but just in case
            if last_exception:
                raise last_exception
            raise RuntimeError("Unexpected state in retry logic")
        
        return wrapper
    return decorator


@dataclass
class ForumSourceConfig(SourceConfig):
    """Configuration for forum-based magnet sources.
    
    Attributes:
        domain: Forum domain (e.g., "sample.com")
        forum_id: Forum ID (fid parameter)
        max_threads: Maximum number of threads to fetch
        listing_url_template: Template for listing page URL
        thread_url_template: Template for thread page URL
        infohash_pattern: Regex pattern to extract infohash
        title_pattern: Regex pattern to extract title
        size_pattern: Regex pattern to extract size
        use_rmdown: Whether to fetch full magnet link from rmdown.com (includes trackers)
        rmdown_url_template: Template for rmdown.com URL
    """
    
    domain: str = ""
    forum_id: str = ""
    max_threads: int = 10
    listing_url_template: str = "https://{domain}/thread0806.php?fid={forum_id}"
    thread_url_template: str = "https://{domain}{thread_path}"
    infohash_pattern: str = r"【特徵全碼】：([A-Fa-f0-9]{40})"
    title_pattern: str = r"<title>(.+?)</title>"
    size_pattern: str = r"【影片大小】：([\d.]+)(GB|MB|KB)"
    use_rmdown: bool = True
    rmdown_url_template: str = "https://rmdown.com/link.php?hash={infohash}"
    
    def __post_init__(self) -> None:
        """Set default name if not provided."""
        if not self.name:
            self.name = f"forum_{self.domain}_{self.forum_id}"


class ForumScraperSource(MagnetSource):
    """Magnet source that scrapes forum-based torrent sites.
    
    This source is designed for PHPBB-style forums that follow a common
    pattern for organizing torrent threads. It's not opinionated about
    specific websites - the user configures the domain and forum ID.
    
    When use_rmdown is enabled (default), this source will fetch the full
    magnet link from rmdown.com which includes multiple tracker announce URLs.
    This is important for initial peer discovery, especially for less popular
    torrents where DHT can be slow to find peers.
    
    Example:
        config = ForumSourceConfig(
            name="sample_video",
            domain="sample.com",
            forum_id="1",
            max_threads=5,
            use_rmdown=True,  # Fetch full magnet with trackers
        )
        
        source = ForumScraperSource(config=config)
        links = await source.search("")
    """
    
    def __init__(self, config: ForumSourceConfig) -> None:
        """Initialize the forum scraper source.
        
        Args:
            config: Forum source configuration
        """
        super().__init__(config)
        self._forum_config = config
        self._listing_pipeline: Optional[ExtractionPipeline] = None
        self._thread_pipeline: Optional[ExtractionPipeline] = None
    
    @property
    def forum_config(self) -> ForumSourceConfig:
        """Get the forum configuration."""
        return self._forum_config
    
    async def initialize(self) -> bool:
        """Initialize the source and build pipelines."""
        if self._initialized:
            return True
        
        try:
            # Build pipelines
            self._build_pipelines()
            self._initialized = True
            return True
        except Exception as e:
            print(f"Failed to initialize forum source: {e}")
            return False
    
    def _build_pipelines(self) -> None:
        """Build extraction pipelines for forum scraping."""
        # Pipeline for extracting thread URLs from listing page
        self._listing_pipeline = ExtractionPipeline([
            FetchHtmlStep(
                url_template=self._forum_config.listing_url_template,
            ),
            SelectElementsStep(selector="tr.tr3.t_one.tac h3 a"),
            # Extract thread URLs and titles
            # This will be handled in custom logic
        ], name="forum_listing")
        
        # Pipeline for extracting magnet info from thread page
        self._thread_pipeline = ExtractionPipeline([
            FetchHtmlStep(
                url_template=self._forum_config.thread_url_template,
            ),
            # Extract infohash
            RegexStep(
                pattern=self._forum_config.infohash_pattern,
                output_key="infohash",
            ),
            # Extract title
            RegexStep(
                pattern=self._forum_config.title_pattern,
                output_key="title",
            ),
            # Extract size
            RegexStep(
                pattern=self._forum_config.size_pattern,
                output_key="size_text",
            ),
            # Build magnet link
            BuildMagnetLinkStep(
                source_name=self.name,
            ),
        ], name="forum_thread")
    
    async def search(self, query: str) -> List[MagnetLink]:
        """Search for magnet links from the forum.
        
        Args:
            query: Search query (ignored for forum sources, uses latest threads)
            
        Returns:
            List of magnet links
        """
        if not self.enabled:
            return []
        
        if not self._initialized:
            await self.initialize()
        
        # Fetch listing page
        listing_url = self._forum_config.listing_url_template.format(
            domain=self._forum_config.domain,
            forum_id=self._forum_config.forum_id,
        )
        
        from haven_cli.plugins.builtin.bittorrent.sources.steps import FetchHtmlStep
        
        fetch_step = FetchHtmlStep(url_template=listing_url)
        context = await fetch_step.execute(ExtractionContext())
        
        if not context.raw_html:
            print(f"Failed to fetch listing page: {listing_url}")
            return []
        
        # Extract thread URLs
        thread_urls = self._extract_thread_urls(context.raw_html)
        
        if not thread_urls:
            print("No thread URLs found on listing page")
            return []
        
        # Limit to max_threads
        thread_urls = thread_urls[:self._forum_config.max_threads]
        
        print(f"Found {len(thread_urls)} threads, processing...")
        
        # Process each thread
        all_magnets: List[MagnetLink] = []
        for thread_url in thread_urls:
            try:
                magnets = await self._process_thread(thread_url)
                all_magnets.extend(magnets)
            except Exception as e:
                print(f"Error processing thread {thread_url}: {e}")
        
        return all_magnets
    
    def _extract_thread_urls(self, html: str) -> List[str]:
        """Extract thread URLs from forum listing page.
        
        Args:
            html: HTML content of listing page
            
        Returns:
            List of thread URLs
        """
        from bs4 import BeautifulSoup
        
        soup = BeautifulSoup(html, "html.parser")
        urls = []
        
        # Find all thread links in the listing
        # Pattern: /htm_data/YYMM/fid/threadid.html
        for link in soup.select("tr.tr3.t_one.tac h3 a"):
            href = link.get("href", "")
            if href.startswith("/htm_data/") and href.endswith(".html"):
                # Build full URL
                full_url = f"https://{self._forum_config.domain}{href}"
                urls.append(full_url)
        
        return urls
    
    async def _process_thread(self, thread_url: str) -> List[MagnetLink]:
        """Process a single thread page to extract magnet links.
        
        Args:
            thread_url: URL of the thread page
            
        Returns:
            List of magnet links from this thread
        """
        from haven_cli.plugins.builtin.bittorrent.sources.steps import FetchHtmlStep
        
        # Fetch thread page
        fetch_step = FetchHtmlStep(url_template=thread_url)
        context = await fetch_step.execute(ExtractionContext())
        
        if not context.raw_html:
            print(f"Failed to fetch thread page: {thread_url}")
            return []
        
        # Extract title
        title_match = re.search(self._forum_config.title_pattern, context.raw_html)
        title = title_match.group(1) if title_match else ""
        
        # Clean up title (remove forum name, etc.)
        title = self._clean_title(title)
        
        # Extract size
        size = 0
        size_match = re.search(self._forum_config.size_pattern, context.raw_html)
        if size_match:
            size_value = float(size_match.group(1))
            size_unit = size_match.group(2).upper()
            
            multipliers = {
                "KB": 1024,
                "MB": 1024 ** 2,
                "GB": 1024 ** 3,
            }
            size = int(size_value * multipliers.get(size_unit, 1))
        
        # Priority 1: Extract rmdown link directly from the page
        rmdown_link = None
        if self._forum_config.use_rmdown:
            rmdown_match = re.search(
                r'(https?://(?:www\.)?rmdown\.com/link\.php\?hash=[A-Za-z0-9]+)',
                context.raw_html,
            )
            if rmdown_match:
                rmdown_link = rmdown_match.group(1)
        
        # Get magnet URI and infohash - prioritize rmdown
        magnet_uri = None
        infohash = None
        
        if rmdown_link:
            # Get full magnet from rmdown (includes trackers)
            magnet_uri = await self._get_magnet_from_rmdown(rmdown_link)
            if magnet_uri:
                # Extract infohash from magnet URI
                infohash_match = re.search(r'xt=urn:btih:([A-Fa-f0-9]{40})', magnet_uri, re.IGNORECASE)
                if infohash_match:
                    infohash = infohash_match.group(1).lower()
        
        # Priority 2: Fall back to infohash pattern if no rmdown
        if not infohash:
            infohash_match = re.search(
                self._forum_config.infohash_pattern,
                context.raw_html,
            )
            if infohash_match:
                infohash = infohash_match.group(1).lower()

        # Priority 3: Some posts no longer expose the 【特徵全碼】： marker
        # and only embed the infohash inside the rmdown link's hash= param.
        # The rmdown hash can be longer than 40 chars (it may carry a short
        # prefix); the real infohash is always the trailing 40 hex chars, so
        # take the last 40 to be safe. Use this as a final fallback so either
        # post layout yields a working magnet.
        if not infohash and rmdown_link:
            hash_match = re.search(
                r'hash=([A-Fa-f0-9]+)', rmdown_link, re.IGNORECASE
            )
            if hash_match:
                candidate = hash_match.group(1).lower()
                if len(candidate) >= 40:
                    infohash = candidate[-40:]

        if not infohash:
            print(f"No infohash found in thread: {thread_url}")
            return []
        
        # If we still don't have magnet URI, build from infohash
        if not magnet_uri:
            magnet_uri = await self._get_magnet_uri(infohash, rmdown_link)
        
        # Create MagnetLink
        try:
            magnet = MagnetLink(
                infohash=infohash,
                uri=magnet_uri,
                title=title,
                size=size,
                category="video",
                source_name=self.name,
                metadata={
                    "thread_url": thread_url,
                    "domain": self._forum_config.domain,
                    "forum_id": self._forum_config.forum_id,
                    "from_rmdown": rmdown_link is not None,
                },
            )
            tracker_count = magnet_uri.count("&tr=") if "&tr=" in magnet_uri else 0
            print(f"Extracted: {title[:50]}... ({size / (1024**3):.2f} GB) - {tracker_count} trackers")
            return [magnet]
        except ValueError as e:
            print(f"Failed to create magnet link: {e}")
            return []
    
    @with_exponential_backoff(
        max_retries=7,
        base_delay=5.0,
        max_delay=300.0,
        retryable_status_codes=(429, 503, 502, 504),
        retryable_exceptions=(httpx.HTTPStatusError,),
    )
    async def _fetch_rmdown_page(self, url: str) -> str:
        """Fetch a page from rmdown.com with exponential backoff retry.
        
        This is a helper method that wraps the fetch logic with retry
        capabilities for handling rate limiting (HTTP 429).
        
        Args:
            url: The URL to fetch
            
        Returns:
            The HTML content of the page
            
        Raises:
            httpx.HTTPStatusError: If the request fails with a non-retryable status
        """
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.text

    async def _get_magnet_from_rmdown(self, rmdown_link: str) -> Optional[str]:
        """Fetch magnet URI from rmdown.com.
        
        Args:
            rmdown_link: The rmdown link extracted from the forum page
            
        Returns:
            Magnet URI string or None if failed
        """
        try:
            # First, fetch the rmdown page to get the reff parameter
            # This uses exponential backoff for handling HTTP 429 errors
            html = await self._fetch_rmdown_page(rmdown_link)
            
            if html:
                # Extract ref from URL or hidden field
                ref_match = re.search(r'hash=([A-Za-z0-9]+)', rmdown_link)
                if not ref_match:
                    ref_match = re.search(r'name="ref"[^>]*value="([^"]+)"', html, re.IGNORECASE)
                
                # Extract reff from hidden field (case-insensitive for NAME= vs name=)
                reff_match = re.search(r'name="reff"[^>]*value="([^"]+)"', html, re.IGNORECASE)
                
                if ref_match and reff_match:
                    ref = ref_match.group(1)
                    reff = reff_match.group(1)
                    
                    # Fetch the magnet link from download.php
                    import urllib.parse
                    magnet_url = f"https://rmdown.com/download.php?action=magnet&ref={ref}&reff={urllib.parse.quote(reff)}"
                    
                    # Use the same retry logic for the magnet URL
                    magnet_html = await self._fetch_rmdown_page(magnet_url)
                    
                    if magnet_html:
                        # The response is the magnet link directly
                        magnet_uri = magnet_html.strip()
                        if magnet_uri.startswith('magnet:'):
                            return magnet_uri
                        else:
                            print(f"Warning: rmdown returned non-magnet response: {magnet_uri[:100]}")
                else:
                    print(f"Warning: Could not extract ref/reff from rmdown page")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                print(f"Warning: Rate limited by rmdown.com after retries, using fallback magnet")
            else:
                print(f"Warning: HTTP error from rmdown.com: {e}")
        except Exception as e:
            print(f"Warning: Failed to fetch magnet from rmdown: {e}")
        
        return None
    
    async def _get_magnet_uri(self, infohash: str, rmdown_link: Optional[str] = None) -> str:
        """Get magnet URI, either from rmdown or build from infohash.
        
        Args:
            infohash: The torrent infohash
            rmdown_link: The rmdown link extracted from the forum page (optional)
            
        Returns:
            Magnet URI string
        """
        if self._forum_config.use_rmdown and rmdown_link:
            magnet_uri = await self._get_magnet_from_rmdown(rmdown_link)
            if magnet_uri:
                return magnet_uri
        
        # Fallback: build magnet link from infohash with public trackers
        # These are common public trackers that help with peer discovery
        public_trackers = [
            "udp://tracker.opentrackr.org:1337/announce",
            "udp://open.stealth.si:80/announce",
            "udp://tracker.torrent.eu.org:451/announce",
            "udp://tracker.bittor.pw:1337/announce",
            "udp://public.popcorn-tracker.org:6969/announce",
            "udp://tracker.dler.org:6969/announce",
            "udp://exodus.desync.com:6969/announce",
            "udp://open.demonii.com:1337/announce",
"http://bvarf.tracker.sh:2086/announce",
"http://sukebei.tracker.wf:8888/announce",
"udp://open.stealth.si:80/announce",
"udp://tracker.opentrackr.org:1337/announce",
"udp://exodus.desync.com:6969/announce",
"udp://tracker.torrent.eu.org:451/announce",
"http://tracker1.itzmx.com:8080/announce",
"http://tracker2.itzmx.com:6961/announce",
"http://tracker3.itzmx.com:6961/announce",
"http://tracker4.itzmx.com:2710/announce",
"udp://tracker1.itzmx.com:8080/announce",
"udp://tracker2.itzmx.com:6961/announce",
"udp://tracker3.itzmx.com:6961/announce",
"udp://tracker4.itzmx.com:2710/announce",
"http://tracker.opentrackr.org:1337/announce",
"http://1337.abcvg.info/announce",
"http://open.acgtracker.com:1096/announce",
"http://retracker.hotplug.ru:2710/announce",
"http://share.camoe.cn:8080/announce",
"http://t.acg.rip:6699/announce",
"http://t.nyaatracker.com/announce",
"http://tracker.birkenwald.de:6969/announce",
"http://tracker.bt4g.com:2095/announce",
"http://tracker.dler.com:6969/announce",
"http://tracker.openbittorrent.com/announce",
"http://tracker.srv00.com:6969/announce",
"http://tracker.swateam.org.uk:2710/announce",
"http://tracker2.dler.org/announce",
"http://movies.zsw.ca:6969/announce",
"http://fe.dealclub.de:6969/announce",
"http://vps02.net.orel.ru/announce",
"https://1337.abcvg.info/announce",
"http://chouchou.top:8080/announce",
"udp://explodie.org:6969/announce",
"udp://ipv4.tracker.harry.lu/announce",
"udp://ipv6.tracker.harry.lu/announce",
"udp://open.stealth.si/announce",
"udp://retracker.hotplug.ru:2710/announce",
"udp://retracker.lanta-net.ru:2710/announce",
"udp://tr.cili001.com:8070/announce",
"udp://tracker.birkenwald.de:6969/announce",
"udp://tracker.cyberia.is:6969/announce",
"udp://tracker.dler.com:6969/announce",
"udp://tracker.srv00.com:6969/announce",
"udp://tracker.swateam.org.uk:2710/announce",
"udp://tracker.filemail.com:6969/announce",
"udp://tracker.moeking.me:6969/announce",
"udp://movies.zsw.ca:6969/announce",
"udp://fe.dealclub.de:6969/announce",
"udp://tracker.tiny-vps.com:6969/announce",
"udp://chouchou.top:8080/announce",
"udp://tracker.publicbt.com:80/announce",
"udp://tracker.openbittorrent.com:80/announce",
"http://pornograd.net/bt/announce.php",
"http://bt.rutor.org:2710/announce",
"http://pubt.net:2710/announce",
"http://unhide-torrents.org/announce.php",
"udp://tracker.openbittorrent.com:80",
"http://hdreactor.org:2710/announce",
"http://bt.unionpeer.org:777/announce",
"http://tracker.pornoshara.tv:2711/announce",
"udp://tracker.pornoshara.tv:2711/announce",
"http://tracker.prq.to/announce.php",
"http://tracker.prq.to/announce",
"http://local.tracker/announce",
"http://tracker.trackerfix.com/announce",
"udp://9.rarbg.me:2710/announce",
"udp://11.rarbg.me:80/announce",
"udp://10.rarbg.me:80/announce",
"udp://12.rarbg.me:80/announce",
"http://tracker.ex.ua/announce",
"http://exodus.desync.com:6969/announce",
"udp://tracker.istole.it:80",
"udp://fr33domtracker.h33t.com:3310/announce",
"http://tracker.desibbrg.com:2710/announce",
"udp://tracker.1337x.org:80/announce",
"udp://tracker.ccc.de:80/announce",
"http://tracker.thepiratebay.org/announce",
"http://tv.tracker.prq.to/announce",
"http://tracker.bitebbs.com:6969/announce",
"http://tracker.ydy.com:87/announce",
"http://btfans.3322.org:8080/announce",
"http://btfans.3322.org:6969/announce",
"http://btfans.3322.org:8000/announce",
"http://tracker.torrent.to:2710/announce",
"http://publictracker.org/announce.php",
"http://gamebt.ali213.net:8000/announce",
"http://inferno.demonoid.com:3413/announce",
"http://denis.stalker.h3q.com:6969/announce",
"http://open.tracker.thepiratebay.org/announce",
"http://tpb.tracker.thepiratebay.org/announce",
"http://ezard.ma.cx:6969/announce",
"http://torrentz.ofgods.com:2710/announce",
"http://bt-dbfr.shonencenter.net:8000/announce",
"http://tracker9.bol.bg/announce.php",
"http://tracker.piecesnbits.net:2710/announce",
"http://tracker.paradise-tracker.com:9999/announce",
"http://unme.findhere.org:2710/announce",
"http://kende.9999mb.com/announce.php",
"udp://denis.stalker.h3q.com:6969/announce",
"udp://bt.romman.net:6969/announce",
"udp://www.lamsoft.net:6969/announce",
"udp://bt1.btally.net:6969/announce",
"udp://tracker.lamsoft.net:6969/announce",
"udp://bt1.511yly.com:6969/announce",
"udp://bt1.125a.net:6969/announce",
"http://a.tracker.thepiratebay.org/announce",
"http://a.tracker.thepiratebay.org/announce.php",
"http://tpb.tracker.thepiratebay.org/announce.php",
"http://open.tracker.thepiratebay.org/announce.php",
"http://denis.stalker.h3q.com:6969/announce.php",
"http://eztv.tracker.thepiratebay.org/announce",
"http://eztv.tracker.thepiratebay.org/announce.php",
"http://vip.tracker.thepiratebay.org/announce",
"http://vip.tracker.thepiratebay.org/announce.php",
"http://tv.tracker.prq.to/announce.php",
"http://tracker.thepiratebay.org/announce.php",
"http://tracker1.torrentum.pl:6969/announce",
"http://tracker1.torrentum.pl:6969/announce.php",
"udp://denis.stalker.h3q.com:6969/announce.php",
"udp://open.demonii.com:1337",
"udp://ipv4.tracker.harry.lu:80/announce",
"udp://open.nyaatorrents.info:6544/announce",
"http://open.nyaatorrents.info:6544/announce",
"http://extremlymtorrents.com/announce.php",
"udp://tracker.bittor.pw:1337/announce",
"udp://tracker.dler.org:6969/announce",
"udp://opentracker.i2p.rocks:6969/announce",
"udp://tracker.internetwarriors.net:1337/announce",
"udp://tracker.leechers-paradise.org:6969/announce",
"udp://coppersurfer.tk:6969/announce",
"udp://tracker.zer0day.to:1337/announce",
"http://opentracker.i2p.rocks:6969/announce",
"udp://open.demonii.com:1337/announce",
"udp://tracker.theoks.net:6969/announce",
"udp://wepzone.net:6969/announce",
"udp://udp.tracker.projectk.org:23333/announce",
"udp://ttk2.nbaonlineservice.com:6969/announce",
"udp://tracker2.dler.org:80/announce",
"udp://tracker.zupix.online:6969/announce",
"udp://tracker.tryhackx.org:6969/announce",
"udp://tracker.torrust-demo.com:6969/announce",
"udp://tracker.therarbg.to:6969/announce",
"udp://tracker.skillindia.site:6969/announce",
"udp://tracker.qu.ax:6969/announce",
"udp://tracker.plx.im:6969/announce",
"udp://tracker.hifitechindia.com:6969/announce",
"udp://tracker.hifimarket.in:2710/announce",
"udp://tracker.healthcareindia.store:1337/announce",
"udp://tracker.gmi.gd:6969/announce",
"udp://tracker.bitcoinindia.space:6969/announce",
"udp://tracker-udp.gbitt.info:80/announce",
"udp://t.overflow.biz:6969/announce",
"udp://run.publictracker.xyz:6969/announce",
"udp://retracker01-msk-virt.corbina.net:80/announce",
"udp://retracker.lanta.me:2710/announce",
"udp://public.tracker.vraphim.com:6969/announce",
"udp://p4p.arenabg.com:1337/announce",
"udp://opentracker.io:6969/announce",
"udp://open.free-tracker.ga:6969/announce",
"udp://open.dstud.io:6969/announce",
"udp://leet-tracker.moe:1337/announce",
"udp://ipv4announce.sktorrent.eu:6969/announce",
"udp://hificode.in:6969/announce",
"udp://evan.im:6969/announce",
"udp://d40969.acod.regrucolo.ru:6969/announce",
"udp://bittorrent-tracker.e-n-c-r-y-p-t.net:1337/announce",
"udp://bandito.byterunner.io:6969/announce",
"udp://6ahddutb1ucc3cp.ru:6969/announce",
"udp://1c.premierzal.ru:6969/announce",
"https://tracker.zhuqiy.top:443/announce",
"https://tracker.yemekyedim.com:443/announce",
"https://tracker.pmman.tech:443/announce",
"https://tracker.moeblog.cn:443/announce",
"https://tracker.leechshield.link:443/announce",
"https://tracker.ghostchu-services.top:443/announce",
"https://tracker.cutie.dating:443/announce",
"https://tracker.bt4g.com:443/announce",
"https://tracker.aburaya.live:443/announce",
"https://tr.zukizuki.org:443/announce",
"https://t1.hloli.org:443/announce",
"http://www.torrentsnipe.info:2701/announce",
"http://www.genesis-sp.org:2710/announce",
"http://wepzone.net:6969/announce",
"http://tracker810.xyz:11450/announce",
"http://tracker2.dler.org:80/announce",
"http://tracker1.bt.moack.co.kr:80/announce",
"http://tracker.zhuqiy.top:80/announce",
"http://tracker.xiaoduola.xyz:6969/announce",
"http://tracker.waaa.moe:6969/announce",
"http://tracker.vanitycore.co:6969/announce",
"http://tracker.sbsub.com:2710/announce",
"http://tracker.renfei.net:8080/announce",
"http://tracker.qu.ax:6969/announce",
"http://tracker.privateseedbox.xyz:2710/announce",
"http://tracker.mywaifu.best:6969/announce",
"http://tracker.moxing.party:6969/announce",
"https://tracker.belmult.online:443/announce",
"https://tr.nyacat.pw:443/announce",
"https://tracker.gcrenwp.top:443/announce",
"https://tracker.jdx3.org:443/announce",
"https://tracker.zhuqiy.com:443/announce",
"https://tracker.alaskantf.com:443/announce",
"http://bittorrent-tracker.e-n-c-r-y-p-t.net:1337/announce",
"http://tracker.beeimg.com:6969/announce",
"http://taciturn-shadow.spb.ru:6969/announce",
"http://tracker.ipv6tracker.ru:80/announce",
"http://lucke.fenesisu.moe:6969/announce",
"http://tracker.lintk.me:2710/announce"
        ]
        
        tracker_params = "&tr=".join(public_trackers)
        return f"magnet:?xt=urn:btih:{infohash}&tr={tracker_params}"
    
    def _clean_title(self, title: str) -> str:
        """Clean up thread title.
        
        Args:
            title: Raw title from HTML
            
        Returns:
            Cleaned title
        """
        # Remove forum name and other suffixes
        title = re.sub(r"\s*-\s*.*?\|\s*.*$", "", title)
        title = re.sub(r"\s*-\s*.*?-\s*.*$", "", title)
        title = title.strip()
        
        return title
    
    async def health_check(self) -> SourceHealth:
        """Check the health of this source.
        
        Returns:
            SourceHealth with status and details
        """
        if not self.enabled:
            return SourceHealth(
                status=SourceHealthStatus.UNKNOWN,
                message="Source is disabled",
            )
        
        try:
            # Try to fetch listing page
            listing_url = self._forum_config.listing_url_template.format(
                domain=self._forum_config.domain,
                forum_id=self._forum_config.forum_id,
            )
            
            from haven_cli.plugins.builtin.bittorrent.sources.steps import FetchHtmlStep
            
            fetch_step = FetchHtmlStep(url_template=listing_url)
            context = await fetch_step.execute(ExtractionContext())
            
            if not context.raw_html:
                return SourceHealth(
                    status=SourceHealthStatus.UNHEALTHY,
                    message=f"Failed to fetch listing page: {listing_url}",
                )
            
            # Check if we can find thread URLs
            thread_urls = self._extract_thread_urls(context.raw_html)
            
            if thread_urls:
                return SourceHealth(
                    status=SourceHealthStatus.HEALTHY,
                    message=f"Found {len(thread_urls)} threads",
                    details={
                        "thread_count": len(thread_urls),
                        "listing_url": listing_url,
                        "use_rmdown": self._forum_config.use_rmdown,
                    },
                )
            else:
                return SourceHealth(
                    status=SourceHealthStatus.DEGRADED,
                    message="No thread URLs found",
                    details={"listing_url": listing_url},
                )
        except Exception as e:
            return SourceHealth(
                status=SourceHealthStatus.UNHEALTHY,
                message=f"Health check failed: {e}",
                details={"error": str(e)},
            )
    
    def validate_config(self) -> List[str]:
        """Validate the forum source configuration.
        
        Returns:
            List of validation error messages (empty if valid)
        """
        errors = super().validate_config()
        
        if not self._forum_config.domain:
            errors.append("Domain is required")
        
        if not self._forum_config.forum_id:
            errors.append("Forum ID is required")
        
        return errors
    
    def __repr__(self) -> str:
        """String representation of the source."""
        return (
            f"ForumScraperSource("
            f"name={self.name!r}, "
            f"domain={self._forum_config.domain!r}, "
            f"forum_id={self._forum_config.forum_id!r}, "
            f"enabled={self.enabled}, "
            f"use_rmdown={self._forum_config.use_rmdown}"
            f")"
        )
