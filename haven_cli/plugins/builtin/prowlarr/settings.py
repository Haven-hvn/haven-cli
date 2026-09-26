"""Configuration model for :class:`ProwlarrPlugin`.

Settings live in ``[plugins.settings.ProwlarrPlugin]`` with one
``[[plugins.settings.ProwlarrPlugin.searches]]`` table per saved search.
Keys are flat (no nested tables) so ``haven config`` can round-trip them.
Every search inherits plugin-level defaults and may override any of the
*inheritable* keys listed in :data:`INHERITABLE_KEYS`.

Secrets are never read from the TOML file itself: each secret is given
as ``<name>_env`` (environment variable name) or ``<name>_file`` (path to
a file containing only the secret).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from haven_cli.acquisition.importer import IMPORT_MODES
from haven_cli.media.filetype import MEDIA_KINDS
from haven_cli.services.prowlarr import SEARCH_TYPES, STANDARD_CATEGORIES

FETCH_MODES = ("auto", "prowlarr", "direct", "grab")
TORRENT_CLIENTS = ("internal", "qbittorrent", "transmission", "prowlarr", "none")
USENET_CLIENTS = ("sabnzbd", "nzbget", "prowlarr", "none")
SORT_KEYS = ("publish_date", "seeders", "size", "grabs", "relevance", "title")
DEDUPE_MODES = ("auto", "info_hash", "title_size", "title", "none")
MULTI_FILE_MODES = ("each", "tar", "largest")
PROTOCOLS = ("any", "torrent", "usenet")
LINK_FIELDS = ("info_url", "guid", "comment_url", "download_url", "magnet_url")

#: Newznab query tokens Prowlarr parses out of the query string, per mode.
QUERY_TOKENS = (
    "imdbid",
    "tmdbid",
    "tvdbid",
    "tvmazeid",
    "traktid",
    "doubanid",
    "rid",
    "season",
    "episode",
    "year",
    "genre",
    "author",
    "title",
    "publisher",
    "artist",
    "album",
    "track",
    "label",
)

_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]i?b?|b)?\s*$", re.IGNORECASE)
_UNITS = {"": 1, "b": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}


class SettingsError(ValueError):
    """Invalid ProwlarrPlugin configuration."""


def parse_size(value: Any, key: str) -> int:
    """Bytes from an int or a string like ``"500MB"`` / ``"1.5 GiB"``."""
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        raise SettingsError(f"{key}: expected a size, got a boolean")
    if isinstance(value, (int, float)):
        if value < 0:
            raise SettingsError(f"{key}: must not be negative")
        return int(value)
    match = _SIZE.match(str(value))
    if not match:
        raise SettingsError(
            f"{key}: cannot parse size {value!r} (examples: 1048576, '500MB', '2GiB')"
        )
    unit = (match.group(2) or "").lower()[:1]
    return int(float(match.group(1)) * _UNITS[unit])


def _list(value: Any, key: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [value]


def _str_list(value: Any, key: str) -> list[str]:
    return [str(v) for v in _list(value, key)]


def _int_list(value: Any, key: str) -> list[int]:
    out: list[int] = []
    for item in _list(value, key):
        try:
            out.append(int(item))
        except (TypeError, ValueError) as exc:
            raise SettingsError(f"{key}: {item!r} is not an integer") from exc
    return out


def _choice(value: Any, choices: tuple[str, ...], key: str) -> str:
    text = str(value).lower()
    if text not in choices:
        raise SettingsError(f"{key}: must be one of {', '.join(choices)} (got {value!r})")
    return text


def _bool(value: Any, key: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "yes", "1", "on"):
        return True
    if isinstance(value, str) and value.lower() in ("false", "no", "0", "off"):
        return False
    if isinstance(value, int):
        return bool(value)
    raise SettingsError(f"{key}: expected true/false, got {value!r}")


def _tri(value: Any, key: str) -> bool | None:
    """``"auto"`` → None, else bool."""
    if isinstance(value, str) and value.lower() == "auto":
        return None
    return _bool(value, key)


def _number(value: Any, key: str, *, minimum: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SettingsError(f"{key}: expected a number, got {value!r}") from exc
    if number < minimum:
        raise SettingsError(f"{key}: must be ≥ {minimum}")
    return number


def _regexes(value: Any, key: str) -> list[str]:
    patterns = _str_list(value, key)
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise SettingsError(f"{key}: invalid regular expression {pattern!r}: {exc}") from exc
    return patterns


def _patterns(value: Any, key: str) -> list[str]:
    """Kinds (``document``) or MIME globs (``image/*``)."""
    items = [s.strip().lower() for s in _str_list(value, key)]
    for item in items:
        if item in ("*", "any", "*/*") or item in MEDIA_KINDS or "/" in item:
            continue
        raise SettingsError(
            f"{key}: {item!r} is neither a kind ({', '.join(MEDIA_KINDS)}) "
            "nor a MIME pattern like 'image/*'"
        )
    return items


def read_secret(settings: dict[str, Any], base: str, *, default_env: str | None = None) -> str:
    """Resolve ``<base>_env`` / ``<base>_file`` (env var wins), or ``""``."""
    env_name = settings.get(f"{base}_env") or default_env
    if env_name:
        value = os.environ.get(str(env_name), "")
        if value:
            return value.strip()
    file_name = settings.get(f"{base}_file")
    if file_name:
        path = Path(str(file_name)).expanduser()
        try:
            return path.read_text("utf-8").strip()
        except OSError as exc:
            raise SettingsError(f"{base}_file: cannot read {path}: {exc}") from exc
    return ""


# ── Search spec ──────────────────────────────────────────────────────────


@dataclass
class SearchSpec:
    """One saved search, fully resolved against plugin defaults."""

    name: str
    enabled: bool = True
    query: str = ""
    type: str = "search"
    tokens: dict[str, str] = field(default_factory=dict)
    indexer_ids: list[int] = field(default_factory=list)
    indexers: list[str] = field(default_factory=list)
    indexer_tags: list[str] = field(default_factory=list)
    protocol: str = "any"
    categories: list[int] = field(default_factory=list)
    limit: int = 100
    pages: int = 1
    max_results: int = 25
    max_age_hours: float = 0.0
    min_size: int = 0
    max_size: int = 0
    min_seeders: int = 0
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    sort: str = "publish_date"
    order: str = "desc"
    dedupe: str = "auto"
    # acquisition (inheritable)
    fetch: str = "auto"
    link_field: str = "info_url"
    link_pattern: str = ""
    link_replacement: str = ""
    allowed_hosts: list[str] = field(default_factory=list)
    accept: list[str] = field(default_factory=lambda: ["*"])
    reject: list[str] = field(default_factory=lambda: ["text/html"])
    select_mode: str = "all"
    multi_file: str = "each"
    import_mode: str = ""
    torrent_client: str = "internal"
    usenet_client: str = "none"
    download_client_id: int = 0
    # presentation / pipeline (inheritable)
    title_template: str = "{title}"
    creator_template: str = ""
    priority: str = "medium"
    publish_source: bool | None = None
    arkiv_release_meta: bool | None = None
    pipeline_options: dict[str, Any] = field(default_factory=dict)

    def compiled_query(self) -> str:
        """The query string with Newznab tokens appended (``{season:1}``)."""
        parts = [self.query.strip()] if self.query.strip() else []
        parts.extend(f"{{{k}:{v}}}" for k, v in self.tokens.items())
        return " ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


#: Keys a search inherits from plugin-level settings when not set itself.
INHERITABLE_KEYS = (
    "limit",
    "pages",
    "max_results",
    "max_age_hours",
    "min_size",
    "max_size",
    "min_seeders",
    "include",
    "exclude",
    "sort",
    "order",
    "dedupe",
    "protocol",
    "fetch",
    "link_field",
    "link_pattern",
    "link_replacement",
    "allowed_hosts",
    "accept",
    "reject",
    "select_mode",
    "multi_file",
    "import_mode",
    "torrent_client",
    "usenet_client",
    "download_client_id",
    "title_template",
    "creator_template",
    "priority",
    "publish_source",
    "arkiv_release_meta",
    "indexer_ids",
    "indexers",
    "indexer_tags",
    "categories",
    "type",
)

_TOKEN_ALIASES = {
    "imdb_id": "imdbid",
    "tmdb_id": "tmdbid",
    "tvdb_id": "tvdbid",
    "tvmaze_id": "tvmazeid",
    "trakt_id": "traktid",
    "douban_id": "doubanid",
    "tvrage_id": "rid",
}


def _categories(value: Any, key: str) -> list[int]:
    out: list[int] = []
    for item in _list(value, key):
        if isinstance(item, int) and not isinstance(item, bool):
            out.append(item)
            continue
        text = str(item).strip()
        if text.isdigit():
            out.append(int(text))
        elif text.lower() in STANDARD_CATEGORIES:
            out.append(STANDARD_CATEGORIES[text.lower()])
        else:
            raise SettingsError(
                f"{key}: unknown category {item!r}; use a Newznab id or one of "
                f"{', '.join(STANDARD_CATEGORIES)}"
            )
    return out


def build_search(raw: dict[str, Any], defaults: dict[str, Any], *, where: str) -> SearchSpec:
    """Resolve one search table against plugin *defaults*."""
    if not isinstance(raw, dict):
        raise SettingsError(f"{where}: each search must be a table")
    merged: dict[str, Any] = {k: defaults[k] for k in INHERITABLE_KEYS if k in defaults}
    merged.update(raw)
    name = str(merged.get("name", "")).strip()
    if not name:
        raise SettingsError(f"{where}: search needs a unique 'name'")
    at = f"{where} '{name}'"
    known = {f.name for f in fields(SearchSpec)} | set(QUERY_TOKENS) | set(_TOKEN_ALIASES)
    unknown = sorted(set(raw) - known)
    if unknown:
        raise SettingsError(f"{at}: unknown keys {', '.join(unknown)}")

    tokens: dict[str, str] = {}
    for key in list(QUERY_TOKENS) + list(_TOKEN_ALIASES):
        if key in raw and raw[key] not in (None, ""):
            token = _TOKEN_ALIASES.get(key, key)
            value = str(raw[key]).strip()
            if "{" in value or "}" in value:
                raise SettingsError(f"{at}: {key} must not contain braces")
            tokens[token] = value

    spec = SearchSpec(name=name)
    spec.enabled = _bool(merged.get("enabled", True), f"{at}.enabled")
    spec.query = str(merged.get("query", "") or "")
    spec.type = _choice(merged.get("type", "search"), SEARCH_TYPES, f"{at}.type")
    spec.tokens = tokens
    spec.indexer_ids = _int_list(merged.get("indexer_ids"), f"{at}.indexer_ids")
    spec.indexers = _str_list(merged.get("indexers"), f"{at}.indexers")
    spec.indexer_tags = _str_list(merged.get("indexer_tags"), f"{at}.indexer_tags")
    spec.protocol = _choice(merged.get("protocol", "any"), PROTOCOLS, f"{at}.protocol")
    spec.categories = _categories(merged.get("categories"), f"{at}.categories")
    spec.limit = int(_number(merged.get("limit", 100), f"{at}.limit", minimum=1))
    spec.pages = int(_number(merged.get("pages", 1), f"{at}.pages", minimum=1))
    spec.max_results = int(_number(merged.get("max_results", 25), f"{at}.max_results", minimum=1))
    spec.max_age_hours = _number(merged.get("max_age_hours", 0) or 0, f"{at}.max_age_hours")
    if "max_age_days" in raw:
        raise SettingsError(f"{at}: use max_age_hours")
    spec.min_size = parse_size(merged.get("min_size"), f"{at}.min_size")
    spec.max_size = parse_size(merged.get("max_size"), f"{at}.max_size")
    spec.min_seeders = int(_number(merged.get("min_seeders", 0) or 0, f"{at}.min_seeders"))
    spec.include = _regexes(merged.get("include"), f"{at}.include")
    spec.exclude = _regexes(merged.get("exclude"), f"{at}.exclude")
    spec.sort = _choice(merged.get("sort", "publish_date"), SORT_KEYS, f"{at}.sort")
    spec.order = _choice(merged.get("order", "desc"), ("asc", "desc"), f"{at}.order")
    spec.dedupe = _choice(merged.get("dedupe", "auto"), DEDUPE_MODES, f"{at}.dedupe")
    spec.fetch = _choice(merged.get("fetch", "auto"), FETCH_MODES, f"{at}.fetch")
    spec.link_field = _choice(merged.get("link_field", "info_url"), LINK_FIELDS, f"{at}.link_field")
    spec.link_pattern = str(merged.get("link_pattern", "") or "")
    spec.link_replacement = str(merged.get("link_replacement", "") or "")
    if spec.link_pattern:
        try:
            re.compile(spec.link_pattern)
        except re.error as exc:
            raise SettingsError(f"{at}.link_pattern: {exc}") from exc
        if not spec.link_replacement:
            raise SettingsError(f"{at}: link_pattern requires link_replacement")
    spec.allowed_hosts = _str_list(merged.get("allowed_hosts"), f"{at}.allowed_hosts")
    spec.accept = _patterns(merged.get("accept", ["*"]), f"{at}.accept") or ["*"]
    spec.reject = _patterns(merged.get("reject", ["text/html"]), f"{at}.reject")
    spec.select_mode = _choice(
        merged.get("select_mode", "all"), ("all", "largest"), f"{at}.select_mode"
    )
    spec.multi_file = _choice(
        merged.get("multi_file", "each"), MULTI_FILE_MODES, f"{at}.multi_file"
    )
    import_mode = str(merged.get("import_mode", "") or "")
    if import_mode:
        _choice(import_mode, IMPORT_MODES, f"{at}.import_mode")
    spec.import_mode = import_mode.lower()
    spec.torrent_client = _choice(
        merged.get("torrent_client", "internal"), TORRENT_CLIENTS, f"{at}.torrent_client"
    )
    spec.usenet_client = _choice(
        merged.get("usenet_client", "none"), USENET_CLIENTS, f"{at}.usenet_client"
    )
    spec.download_client_id = int(
        _number(merged.get("download_client_id", 0) or 0, f"{at}.download_client_id")
    )
    spec.title_template = str(merged.get("title_template", "{title}") or "{title}")
    spec.creator_template = str(merged.get("creator_template", "") or "")
    for template_key in ("title_template", "creator_template"):
        validate_template(getattr(spec, template_key), f"{at}.{template_key}")
    spec.priority = _choice(
        merged.get("priority", "medium"), ("high", "medium", "low"), f"{at}.priority"
    )
    spec.publish_source = _tri(merged.get("publish_source", "auto"), f"{at}.publish_source")
    spec.arkiv_release_meta = _tri(
        merged.get("arkiv_release_meta", "auto"), f"{at}.arkiv_release_meta"
    )
    options = {**(defaults.get("pipeline_options") or {}), **(raw.get("pipeline_options") or {})}
    if not isinstance(options, dict):
        raise SettingsError(f"{at}.pipeline_options must be a table")
    spec.pipeline_options = options
    return spec


# ── Templates ────────────────────────────────────────────────────────────

_TEMPLATE_FIELD = re.compile(r"\{([^{}]*)\}")
TEMPLATE_FIELDS = (
    "title",
    "guid",
    "indexer",
    "indexer_id",
    "protocol",
    "publish_date",
    "year",
    "size",
    "info_hash",
    "categories",
    "category_ids",
    "info_url",
    "comment_url",
    "imdb_id",
    "tmdb_id",
    "tvdb_id",
    "search",
    "file",
)


def validate_template(template: str, key: str) -> None:
    for match in _TEMPLATE_FIELD.finditer(template):
        name = match.group(1)
        if name not in TEMPLATE_FIELDS:
            raise SettingsError(
                f"{key}: unknown field {{{name}}}; available: {', '.join(TEMPLATE_FIELDS)}"
            )


def render_template(template: str, values: dict[str, Any]) -> str:
    """Substitute ``{field}`` placeholders (no attribute/index access)."""
    return _TEMPLATE_FIELD.sub(lambda m: str(values.get(m.group(1), "")), template).strip()


# ── Plugin settings ──────────────────────────────────────────────────────


@dataclass
class ProwlarrSettings:
    base_url: str = "http://localhost:9696"
    api_key: str = field(default="", repr=False)
    timeout: float = 90.0
    verify: bool | str = True
    download_dir: Path = Path("~/.haven/prowlarr/downloads")
    state_file: Path = Path("~/.haven/prowlarr/state.json")
    max_file_bytes: int = 4 * 1024**3
    allow_private_hosts: bool = False
    min_request_interval: float = 0.0
    fetch_timeout: float = 300.0
    wait_timeout: float = 600.0
    poll_interval: float = 15.0
    max_attempts: int = 3
    retry_backoff: float = 1800.0
    max_sources_per_run: int = 100
    watch_dir: Path | None = None
    watch_settle: float = 60.0
    remote_path_mappings: tuple[str, ...] = ()
    torrent_listen: str = "0.0.0.0:0,[::]:0"
    torrent_dht: bool = True
    torrent_seed: bool = False
    torrent_download_rate: int = 0
    torrent_upload_rate: int = 0
    qbittorrent: dict[str, Any] = field(default_factory=dict, repr=False)
    transmission: dict[str, Any] = field(default_factory=dict, repr=False)
    sabnzbd: dict[str, Any] = field(default_factory=dict, repr=False)
    nzbget: dict[str, Any] = field(default_factory=dict, repr=False)
    searches: list[SearchSpec] = field(default_factory=list)
    #: Plugin-level values searches inherit (see INHERITABLE_KEYS).
    defaults: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def search(self, name: str) -> SearchSpec | None:
        return next((s for s in self.searches if s.name == name), None)


_PLUGIN_KEYS = {
    "base_url",
    "api_key_env",
    "api_key_file",
    "timeout_s",
    "verify_tls",
    "ca_bundle",
    "download_dir",
    "state_file",
    "max_file_bytes",
    "allow_private_hosts",
    "min_request_interval_s",
    "fetch_timeout_s",
    "wait_timeout_s",
    "poll_interval_s",
    "max_attempts",
    "retry_backoff_s",
    "max_sources_per_run",
    "watch_dir",
    "watch_settle_s",
    "remote_path_mappings",
    "torrent_listen",
    "torrent_dht",
    "torrent_seed",
    "torrent_download_rate",
    "torrent_upload_rate",
    "pipeline_options",
    "searches",
    "enabled",
}
_CLIENT_PREFIXES = ("qbittorrent_", "transmission_", "sabnzbd_", "nzbget_")


def _client_settings(raw: dict[str, Any], prefix: str) -> dict[str, Any]:
    return {k[len(prefix) :]: v for k, v in raw.items() if k.startswith(prefix)}


def load_settings(raw: dict[str, Any], *, data_dir: Path | None = None) -> ProwlarrSettings:
    """Validate raw plugin settings into :class:`ProwlarrSettings`.

    Raises:
        SettingsError: on any invalid value (message names the key).
    """
    raw = dict(raw or {})
    unknown = sorted(
        k
        for k in raw
        if k not in _PLUGIN_KEYS
        and k not in INHERITABLE_KEYS
        and not k.startswith(_CLIENT_PREFIXES)
    )
    if "api_key" in raw:
        raise SettingsError(
            "ProwlarrPlugin: do not put api_key in config; use api_key_env or api_key_file"
        )

    settings = ProwlarrSettings()
    warnings: list[str] = []
    if unknown:
        # Warn rather than fail: hosts may inject their own keys into plugin config.
        warnings.append(f"ignoring unknown settings: {', '.join(unknown)}")
    settings.base_url = str(
        raw.get("base_url") or os.environ.get("PROWLARR_URL") or settings.base_url
    ).rstrip("/")
    settings.api_key = read_secret(raw, "api_key", default_env="PROWLARR_API_KEY")
    settings.timeout = _number(raw.get("timeout_s", 90), "timeout_s", minimum=1)
    verify: bool | str = _bool(raw.get("verify_tls", True), "verify_tls")
    if raw.get("ca_bundle"):
        verify = str(Path(str(raw["ca_bundle"])).expanduser())
    elif verify is False:
        warnings.append(
            "verify_tls = false disables TLS certificate checks for Prowlarr and downloads"
        )
    settings.verify = verify
    base = (data_dir or Path("~/.haven")).expanduser() / "prowlarr"
    settings.download_dir = Path(str(raw.get("download_dir") or base / "downloads")).expanduser()
    settings.state_file = Path(str(raw.get("state_file") or base / "state.json")).expanduser()
    settings.max_file_bytes = parse_size(
        raw.get("max_file_bytes", settings.max_file_bytes), "max_file_bytes"
    )
    settings.allow_private_hosts = _bool(
        raw.get("allow_private_hosts", False), "allow_private_hosts"
    )
    settings.min_request_interval = _number(
        raw.get("min_request_interval_s", 0), "min_request_interval_s"
    )
    settings.fetch_timeout = _number(raw.get("fetch_timeout_s", 300), "fetch_timeout_s", minimum=1)
    settings.wait_timeout = _number(raw.get("wait_timeout_s", 600), "wait_timeout_s")
    settings.poll_interval = _number(
        raw.get("poll_interval_s", 15), "poll_interval_s", minimum=0.01
    )
    settings.max_attempts = int(_number(raw.get("max_attempts", 3), "max_attempts", minimum=1))
    settings.retry_backoff = _number(raw.get("retry_backoff_s", 1800), "retry_backoff_s")
    settings.max_sources_per_run = int(
        _number(raw.get("max_sources_per_run", 100), "max_sources_per_run", minimum=1)
    )
    if raw.get("watch_dir"):
        settings.watch_dir = Path(str(raw["watch_dir"])).expanduser()
    settings.watch_settle = _number(raw.get("watch_settle_s", 60), "watch_settle_s")
    mappings = _str_list(raw.get("remote_path_mappings"), "remote_path_mappings")
    for mapping in mappings:
        if "=" not in mapping:
            raise SettingsError(
                f"remote_path_mappings: {mapping!r} must look like '/remote=/local'"
            )
    settings.remote_path_mappings = tuple(mappings)
    settings.torrent_listen = str(raw.get("torrent_listen", settings.torrent_listen))
    settings.torrent_dht = _bool(raw.get("torrent_dht", True), "torrent_dht")
    settings.torrent_seed = _bool(raw.get("torrent_seed", False), "torrent_seed")
    settings.torrent_download_rate = parse_size(
        raw.get("torrent_download_rate", 0), "torrent_download_rate"
    )
    settings.torrent_upload_rate = parse_size(
        raw.get("torrent_upload_rate", 0), "torrent_upload_rate"
    )

    for name in ("qbittorrent", "transmission", "sabnzbd", "nzbget"):
        client = _client_settings(raw, f"{name}_")
        for secret_key in ("password", "api_key"):
            if secret_key in client:
                raise SettingsError(
                    f"{name}_{secret_key}: use {name}_{secret_key}_env or {name}_{secret_key}_file"
                )
        setattr(settings, name, client)

    pipeline_options = raw.get("pipeline_options") or {}
    if not isinstance(pipeline_options, dict):
        raise SettingsError("pipeline_options must be a table")
    defaults = {k: raw[k] for k in INHERITABLE_KEYS if k in raw}
    defaults["pipeline_options"] = pipeline_options
    settings.defaults = defaults

    searches_raw = raw.get("searches") or []
    if isinstance(searches_raw, dict):
        searches_raw = [searches_raw]
    if not isinstance(searches_raw, list):
        raise SettingsError(
            "searches must be an array of tables ([[plugins.settings.ProwlarrPlugin.searches]])"
        )
    seen: set[str] = set()
    for index, row in enumerate(searches_raw):
        spec = build_search(row, defaults, where=f"searches[{index}]")
        if spec.name in seen:
            raise SettingsError(f"searches: duplicate name {spec.name!r}")
        seen.add(spec.name)
        settings.searches.append(spec)
    for spec in settings.searches:
        warnings.extend(check_spec(spec, settings))
    settings.warnings = warnings
    return settings


def check_spec(spec: SearchSpec, settings: ProwlarrSettings) -> list[str]:
    """Non-fatal consistency warnings for a search."""
    notes: list[str] = []
    where = f"search '{spec.name}'"
    if spec.fetch == "grab" and settings.watch_dir is None:
        notes.append(f"{where}: fetch = 'grab' needs watch_dir to detect completion")
    if spec.torrent_client == "prowlarr" and settings.watch_dir is None:
        notes.append(f"{where}: torrent_client = 'prowlarr' needs watch_dir")
    if spec.usenet_client == "prowlarr" and settings.watch_dir is None:
        notes.append(f"{where}: usenet_client = 'prowlarr' needs watch_dir")
    for client in (spec.torrent_client, spec.usenet_client):
        external = client in ("qbittorrent", "transmission", "sabnzbd", "nzbget")
        if external and not getattr(settings, client).get("url"):
            notes.append(f"{where}: uses {client} but {client}_url is not set")
    if (
        spec.fetch == "direct"
        and not spec.link_pattern
        and spec.link_field in ("download_url", "magnet_url")
    ):
        notes.append(
            f"{where}: fetch = 'direct' with link_field = {spec.link_field} is a Prowlarr "
            "proxy link; use fetch = 'prowlarr'"
        )
    return notes
