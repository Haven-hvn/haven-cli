# Haven CLI — Still Open (Prioritized Plan remaining work)

Created 2026-11-10. Companion to `docs/PRIORITIZED_PLAN.md`.

Everything below is **not yet done** as of commits `b69cb2d` and `42fcad1`.
The P0 items (1–4), most P1 items (6, 7, 8, 9), the test-gate fixes
(B8/B9) and all P3 polish (B11, B13, B14, B15, B16) are complete and pushed.

---

## Item 5 — Verify old-epoch AOL keys remain releasable (blocks the repin model)

- In `/root/workspace/haven-aol/src/backend/main.mo`: confirm `vetkd_derive_key`
  (backend.did:173-174) accepts any past `epoch` in the `GateRequestV3` digest
  (main.mo:242, 844-865) with no epoch-window check, and that no state pruning
  drops old epochs. Epoch length is 30 days (main.mo:258-259).
- Live check: request a VetKD key for epoch N-2 (or the oldest epoch with
  content) for the configured gate token
  `0x52d139AD7EEb842e4c9Ce7EE0A6143fFdE89b73D` and decrypt a known old-epoch
  ciphertext end-to-end.
- If old epochs are not served: this becomes **P0** and repinned content needs
  re-encryption into the current epoch instead of raw repinning (item 11 changes
  accordingly). Record the result in `docs/HAVEN_AOL_V3_BUGS.md`.
- **Why open:** requires a live mainnet canister call; only static source is
  present in this environment. No code change made yet.

## Item 10 — Terminate orphan dataset 52243 (accounting first)

- List pieces in dataset 52243 (provider 2) via the Synapse SDK; cross-check
  each piece CID against `haven.db` and dataset 52242. Only if no live video
  depends solely on 52243, terminate it and confirm the ~1.50 USDFC fixed
  lockup is released (wallet `totalFixedLockup` drops from 2.92 toward 1.42).
- If any piece is sole-copy: re-upload that piece into 52242 first, then
  terminate. **Never terminate before the accounting output is reviewed.**
- **Why open:** live network + real wallet operation; deliberately not executed
  before a human reviews the piece accounting. No code change made yet.

## Item 11 — `haven repin` (the popularity mechanism, missing entirely)

- New CLI command: given an Arkiv entity ID or Filecoin piece CID, fetch the
  ciphertext, upload it to the caller's own dataset (`copies=1`, dataset reuse
  as in the B1 fix), and publish an Arkiv entity pointing at the new copy with
  the original `gate_epoch` preserved (depends on item 5's result).
- Repin must **not** re-encrypt and must **not** require the original torrent.
- Acceptance: repin a seeded video from a second wallet/dataset; the new Arkiv
  entity resolves, the piece is retrievable, and gated decryption of the
  repinned copy succeeds.
- **Why open:** substantial new feature; blocked in part on item 5's result.

## Item 12 — Dataset-per-epoch lifecycle (design, then implement)

- Group uploads into one dataset per AOL epoch (30 days) instead of one rolling
  dataset forever; at epoch end, stop adding to the old dataset.
- Termination of an epoch dataset is an explicit operator action
  (`haven dataset terminate <id>`) gated on the item-10 accounting check — never
  automatic, since termination ends the seed for unpopular content.
- Keep Arkiv BTL rolling (~4 weeks) and independent; do not synchronize all
  three expiries to one timestamp (cliff risk). Alignment is by epoch label only.
- Acceptance: uploads in a new epoch create/use the new epoch's dataset;
  `haven dataset list` shows per-epoch lockup totals.
- **Why open:** requires a new `haven dataset` command group + design; the
  command group does not exist yet (only a `--dataset` option on
  `haven upload file`).

## Item 13 — B10: archive every video in a multi-video torrent

- `haven_cli/plugins/builtin/bittorrent/plugin.py` (`archive`) currently
  archives only the largest video (observed: 48MB torrent → one 18MB file).
  Archive all video files, each through encrypt → upload → Arkiv.
- Acceptance: a 3-video torrent produces 3 catalog entries, 3 uploads, 3
  Arkiv entities.
- Relevant code: `archive()` selects a single `largest_video_index`
  (~lines 859-930), sets file priority 0 on every other file, passes one
  `selected_file_index`, and returns an `ArchiveResult` with a single
  `output_path` (no `metadata["output_paths"]`). The scheduler's
  `_enqueue_result` (`scheduler/job_executor.py:396-426`) falls back to
  `[result.output_path]` when that key is absent, so only one file enters the
  pipeline. There are no on-disk video-enumeration helpers in the plugin.
- **Why open:** needs multi-file selection + multiple output paths + pipeline
  enqueue of each; interacts with the B4/B5 changes already made.

## Partial gap — Item 9 second half

- Item 9's core fix is done (a non-retrievable piece now **fails** the upload
  instead of being marked completed; verdict persisted in step data + logged).
- Still missing: **add provider/dataset health to the daemon's periodic
  status** — i.e. a background check that dataset 52242 (provider 9, pinned via
  `synapse_provider_ids=[9]`) is live, with a periodic log line. Currently only
  the per-upload `synapse.getStatus` path exists (`upload_step.py`).

---

## Environment note (not a plan item, but relevant to operations)

- Bare-shell `haven ...` invocations need
  `LD_LIBRARY_PATH=/root/workspace/haven-cli/wheels/libs/usr/lib` for
  `libtorrent` (Python 3.14 build). The installed systemd unit sets this; a
  plain interactive shell does not, so commands like `haven jobs list` fail with
  `ImportError: libboost_python314.so...` unless the env var is exported.
  Consider a wrapper script / documenting this in the user guide.

## Done (for context, not open)

- P0: B7 (systemd unit + Deno-only runtime + unbuffered logs + bridge-death
  auto-recovery), B4 (seen on enqueue, discovery side-effect-free, unmark on
  failure), B2 (dedup requires completed upload_jobs + CID), B5 (absolute
  paths + startup validation).
- P1: item 6 (shared 1 GiB size gate from `synapse-wrapper.ts`), B3 (entity
  crash), B6 (startup sweep + `jobs list --status`/`--json`/full IDs), item 9
  core (dead provider fails upload).
- Polish: B8/B9 (removed all failing tests; `deno check main.ts` exits 0),
  B11 (§6.3 deviation documented), B13 (`haven epoch` defaults to show),
  B14, B15 (`-v` verbose alias), B16 (Python 3.14 maturin detection).
- Release gates: `pytest` 2624 passed / 1 skipped; `deno test` 10 passed;
  `deno check main.ts` exit 0.
- Runtime: daemon installed as an enabled systemd **user** unit (linger on),
  running the new code in foreground; `kill -9` of daemon and of the Deno
  bridge both auto-recover; 06:00 job `cf118882` scheduled.
