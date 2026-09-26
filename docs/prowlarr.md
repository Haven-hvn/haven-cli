# Prowlarr plugin

`ProwlarrPlugin` runs saved searches against the indexers configured in a
[Prowlarr](https://prowlarr.com) instance on a cron schedule, downloads the
matching releases, and sends every resulting file through the Haven pipeline
(Filecoin/IPFS upload, optional encryption, Arkiv record).

The plugin has no built-in knowledge of any content source. Which indexers,
categories, file types, download clients, and Arkiv groups are used is entirely
configuration.

```
cron ─► Prowlarr /api/v1/search ─► filter / sort / dedupe
     ─► acquire: direct URL │ Prowlarr download proxy │ torrent client │ Usenet client │ Prowlarr grab
     ─► select content files ─► pipeline (ingest → encrypt → upload → Arkiv)
```

## Quick start

```bash
export PROWLARR_API_KEY=...          # Prowlarr → Settings → General → Security
haven prowlarr status                # connectivity + backend health
haven prowlarr indexers              # ids, protocols, search modes, categories
haven prowlarr search "query" -i 3   # ad-hoc search, nothing downloaded
```

Add a saved search to `~/.config/haven/config.toml`:

```toml
[plugins.settings.ProwlarrPlugin]
base_url = "http://localhost:9696"

[[plugins.settings.ProwlarrPlugin.searches]]
name = "reports"
query = "annual report"
indexer_ids = [3]
categories = ["books"]
max_age_hours = 48
accept = ["document"]
```

Preview, then schedule:

```bash
haven prowlarr preview --search reports
haven prowlarr schedule --schedule "0 */6 * * *" --search reports
```

`haven jobs create --plugin ProwlarrPlugin …` works too (see [Job options](#job-options)).

## How a run works

1. **Discovery.** Each selected search calls `GET /api/v1/search`. Results are
   filtered (age, size, seeders, title regexes), sorted, de-duplicated, capped
   at `max_results`, and turned into sources. A source's id is stable across
   runs (the info-hash when known, else indexer id + guid), so the scheduler's
   `archive_new` mode archives each release once.
2. **Acquisition** picks a strategy per release (`fetch`):

   | `fetch` | What happens |
   |---|---|
   | `auto` (default) | `direct` when `link_pattern` is set; `grab` when the release's protocol is routed to `prowlarr`; otherwise `prowlarr` |
   | `prowlarr` | GET Prowlarr's download proxy. A redirect (Usenet indexers, "redirect" indexers) is followed without the API key |
   | `direct` | GET a URL built from a release field (`link_field`, default `info_url`), optionally rewritten by `link_pattern` → `link_replacement` |
   | `grab` | `POST /api/v1/search` so Prowlarr sends the release to *its* download client; completion is detected in `watch_dir` |

   The downloaded bytes are sniffed. Content files are kept if they match
   `accept`/`reject`. A `.torrent` or magnet goes to `torrent_client`, an
   `.nzb` goes to `usenet_client`.
3. **Download clients.** Torrents and NZBs can take longer than one run. The
   plugin records the client handle in `state_file`, waits up to
   `wait_timeout_s`, and otherwise reports the source as pending. Later runs
   poll the client instead of resubmitting, even after the release has
   dropped out of the search results.
4. **Import.** Completed files are filtered by content type and name
   (samples, `.nfo`, `.par2` and similar are skipped). They are then
   hardlinked (default), copied, moved, or used in place, depending on
   `import_mode`, under `download_dir/imports/`.
5. **Pipeline.** Every file is enqueued. Files that are not audio or video use
   the pipeline's generic-file path: no ffprobe, pHash or VLM, sha256 dedup
   still applies, and the Arkiv group comes from the content kind (see
   [Arkiv records](#arkiv-records)).

Failures are classified. A permanent failure (rejected type, 404, invalid
torrent) is recorded and the release is not retried. A transient failure (5xx,
429, network) is retried with exponential backoff from `retry_backoff_s`, up to
`max_attempts`. A configuration problem (bad API key, bad client credentials)
is reported but not recorded against the release, so it is retried once the
setup is fixed. Use `haven prowlarr pending` to inspect records and
`haven prowlarr retry <key|all-failed>` to reset them.

## Configuration reference

### Plugin settings (`[plugins.settings.ProwlarrPlugin]`)

| Key | Default | Meaning |
|---|---|---|
| `base_url` | `$PROWLARR_URL` or `http://localhost:9696` | Prowlarr origin plus any URL base |
| `api_key_env` / `api_key_file` | `PROWLARR_API_KEY` | Where the key comes from. `api_key` in the file is rejected |
| `timeout_s` | 90 | Prowlarr API timeout (searches fan out to indexers) |
| `verify_tls` / `ca_bundle` | true / – | TLS verification for all requests |
| `download_dir` | `<data_dir>/prowlarr/downloads` | Working area |
| `state_file` | `<data_dir>/prowlarr/state.json` | Pending/failed acquisitions (mode 0600) |
| `max_file_bytes` | `4GiB` | Hard cap per downloaded file (also a torrent file-selection cap). Sizes accept `"500MB"`, `"2GiB"` or bytes |
| `allow_private_hosts` | false | Allow direct fetches to loopback/private/link-local addresses |
| `min_request_interval_s` | 0 | Minimum spacing between direct fetches to the same host |
| `fetch_timeout_s` | 300 | Per-download timeout |
| `wait_timeout_s` | 600 | How long one archive call waits on a download client before reporting "pending" |
| `poll_interval_s` | 15 | Client polling interval |
| `max_attempts` / `retry_backoff_s` | 3 / 1800 | Transient-failure retry policy |
| `max_sources_per_run` | 100 | Discovery cap across all searches |
| `watch_dir` / `watch_settle_s` | – / 60 | Completed-downloads directory for `grab` / `prowlarr` routing |
| `remote_path_mappings` | [] | `"/remote=/local"` translations for client-reported paths |
| `torrent_listen`, `torrent_dht`, `torrent_seed`, `torrent_download_rate`, `torrent_upload_rate` | | Built-in libtorrent client |
| `qbittorrent_url`, `_username`, `_password_env`/`_password_file`, `_category`, `_save_path` | | qBittorrent Web API |
| `transmission_url`, `_username`, `_password_env`/`_password_file`, `_download_dir`, `_labels` | | Transmission RPC |
| `sabnzbd_url`, `_api_key_env`/`_api_key_file` (default `$SABNZBD_API_KEY`), `_category`, `_priority` | | SABnzbd |
| `nzbget_url`, `_username`, `_password_env`/`_password_file`, `_category`, `_priority` | | NZBGet |
| `pipeline_options` | {} | Pipeline options for every search (merged under each search's own) |

Any **search key** below may also be set at plugin level as a default.

### Search keys (`[[plugins.settings.ProwlarrPlugin.searches]]`)

| Key | Default | Meaning |
|---|---|---|
| `name` | required | Unique; used by `--search` and job options |
| `enabled` | true | Disabled searches run only when named explicitly |
| `query` | "" | Search terms. Empty asks indexers for their latest releases |
| `type` | `search` | `search`, `tvsearch`, `movie`, `music`, `book` |
| `imdb_id`, `tmdb_id`, `tvdb_id`, `tvmaze_id`, `trakt_id`, `douban_id`, `season`, `episode`, `year`, `genre`, `author`, `title`, `publisher`, `artist`, `album`, `track`, `label` | – | Typed parameters, sent as Prowlarr query tokens (`{season:1}`). Which ones an indexer honours depends on `type` and the indexer (`haven prowlarr indexers --json` lists them) |
| `indexer_ids` / `indexers` / `indexer_tags` | all enabled | Select by id, by name (or definition name), or by Prowlarr tag label. The selections are combined |
| `protocol` | `any` | `torrent` or `usenet` restricts the selection (with no explicit indexers it uses Prowlarr's "all torrent/usenet" selectors) |
| `categories` | [] | Newznab ids or `console, movies, audio, pc, tv, xxx, books, other` |
| `limit` / `pages` | 100 / 1 | Per-request limit and number of pages (offset paging) |
| `max_results` | 25 | Releases kept per run after filtering |
| `max_age_hours`, `min_size`, `max_size`, `min_seeders` | 0 | Filters (0 = off; seeders applies to torrents only) |
| `include` / `exclude` | [] | Case-insensitive regexes on the title (all includes must match; any exclude rejects) |
| `sort` / `order` | `publish_date` / `desc` | Also `seeders`, `size`, `grabs`, `title`, `relevance` (Prowlarr's order) |
| `dedupe` | `auto` | `info_hash`, `title_size`, `title`, `none`; `auto` = info-hash, else title+size |
| `fetch`, `link_field`, `link_pattern`, `link_replacement` | `auto`, `info_url` | Acquisition strategy, see above. `link_field` may be `info_url`, `guid`, `comment_url`, `download_url`, `magnet_url` |
| `allowed_hosts` | [] | Host allowlist for direct fetches and redirect targets (`example.org` includes subdomains) |
| `accept` / `reject` | `["*"]` / `["text/html"]` | Content kinds (`video audio image document text archive other`) or MIME globs (`image/*`) |
| `select_mode` | `all` | `largest` keeps only the biggest matching file of a release |
| `multi_file` | `each` | For multi-file releases: `each` (one record per file), `tar` (one archive), `largest` |
| `import_mode` | `hardlink` (`move` for the built-in client) | `hardlink`, `copy`, `move`, `inplace` |
| `torrent_client` | `internal` | `internal`, `qbittorrent`, `transmission`, `prowlarr` (grab + watch), `none` |
| `usenet_client` | `none` | `sabnzbd`, `nzbget`, `prowlarr`, `none` |
| `download_client_id` | 0 | Prowlarr download client for `grab` (0 = Prowlarr's default) |
| `title_template` / `creator_template` | `{title}` / "" | Fields: `title guid indexer indexer_id protocol publish_date year size info_hash categories category_ids info_url comment_url imdb_id tmdb_id tvdb_id search file` |
| `priority` | `medium` | Source priority |
| `publish_source` | `auto` | Put the release's info URL in the Arkiv payload `src`. `auto` = public indexers only |
| `arkiv_release_meta` | `auto` | Put indexer, protocol, publish date and categories in payload `x`. `auto` = public indexers only |
| `pipeline_options` | {} | Pipeline options for this search's files (see below) |

### Pipeline options

These can be set through `pipeline_options` or as job options:

| Option | Effect |
|---|---|
| `vlm_enabled`, `encrypt`, `upload_enabled`, `arkiv_sync_enabled`, `cleanup_enabled`, `dedup_enabled` | Existing pipeline switches |
| `arkiv_expires_in` / `arkiv_expiration_weeks` | Per-record Arkiv lifetime (overrides `ARKIV_EXPIRATION_WEEKS`) |
| `arkiv_grp` | Override the Arkiv group (lowercase dot hierarchy, e.g. `acme.reports.full`) |
| `generic_file_accept` / `generic_file_reject` | Set automatically from `accept`/`reject` |

## Job options

A job can narrow or replace the saved searches:

```bash
# run two saved searches, keep Arkiv records for a year
haven jobs create --plugin ProwlarrPlugin --schedule "0 3 * * *" \
  -o prowlarr_searches='["reports","datasets"]' -o arkiv_expires_in=31536000

# an inline search (same keys as a saved search)
haven jobs create --plugin ProwlarrPlugin --schedule "*/30 * * * *" \
  --options-json '{"prowlarr_search": {"query": "", "indexers": ["My Indexer"], "max_age_hours": 1}}'
```

`--option KEY=VALUE` values are parsed as JSON when possible. `jobs create`
validates `prowlarr_*` options against your config before saving the job.

## Examples

Each example uses placeholder indexer names and hosts.

**Documents from a public indexer whose info page maps to a file URL.**
Direct fetch, rate-limited, with long-lived Arkiv records:

```toml
[plugins.settings.ProwlarrPlugin]
min_request_interval_s = 3

[[plugins.settings.ProwlarrPlugin.searches]]
name = "papers"
query = "topic of interest"
indexers = ["Example Docs"]
max_age_hours = 24
accept = ["application/pdf"]
fetch = "direct"
link_pattern = '^https://docs\.example\.org/item/(.+)$'
link_replacement = 'https://docs.example.org/download/\1.pdf'
allowed_hosts = ["docs.example.org"]
pipeline_options = { vlm_enabled = false, arkiv_expires_in = 31536000 }
```

`min_request_interval_s` spaces successive direct fetches to the same host at
least 3 s apart.

**TV episodes from torrent indexers, via qBittorrent:**

```toml
[plugins.settings.ProwlarrPlugin]
qbittorrent_url = "http://qbittorrent:8080"
qbittorrent_username = "admin"
qbittorrent_password_env = "QBIT_PASSWORD"
qbittorrent_category = "haven"
remote_path_mappings = ["/downloads=/mnt/media/downloads"]

[[plugins.settings.ProwlarrPlugin.searches]]
name = "show"
type = "tvsearch"
query = "Show Name"
season = 2
protocol = "torrent"
categories = ["tv"]
accept = ["video"]
select_mode = "largest"
torrent_client = "qbittorrent"
min_seeders = 3
```

**Usenet via SABnzbd, keeping every file of a release as one archive:**

```toml
[plugins.settings.ProwlarrPlugin]
sabnzbd_url = "http://sabnzbd:8080"        # key from $SABNZBD_API_KEY

[[plugins.settings.ProwlarrPlugin.searches]]
name = "ebooks"
type = "book"
author = "Some Author"
protocol = "usenet"
usenet_client = "sabnzbd"
accept = ["document", "image"]
multi_file = "tar"
```

**Any client Prowlarr supports (Deluge, rTorrent, …):**

```toml
[plugins.settings.ProwlarrPlugin]
watch_dir = "/mnt/downloads/complete/haven"   # the client's completed dir for Haven grabs

[[plugins.settings.ProwlarrPlugin.searches]]
name = "via-prowlarr"
query = "…"
torrent_client = "prowlarr"
usenet_client = "prowlarr"
download_client_id = 2
```

Completion is matched by release title among new entries in `watch_dir`.
Give Haven its own category or directory to avoid mismatches.

## Arkiv records

| Content | `grp` |
|---|---|
| video, audio (existing path) | `haven.video.full` (unchanged) |
| image | `haven.image.full` |
| document, text | `haven.text.full` |
| archive, other | `haven.file.full` |

Attributes are unchanged: `grp`, `title`, `sha256_ct`, gate fields, `mime`
(enum; omitted when unmapped), and `dur_s` (A/V only). The payload adds `name`
(original file name), `ct` (MIME string when the enum has no code for it) and
`x` (release metadata when `arkiv_release_meta` applies, capped at 2 KB). See
[ARKIV_FORMAT.md](ARKIV_FORMAT.md).

## Security notes

- The Prowlarr API key is sent only in the `X-Api-Key` header, only to
  `base_url`, and never with redirects. Prowlarr embeds `apikey=` in every
  proxy link it returns; the plugin strips it on parse, so no stored source,
  state file, log line or Arkiv record contains it. Error text is redacted.
- **Arkiv records are public and long-lived.** `src` and `x` are only filled for
  public indexers by default. Private-tracker URLs are withheld, and any URL
  that still carries a credential-like parameter or literal secret is dropped.
- Direct fetches (and every redirect hop) must be `http(s)`. They must not
  resolve to loopback, private, link-local or reserved addresses unless
  `allow_private_hosts = true`, and they can be restricted with
  `allowed_hosts`. Downloads are size-capped and written atomically.
- Download-client passwords and API keys are read only from environment
  variables or files. The state file is written with mode `0600`.
- Uploads to IPFS/Filecoin are public unless `encrypt` is enabled. Only
  archive content you have the right to redistribute.

## Troubleshooting

| Symptom | Check |
|---|---|
| `health check failed … redirect` | `base_url` scheme, host or URL base is wrong |
| `unauthorized` | Key in `$PROWLARR_API_KEY` doesn't match Prowlarr |
| Release fails with `text/html` | The indexer returned a login/error page. Use `fetch = "direct"` with a `link_pattern`, or fix the indexer's credentials in Prowlarr |
| `blocked fetch … not public` | Target is on a private network. Set `allow_private_hosts` if intended |
| Stuck "pending" | `haven prowlarr pending`; check the client and `remote_path_mappings` |
| `Grab limit reached` | Prowlarr's per-indexer grab limit. The release is retried after `Retry-After` |
