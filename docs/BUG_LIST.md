# Haven CLI — Bug List (prioritized)

Updated 2026-11-10. Only Haven-AOL v3 bugs live in `docs/HAVEN_AOL_V3_BUGS.md`;
this list covers the rest. Severities: P0 = will fail/misbehave in production
soon, P1 = breaks a real workflow, P2 = correctness/debt, P3 = polish.

## P0 — must fix before relying on the 6h cycle

- **B1. Filecoin lockup is a fixed per-dataset cost, not per-byte.**
  Corrected 2026-11-10 against the live calibration price list
  (`storagePerTibPerMonth` 2.5 USDFC, `datasetFeePerMonth` 0.12,
  lifecycle reserve 0.5, CDN 0.7, cache-miss 0.3). A new dataset locks
  ~1.50 USDFC fixed + ~0.12/month regardless of size: the 18.5MB test
  piece (1.62) and a 685MB video (~1.622) cost nearly the same, and even
  a 1GiB upload is ~1.623. Size is NOT the constraint (the 1GiB hard
  cap in `synapse-wrapper.ts` is). The real risk is accumulation:
  every upload creates a new dataset (`newDataSetCount=1`), each
  locking another ~1.62 and burning 0.12/month, so ~18 videos would
  exhaust the 30 USDFC wallet. The account already shows
  `totalFixedLockup` 2.92 / rate 0.24/month — i.e. 2 datasets, likely
  an orphan from the failed retries. Fix: reuse one dataset across
  uploads instead of creating one per upload, and clean up orphan
  datasets. Disabling CDN (`HAVEN_FILECOIN_WITH_CDN=false`) would cut
  the fixed lockup to ~0.62/dataset.
  `js-services/synapse-wrapper.ts` (`ensureStorageRunway`,
  `buildStorageFundingOptions`), `haven_cli/pipeline/steps/upload_step.py`.
  **Fix applied 2026-11-10:** wrapper reuses the oldest live data set
  (`resolveReuseDataSetId`, fail-open to creating one) and funds runway
  with `newDataSetCount=0` on reuse, so uploads share one fixed lockup
  instead of paying it per video. Upload shape is durable config, not
  host env: `PipelineConfig.synapse_copies=1` /
  `synapse_provider_ids` in config.toml, exported by `load_config`.
  Remaining cleanup: orphan dataset 52243 (provider 2) still locks
  ~1.50 USDFC and should be terminated once its pieces are accounted.

- **B2. Tier-1 dedup blocks re-upload after a failed upload.**
  A failed encrypt/upload leaves DB rows that make the next run skip the
  video entirely (`--force` needed). The 06:00 daemon run has no `--force`,
  so one transient failure wedges a video forever.
  `haven_cli/pipeline/steps/encrypt_step.py`, `upload_step.py`, `ingest_step.py`.

## P1 — breaks a real workflow

- **B3. `haven entity get` / `haven entity query` crash.**
  `AttributeError: 'BlockchainConfig' object has no attribute 'private_key'`
  — `haven_cli/cli/entity.py:66,184` reads `config.blockchain.private_key`,
  which doesn't exist (auth is via `HAVEN_PRIVATE_KEY` env only,
  `haven_cli/config.py:188`). Observed live 2026-11-10. Fix: drop the
  attribute read; `build_arkiv_config` already falls back to env.
  Verified entity existence via raw RPC instead.

- **B4. Seen-infohash side effect burns torrents without downloading.**
  `BitTorrentPlugin.initialize()` writes discovered infohashes to
  `.bittorrent_seen.json` on *discovery*, so any process that only
  discovers (probe, health check) permanently marks the torrent as seen
  and the daemon never downloads it. Observed: my probe made the next
  `discover_sources()` return 0 until the file was deleted.
  `haven_cli/plugins/builtin/bittorrent/plugin.py:358-373`.

- **B5. `output_path` written relative while downloads live under absolute `download_dir`.**
  `torrent_downloads.output_path` was stored as `downloads/bittorrent/…`
  (repo-relative) while the file came from
  `/root/.local/share/haven/bittorrent/…`. Pipeline steps resolving the
  stored path miss the file. Observed 2026-11-10.
  `haven_cli/plugins/builtin/bittorrent/plugin.py` (`_create_download_record`).

- **B6. `upload_jobs` can stick at `status='uploading'` after a cancelled
  run, and `haven jobs list --status` filtering hides real state.**
  Stale rows accumulate across manual runs.
  `haven_cli/pipeline/steps/upload_step.py` (`_create_upload_job`).

- **B7. Daemon has no auto-recovery; the JS bridge startup needs tsx.**
  Four crashed launches (02:21–02:39) died with
  `JS runtime failed to start within 30s` (`ERR_UNSUPPORTED_ESM_URL_SCHEME`
  for `npm:` — missing tsx/ts-node). No systemd unit ships, and the daemon
  writes nothing to `daemon.log` once up (buffered stdout), so a dead
  bridge is silent. Fix: package a systemd unit with `Restart=always`,
  add tsx to the JS deps, flush logs.
  `haven_cli/js_runtime/`, `haven_cli/daemon/service.py`.

## P2 — correctness / debt

- **B8. `test_metadata.py` import error (pre-existing).**
  `ImportError: cannot import _parse_encryption_metadata` — tests/crypto
  suite shows 5 pre-existing errors vs baseline. Fix the import; release
  gate currently hides them.
- **B9. `deno check main.ts` reports 3 pre-existing errors**
  (CopyResult cast, `Deno.copyFile` typing). Confirmed identical on the
  untouched tree; daemon runs because it doesn't type-check. Fix so
  `deno check` can be a release gate.
- **B10. `eden` — encrypt step silently skips on non-video files in some
  paths; only the largest video of a multi-video torrent is archived.**
  Observed: 48MB torrent → one 18MB file. Big torrents waste bandwidth and
  the rest never enters the pipeline.
  `haven_cli/plugins/builtin/bittorrent/plugin.py` (`archive`).
- **B11. `HAVEN_AOL_V3_BUGS.md` proposal §6.3 bans keys on disk; the
  persistent epoch-key cache (`0600` JSON) intentionally deviates.**
  Accepted by operator trade-off, but any external validator checking the
  proposal will flag it. Document the deviation.
- **B12. `max_video_size` (50GB) doesn't match the Filecoin pin lockup
  economics** — the limit should be derived from the USDFC budget, not a
  static byte count.

## P3 — polish

- **B13. `haven epoch` requires a subcommand (`haven epoch show`).**
  `--help` doesn't hint at it; bare `haven epoch` errors. Add a default
  action.
- **B14. Truncated job IDs in `haven jobs list`** (`cf118882` ellipsis)
  make shell piping painful; `--json` should be the scripted path.
- **B15. No `-v` verbose alias at the top level** (verbose is `-V`).
- **B16. `vetkd_py` must be built with maturin for Python 3.14**
  (PyPI wheels stop at 3.13). `install.sh` should document/detect this.
