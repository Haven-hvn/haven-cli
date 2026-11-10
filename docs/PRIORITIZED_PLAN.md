# Haven CLI — Prioritized Plan (Seeder Model)

Created 2026-11-10. Supersedes the priority order in `docs/BUG_LIST.md` (contents
of that list remain the bug inventory; this file is the execution order).
Model: Haven CLI is the initial seed only. Retention and replication are
popularity-driven repinning by others. Therefore P0 = anything that silently
prevents the first seed during an unattended 6h cycle.

## Goal

No silent seed loss: every discovered torrent is downloaded, encrypted with
Haven-AOL v3, uploaded once to Filecoin (copies=1, reused dataset), and
announced on Arkiv — or fails loudly with a retryable state.

## Success Criteria

- Killing the daemon or JS bridge mid-cycle recovers automatically and the
  06:00 job `cf118882` completes end-to-end (download → encrypt → upload →
  Arkiv entity) with no manual step.
- A transient failure at any step never permanently wedges a video; the next
  scheduled run retries it without `--force`.
- Discovery-only processes (probes, health checks) never consume a torrent.
- Every stored path resolves to the file on disk; every file >1 GiB fails
  before download completes, not after a 2–15 min upload attempt.
- Old-epoch ciphertext remains decryptable, proven against the AOL canister.

---

## P0 — Silent seed loss (fix first, in this order)

### 1. B7 — Daemon auto-recovery and audible failure
- Ship a systemd unit (new file, e.g. `deploy/haven.service`):
  `Restart=always`, `RestartSec=10`, `Environment=LD_LIBRARY_PATH=/root/workspace/haven-cli/wheels/libs/usr/lib`,
  `EnvironmentFile=%h/.config/haven/haven.env`, `WorkingDirectory` = repo root,
  `ExecStart=.venv/bin/haven run daemon --daemon -v`.
- Make daemon logging unbuffered once up: `daemon.log` currently goes silent
  after startup (buffered stdout in `haven_cli/daemon/service.py`); force
  line-buffered/flush-per-record logging so a dead JS bridge is visible.
- Ensure the JS bridge starts under the shipped runtime path only
  (`haven_cli/js_runtime/`, Deno + `js-services/main.ts`); remove any
  tsx/ts-node fallback that produced `ERR_UNSUPPORTED_ESM_URL_SCHEME`.
- Acceptance: `kill -9` the daemon PID and the bridge PID separately; both
  return within 30s; a synthetic scheduled run completes; log shows the kill,
  restart, and recovery lines with timestamps.

### 2. B4 — Seen-infohash must be written on download, not discovery
- In `haven_cli/plugins/builtin/bittorrent/plugin.py:574-582`, stop adding
  infohashes to `_seen_infohashes` (and persisting `.bittorrent_seen.json`)
  inside `discover_sources()`. Move the mark-seen write to the point a
  download record is actually created (`_create_download_record`) or the
  download is enqueued.
- Discovery must be side-effect-free: two consecutive `discover_sources()`
  calls return the same set.
- Acceptance: run a discovery-only probe, then the daemon; the torrent still
  downloads. Regression test: probe-then-discover returns non-zero sources
  (the exact failure observed 2026-11-10).

### 3. B2 — Tier-1 dedup must not wedge a video after a failed upload
- In `haven_cli/pipeline/steps/upload_step.py:156-175` (`should_skip`) and
  the `IngestStep` code that sets `context.skip_upload`: only skip when the
  matching catalog row proves a completed upload — an `upload_jobs` row with
  `status='completed'` AND a non-null Filecoin CID/piece CID. A row left by a
  failed/cancelled encrypt or upload must not set `skip_upload`.
- Same audit in `haven_cli/pipeline/steps/encrypt_step.py`: a partial/failed
  encrypt must not leave state that makes the next run skip.
- Acceptance: force an upload failure (e.g. bridge stopped), rerun without
  `--force`; the video retries and completes. Add a test seeding a failed
  upload row and asserting the next run does not skip.

### 4. B5 — Store absolute `output_path` for torrent downloads
- In `haven_cli/plugins/builtin/bittorrent/plugin.py` (`_create_download_record`),
  persist `str(Path(download_dir).resolve() / filename)`, never the
  repo-relative `downloads/bittorrent/…` form observed 2026-11-10.
- Add a startup/validation check: any `torrent_downloads.output_path` that is
  relative or does not exist is logged as an error, not silently missed by
  later pipeline steps.
- Acceptance: after a download, `os.path.exists(row.output_path)` is true for
  every row; pipeline steps resolve the file with no path joining.

---

## P1 — Seed correctness and the repin model's open risks

### 5. Verify old-epoch AOL keys remain releasable (blocks the repin model)
- In `/root/workspace/haven-aol/src/backend/main.mo`: confirm
  `vetkd_derive_key` (backend.did:173-174) accepts any past `epoch` in the
  `GateRequestV3` digest (main.mo:242, 844-865) with no epoch-window check,
  and that no state pruning drops old epochs. Epoch length is 30 days
  (main.mo:258-259).
- Live check: request a VetKD key for epoch N-2 (or the oldest epoch with
  content) for the configured gate token `0x52d139AD7EEb842e4c9Ce7EE0A6143fFdE89b73D`
  and decrypt a known old-epoch ciphertext end-to-end.
- If old epochs are not served: this becomes P0 and repinned content needs
  re-encryption into the current epoch instead of raw repinning (item 11
  changes accordingly). Record the result in `docs/HAVEN_AOL_V3_BUGS.md`.

### 6. Enforce the real size limit before upload (replaces B12)
- The binding limit is `MAX_UPLOAD_SIZE = 1_065_353_216` (~1 GiB) in
  `js-services/synapse-wrapper.ts:191`, enforced on projected encrypted size
  at lines 530/582 — not USDFC cost (B1 showed cost is ~flat per dataset)
  and not the static `max_video_size` (50GB).
- Set the pipeline size gate from that constant minus encryption overhead so
  oversize videos are rejected at ingest with a clear log line, before
  download bandwidth and encrypt time are spent. Keep one source of truth:
  Python reads/derives the same value the wrapper enforces.
- Acceptance: a >1 GiB test file is refused at ingest in seconds with the
  limit named in the log; a just-under-limit file uploads successfully.

### 7. B3 — Fix `haven entity get` / `haven entity query` crash
- Remove the `config.blockchain.private_key` reads at
  `haven_cli/cli/entity.py:66,184`; auth comes from `HAVEN_PRIVATE_KEY`
  (`haven_cli/config.py:188`) and `build_arkiv_config` already falls back to
  env.
- Acceptance: both commands return the entity for the known wallet
  `0x37000ea882ff2B36D036D093F8c3c41d6e7A3828` with no `AttributeError`
  (previously verified only via raw RPC).

### 8. B6 — Confirm stale `uploading` jobs cannot hide state
- `_create_upload_job` (`upload_step.py:1145-1190`) already abandons prior
  `uploading` rows as `failed` on a new attempt — verify this fires on the
  daemon-restart path too, and add a startup sweep: any `uploading` row older
  than the current daemon start is marked `failed` at boot.
- Fix `haven jobs list --status` filtering so it does not hide these rows.
- Acceptance: kill the daemon mid-upload, restart; no row remains
  `uploading`, and `haven jobs list --status failed` shows it.

### 9. Detect a dead storage provider instead of assuming the seed lives
- Upload already polls `synapse.getStatus` for retrievability
  (`upload_step.py:844-877`). Persist that result per video; if status never
  reaches `active`/`confirmed`, mark the upload failed (retryable per item 3),
  never completed.
- Add the provider/dataset health to the daemon's periodic status: dataset
  52242 (provider 9, pinned via `synapse_provider_ids=[9]`) live or not.
- Acceptance: with provider responses stubbed to non-retrievable, the video
  ends `failed`, is retried next run, and the failure is in the log.

### 10. Terminate orphan dataset 52243 (accounting first)
- List pieces in dataset 52243 (provider 2) via the Synapse SDK; cross-check
  each piece CID against `haven.db` and dataset 52242. Only if no live video
  depends solely on 52243, terminate it and confirm the ~1.50 USDFC fixed
  lockup is released (wallet `totalFixedLockup` drops from 2.92 toward 1.42).
- If any piece is sole-copy: re-upload that piece into 52242 first, then
  terminate. Never terminate before the accounting output is reviewed.

---

## P2 — Build the repin path and stop long-term accumulation

### 11. `haven repin` — the popularity mechanism, currently missing entirely
- New CLI command: given an Arkiv entity ID or Filecoin piece CID, fetch the
  ciphertext, upload it to the caller's own dataset (`copies=1`, dataset
  reuse as in B1 fix), and publish an Arkiv entity pointing at the new copy
  with the original `gate_epoch` preserved (depends on item 5's result).
- Repin must not re-encrypt and must not require the original torrent.
- Acceptance: repin a seeded video from a second wallet/dataset; the new
  Arkiv entity resolves, the piece is retrievable, and gated decryption of
  the repinned copy succeeds.

### 12. Dataset-per-epoch lifecycle (design, then implement)
- Group uploads into one dataset per AOL epoch (30 days) instead of one
  rolling dataset forever; at epoch end, stop adding to the old dataset.
- Termination of an epoch dataset is an explicit operator action
  (`haven dataset terminate <id>`) gated on the item-10 accounting check —
  never automatic, since termination ends the seed for unpopular content.
- Keep Arkiv BTL rolling (~4 weeks) and independent; do not synchronize all
  three expiries to one timestamp (cliff risk). Alignment is by epoch label
  only.
- Acceptance: uploads in a new epoch create/use the new epoch's dataset;
  `haven dataset list` shows per-epoch lockup totals.

### 13. B10 — Archive every video in a multi-video torrent
- `haven_cli/plugins/builtin/bittorrent/plugin.py` (`archive`) currently
  archives only the largest video (observed: 48MB torrent → one 18MB file).
  Archive all video files, each through encrypt → upload → Arkiv.
- Acceptance: a 3-video torrent produces 3 catalog entries, 3 uploads, 3
  Arkiv entities.

### 14. B8 + B9 — Make the test suites usable as release gates
- B8: fix `ImportError: cannot import _parse_encryption_metadata` in
  `tests/crypto/test_metadata.py` so `tests/crypto/` collects without
  `--ignore`. (The 5 `test_vetkd_e2e_mainnet.py` errors are live-network
  `ReplicaReject` failures; mark them explicitly network-gated, not silent.)
- B9: fix the 3 pre-existing `deno check main.ts` errors in `js-services/`
  (CopyResult cast, `Deno.copyFile` typing) so `deno check` passes clean.
- Acceptance: `.venv/bin/python -m pytest tests/test_config.py tests/crypto/ -q`
  passes with no ignores (network-gated tests skipped with reason shown);
  `cd js-services && deno check main.ts` exits 0; existing baselines kept:
  `tests/test_config.py` 58 passed, `synapse-wrapper_test.ts` 10 passed.

---

## P3 — Polish (do last, any order)

- **B11**: document the §6.3 deviation (persistent epoch-key cache, `0600`
  JSON in `haven_cli/crypto/epoch_key_cache.py` / `gate_key_cache.py`) in
  `docs/HAVEN_AOL_V3_BUGS.md` as an accepted operator trade-off.
- **B13**: `haven epoch` without a subcommand defaults to `haven epoch show`.
- **B14**: `haven jobs list` prints full job IDs (no `cf118882…` truncation);
  document `--json` as the scripted path.
- **B15**: add top-level `-v` as an alias for verbose (currently `-V`).
- **B16**: `install.sh` detects Python 3.14 and builds `vetkd_py` with
  maturin instead of failing on missing PyPI wheels (3.13 max).

## Closed / rewritten by this plan

- **B1**: fixed in `ce065eb` (dataset reuse, `synapse_copies=1`,
  `newDataSetCount=0` on reuse). Only item 10's cleanup remains.
- **B12**: premise was wrong (cost is per-dataset, not per-byte); replaced
  by item 6.

## Non-goals

- No multi-copy (`copies>1`) uploads paid by Haven; redundancy is the
  repinners' job (item 11).
- No tight expiry synchronization across Arkiv, Filecoin, and AOL.
- No changes to the AOL canister unless item 5 proves old-epoch keys lapse.

## Validation Plan (whole plan)

1. `.venv/bin/python -m pytest tests/test_config.py tests/crypto/test_gate_key_cache.py tests/crypto/test_v3_bug_fixes.py -q` — baseline 105 passed, 1 skipped must not regress.
2. `cd js-services && deno test --no-check --allow-all synapse-wrapper_test.ts` — baseline 10 passed.
3. Kill/restart drill (item 1) followed by one full scheduled cycle at 06:00
   observed in `daemon.log`: download → v3 encrypt → upload to dataset
   52242 → Arkiv entity, zero manual steps.
4. Failure-injection drill (items 3, 8, 9): stop the bridge mid-upload,
   restart, confirm retry-without-`--force` and terminal job states.
