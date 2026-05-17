# Large-upload memory usage: analysis for upstream GitHub issues

**Date:** 2026-05-16  
**Context:** Haven CLI daemon OOM during Filecoin upload (`UploadJob` stuck at `status=uploading`, `stage=preparing`). Example: **614 MB video** → Deno subprocess RSS exceeded ~1.5 GB and was killed.  
**Repos analyzed:**

| Component | Local path | Version |
|-----------|------------|---------|
| CAR creation (`buildCar`) | `filecoin-pin/` (full repo) | `0.21.0` (`filecoin-pin/package.json`) |
| PDP upload / storage | `synapse-sdk/` (vendored) | `@filoz/synapse-sdk@0.41.0` |
| Integration | `haven-cli` (`js-services/synapse-wrapper.ts`) | — |

> **Naming note:** `buildCar` is **not implemented in synapse-sdk**. It lives in **filecoin-pin** (`createUnixfsCarBuilder` → `createCarFromPath`). The production path is: **encrypt (Python, streaming) → build CAR (filecoin-pin / Helia) → upload CAR stream (synapse-sdk)**. This document covers both codebases because integrators treat them as one pipeline.

---

## Executive summary

For a ~614 MB file, peak memory is driven by **(1) duplicate on-disk artifacts plus OS page cache**, **(2) Helia/UnixFS DAG construction during CAR build**, and **(3) several synapse-sdk code paths that still materialize full payloads** when callers pass `Uint8Array` or use legacy APIs.

The **recommended** synapse-sdk path (`StorageManager.upload` / `uploadPieceStreaming` with `ReadableStream`) is designed for bounded memory, but:

- **filecoin-pin** always builds a **full temporary CAR on disk** via `@helia/unixfs` before upload starts (the maintainers document this explicitly; see below).
- **synapse-sdk** still exports **legacy upload APIs** that require a full in-memory `Uint8Array`.
- **`uploadPieceStreaming`** has a **browser fallback** that drains the entire stream into a `Blob` (full file in RAM) on Firefox/Safari.
- **Official React helper** (`use-upload`) calls `file.arrayBuffer()` before upload.
- **Downloads** always buffer the full piece in memory.

A 614 MB asset can therefore produce **~1.2–1.8 GB on disk** (source + encrypted + CAR) and **>1.5 GB RSS** in a single Deno process even when the final HTTP upload uses streaming.

---

## End-to-end data flow (haven-cli)

```text
Python UploadStep
  │  file path only (no full-file buffer in Python)
  ▼
Deno synapse-wrapper.ts
  │  unixfsCarBuilder.buildCar(filePath)     ← "preparing" (~20% progress)
  │       → temp CAR on disk (~file size)
  ▼
  │  checkUploadReadiness(fileSize = CAR stat)
  ▼
  │  openCarReadableStream(carPath)           ← streaming (good)
  │  executeUpload(synapse, stream, rootCid)  ← filecoin-pin → synapse.storage.upload
  ▼
synapse-sdk StorageManager.upload
  │  primary.store(stream)  → uploadPieceStreaming → PDP PUT (streaming on Node/Deno)
  │  secondary.pull(SP-to-SP)                 ← no second client upload of bytes
  │  commit (on-chain)
```

Relevant haven-cli code:

- CAR build: `js-services/synapse-wrapper.ts` (`buildCar` at ~539, stream upload at ~694).
- Stream helper: `js-services/car_stream.ts` (`Deno.open` + `handle.readable`).

---

## Part A — filecoin-pin: `buildCar` memory & I/O (primary `preparing`-stage cost)

**Package:** `filecoin-pin@0.21.0` (local: `filecoin-pin/`)  
**Entry:** `createUnixfsCarBuilder().buildCar()` → `createCarFromPath()`  
**Source:** `filecoin-pin/src/core/unixfs/car-builder.ts`

### A.0 Maintainers already acknowledge the limitation

The Node.js CLI and pinning server **do not** stream source bytes straight to Synapse while building the DAG. They **materialize a complete CAR file on disk first**, then open a read stream for upload (since [#428](https://github.com/filecoin-project/filecoin-pin/pull/428) added stream uploads).

```248:253:filecoin-pin/src/add/add.ts
    // The CLI still materializes a full CAR on disk first. This only avoids
    // buffering that CAR again in memory during upload.
    spinner.start('Inspecting packed IPFS content...')
    const { size: carSize } = await stat(tempCarPath)
    const carData = Readable.toWeb(createReadStream(tempCarPath)) as ReadableStream<Uint8Array>
```

```310:314:filecoin-pin/src/filecoin-pin-store.ts
        // The pinning server still writes a full CAR to disk first, but the
        // upload body no longer buffers that CAR in memory.
        const carData = Readable.toWeb(createReadStream(pinStatus.filecoin.carFilePath)) as ReadableStream<Uint8Array>
```

**Implication for upstream issues:** the team is aware that upload streaming fixed only the **second** full-file copy (CAR bytes in RAM), not the **first** cost center (UnixFS DAG build + temp CAR file + page cache). Haven-cli’s `preparing` OOM is in that first phase.

**Docs vs implementation:** `filecoin-pin/documentation/behind-the-scenes-of-adding-a-file.md` states that “as the car is being created, it can be streamed to an SP.” The current `createCarFromPath` + `add` / `synapse-wrapper` paths **finish CAR creation before** `uploadToSynapse` / `executeUpload` run. True create-and-upload piping would be new work.

### A.1 What the code does

1. Creates a temp CAR path under `os.tmpdir()`.
2. Instantiates `CARWritingBlockstore` → `CARFileBackend` (streams CAR bytes to disk).
3. Creates `unixfs({ blockstore })` from `@helia/unixfs`.
4. For a single file (haven-cli uses `bare: true`):
   - `createReadStream(filePath)` → `Readable.toWeb()` → `fs.addByteStream(webStream)`.
5. `blockstore.finalize()` then `CarWriter.updateRootsInFile()` patches the root CID in the CAR.

Disk streaming of the **output CAR** is implemented correctly in `CARFileBackend` (`createWriteStream` + `@ipld/car` writer pipeline). Blocks are written incrementally via `writeBlock()`.

### A.2 Memory hotspots

| Location | Issue | Severity |
|----------|--------|----------|
| **Helia / `@helia/unixfs` `addByteStream` / `addFile`** | Chunking, dedup, and DAG layout are opaque; internal buffers can hold **multiple chunk-sized blocks** (often hundreds of KB–MB each) while the CAR is built. Not bounded by filecoin-pin. | **High** (likely dominant heap during `preparing`) |
| **`CARBlockstoreBase.putMany`** | Uses `it-to-buffer` to coerce non-`Uint8Array` inputs: `bytes instanceof Uint8Array ? bytes : await toBuffer(bytes)` — can **fully buffer** async iterables per block. | **Medium** (if Helia feeds non-Uint8Array chunks) |
| **`blockOffsets` Map** | One entry per IPLD block (614 MB ÷ ~256 KB–1 MB ≈ hundreds–thousands of entries). Metadata only. | Low |
| **Duplicate on-disk files** | Source file + `.encrypted` (if enabled) + temp CAR ≈ **2–3× file size**. Linux/macOS/Windows **page cache** may keep recently read/written ranges in RAM during build + upload. | **High** (system-level, not heap) |
| **Second read of source** | `createCarFromSingleFile` opens a **sniff** `createReadStream` for `isCar()`, then a **second** stream for `addByteStream`. Doubles read traffic; warms page cache. | Medium |

### A.3 Bare vs directory wrapper

Haven-cli passes `bare: true` (file bytes as UnixFS raw stream, no directory wrapper). That avoids an extra directory DAG layer but **does not eliminate** per-chunk UnixFS blocks inside the CAR.

### A.4 Estimated memory / disk budget (614 MB video, encrypted)

| Resource | Approximate |
|----------|-------------|
| Source + encrypted on disk | ~1.23 GB |
| Temp CAR on disk | ~620 MB |
| OS page cache (both files touched) | 0.5–1.2 GB+ (environment-dependent) |
| Deno/Helia heap during `buildCar` | **Unbounded in API contract**; empirically often **hundreds of MB** extra |
| **Total pressure** | Easily **>1.5 GB RSS + cache** → OOM on constrained hosts |

### A.5 Node vs browser backends

| Backend | File | Behavior |
|---------|------|----------|
| **Node (haven-cli, CLI)** | `CARFileBackend` in `filecoin-pin/src/core/car/car-file-backend.ts` | CAR **streamed to disk** via `createWriteStream` + `@ipld/car` pipeline |
| **Browser** | `CARMemoryBackend` in `filecoin-pin/src/core/car/car-memory-backend.ts` | All CAR writer `out` chunks pushed to `carChunks[]`; `getCarBytes()` calls `toBuffer(this.carChunks)` → **entire CAR in RAM** |

Browser path is irrelevant to haven-cli’s Deno upload but is critical for **browser integrators** filing against filecoin-pin.

### A.6 Directory uploads: extra heap

`createCarFromDirectory` accumulates every `addAll` entry in an in-memory `entries` array before returning the root CID (`filecoin-pin/src/core/unixfs/car-builder.ts` lines 225–228). Large directories can add **metadata overhead** beyond per-file CAR blocks.

### A.7 Upload phase in filecoin-pin (post-CAR)

After CAR exists, `uploadToSynapse` accepts `Uint8Array | ReadableStream` (`filecoin-pin/src/core/upload/synapse.ts`) and forwards `onProgress` from synapse-sdk as `uploadProgress` events (lines 183–193). This path is **not** the haven-cli OOM bottleneck once streaming is used.

### A.8 Suggested filecoin-pin improvements (upstream)

1. **Streaming CAR from source without full UnixFS DAG** when `bare: true` and input is already a single file (e.g. optional “raw CAR” / “file-as-single-block” fast path for PDP upload).
2. **Document peak RAM** for `buildCar` vs input size; add integration tests with RSS sampling on 500 MB+ fixtures.
3. **Avoid double `createReadStream`** where possible (combine `isCar` sniff with first bytes of main read).
4. **Plumb backpressure** from `CARFileBackend` through Helia if chunk production outruns disk writes.
5. **Optional in-place / named-pipe path** so integrators can skip a third full copy on disk when source is already encrypted.

**Suggested issue title (filecoin-pin):**  
`buildCar: document and reduce peak memory for large single-file inputs (Helia/unixfs + temp CAR)`

---

## Part B — synapse-sdk: upload & download memory paths

**Packages:** `@filoz/synapse-sdk`, `@filoz/synapse-core`  
**Vendored paths:** `synapse-sdk/packages/synapse-sdk/`, `synapse-sdk/packages/synapse-core/`

### B.1 Recommended path: `uploadPieceStreaming` (good, with caveats)

**File:** `packages/synapse-core/src/sp/upload-streaming.ts`

**Intended behavior:** 3-step PDP streaming upload; CommP via `Piece.createPieceCIDStream()` in a `TransformStream`; progress via byte counter.

**Streaming fetch body (Node.js, Deno, Chrome):**

```148:151:synapse-sdk/packages/synapse-core/src/sp/upload-streaming.ts
  if (supportsStreamingFetchBody()) {
    fetchBody = bodyStream
    fetchOptions = { duplex: 'half' }
```

Haven-cli’s Deno runtime typically takes this branch → **upload HTTP body should not buffer the full CAR in JS heap**.

**Caveat 1 — `Uint8Array` input copies entire payload into a Blob:**

```88:90:synapse-sdk/packages/synapse-core/src/sp/upload-streaming.ts
  const dataStream = isUint8Array(options.data)
    ? new Blob([options.data as Uint8Array<ArrayBuffer>]).stream()
    : (options.data as ReadableStream)
```

Any caller passing `Uint8Array` (including `StorageContext.store` validation path and tests) holds the **full file in memory** before streaming begins.

**Caveat 2 — Firefox / Safari fallback buffers entire stream:**

```152:163:synapse-sdk/packages/synapse-core/src/sp/upload-streaming.ts
  } else {
    const chunks: Uint8Array[] = []
    let totalSize = 0
    const reader = bodyStream.getReader()
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      chunks.push(value)
      totalSize += value.length
    }
    fetchBody = new Blob(chunks as BlobPart[])
```

Documented in CHANGELOG as [#681](https://github.com/FilOzone/synapse-sdk/issues/681). For a 614 MB upload in Safari/Firefox, peak JS heap includes **full file + Blob overhead**.

**Caveat 3 — `size` optional for streams:** If `pieceCid` is pre-supplied and `size` omitted, behavior relies on stream consumption; integrators should pass `size` from `stat()` (haven-cli does via CAR length).

### B.2 High-level API: `StorageManager.upload` / `StorageContext.store`

**Files:**

- `packages/synapse-sdk/src/storage/manager.ts` — multi-copy `store → pull → commit`
- `packages/synapse-sdk/src/storage/context.ts` — `store()` delegates to `SP.uploadPieceStreaming`

Docs correctly recommend streaming:

```296:296:synapse-sdk/docs/src/content/docs/developer-guides/storage/upload-pipeline.mdx
`store()` accepts `Uint8Array` or `ReadableStream<Uint8Array>`. Use streaming for large files to minimize memory.
```

Reference E2E uses filesystem streams (good pattern):

```113:119:synapse-sdk/utils/example-storage-e2e.js
    const fileStream = Readable.toWeb(fs.createReadStream(file.path))
    ...
    const result = await synapse.storage.upload(fileStream, {
```

**Multi-copy default (`copies: 2`):** Primary `store(stream)` uploads once; secondaries use SP-to-SP `pull` — **does not double client-side upload buffers**, but increases SDK/RPC work during the same process lifetime (longer window where CAR + caches remain hot).

### B.3 Legacy path: `uploadPiece` (full buffer required)

**File:** `packages/synapse-core/src/sp/upload.ts`

```72:78:synapse-sdk/packages/synapse-core/src/sp/upload.ts
  const uploadResponse = await request.put(new URL(`pdp/piece/upload/${uploadUuid}`, options.serviceURL), {
    body: options.data as BufferSource,
    headers: {
      'Content-Type': 'application/octet-stream',
      'Content-Length': options.data.length.toString(),
```

`options.data` is typed as **`Uint8Array` only**. Still exported from `@filoz/synapse-core/sp` (`export * from './upload.ts'`).

### B.4 Legacy path: `SP.upload` with `File[]` (browser-oriented)

**Same file** (`upload.ts`):

```135:150:synapse-sdk/packages/synapse-core/src/sp/upload.ts
  const uploadResponses = await Promise.all(
    options.data.map(async (file: File) => {
      const data = new Uint8Array(await file.arrayBuffer())
      const pieceCid = Piece.calculate(data)
      ...
      await uploadPiece({
        data,
```

Loads **entire file**, computes PieceCID in memory (`Piece.calculate`), then uploads — **triple materialization** risk (ArrayBuffer + Uint8Array + PUT body).

### B.5 `Piece.calculate` (non-streaming CommP)

**File:** `packages/synapse-core/src/piece/piece.ts`

```140:151:synapse-sdk/packages/synapse-core/src/piece/piece.ts
export function calculate(data: Uint8Array): PieceCID {
  const hasher = Hasher.create()
  const chunkSize = 2048
  for (let i = 0; i < data.length; i += chunkSize) {
    hasher.write(data.subarray(i, i + chunkSize))
  }
```

Requires full `Uint8Array`. Streaming alternative exists (`calculateFromIterable`, `createPieceCIDStream`) and is used by `uploadPieceStreaming` when `pieceCid` is omitted.

### B.6 Downloads (full buffer)

**File:** `packages/synapse-core/src/piece/download.ts`

- `download()` → `arrayBuffer()` → `Uint8Array` (**entire piece**).
- `downloadAndValidate()` → collects **all chunks** into array, then allocates **single `Uint8Array` of total length** (lines 87–135).

No streaming download API for large pieces.

### B.7 `@filoz/synapse-react` — `useUpload` forces `arrayBuffer()`

**File:** `packages/synapse-react/src/warm-storage/use-upload.ts`

```36:39:synapse-sdk/packages/synapse-react/src/warm-storage/use-upload.ts
      const rsp = await synapse.storage.upload(new Uint8Array(await file.arrayBuffer()), {
        ...props,
        pieceMetadata: metadata,
      })
```

Any UI using this hook cannot upload large files without OOM, contradicting SDK docs.

---

## Part C — Integration-specific notes (haven-cli)

| Stage | Memory behavior |
|-------|-----------------|
| Python encryption | **Streaming** (`encrypt_file_streaming`, 1 MiB chunks) — good |
| `buildCar` | **filecoin-pin / Helia** — high disk + heap + cache |
| `synapse.upload` | **CAR `ReadableStream`** — good on Deno if `supportsStreamingFetchBody()` is true |
| Progress `stage=preparing` | Maps to CAR build (~20%), **before** network upload (~80%) — matches OOM before stream upload |
| Stale `UploadJob` | No daemon recovery; jobs killed mid-`preparing` stay `uploading` |

---

## Reproduction guidance (for upstream issues)

1. **Fixture:** single file, 500–700 MB, repeatable binary (e.g. `/dev/urandom` or sparse file).
2. **Process:** Deno 2.x subprocess (matches haven-cli JS bridge) or Node 20+.
3. **Metrics:** RSS of Deno process + temp disk under `tmpdir` + `/proc/meminfo` or OS equivalent.
4. **Phases:** separate benchmarks for (a) `buildCar` only, (b) `synapse.storage.upload(stream)` only, (c) combined pipeline.
5. **Compare:** `bare: true` vs wrapped file; `Uint8Array` vs `fs.createReadStream`; Firefox/Safari vs Chromium vs Deno.

---

## Recommendations summary

### For synapse-sdk maintainers

| Priority | Item |
|----------|------|
| P0 | Deprecate or gate `SP.upload` / `uploadPiece` for large files; point to `uploadPieceStreaming` only |
| P0 | Fix `use-upload` to use `file.stream()` / `ReadableStream` |
| P1 | For `Uint8Array` input, avoid `new Blob([data])` — use `uint8ArrayToAsyncIterable` + `ReadableStream` without copying entire buffer |
| P1 | Streaming download API (or document hard max download size) |
| P2 | Runtime warning when `supportsStreamingFetchBody()` is false and payload > N MB |
| P2 | Export a single “large file” cookbook that skips CAR (if/when PDP accepts raw piece streams from integrators) |

### For filecoin-pin maintainers

| Priority | Item |
|----------|------|
| P0 | Characterize Helia peak RAM vs file size for `addByteStream` |
| P0 | Fast path: stream file → PDP/Synapse **without** full UnixFS CAR when appropriate |
| P1 | Reduce on-disk copies; optional upload-from-source-path API |
| P2 | `putMany` / `toBuffer` audit to ensure no full-file buffering |

### For integrators (haven-cli)

- Treat **`preparing` OOM** as **CAR-build / cache**, not Python video buffering.
- Consider **uploading encrypted file stream directly** when CAR is not required by policy.
- Add **startup recovery** for stale `uploading` jobs (separate issue).
- Cap concurrent uploads per Deno subprocess.

---

## Suggested GitHub issue templates

### Issue 1 — filecoin-pin

**Title:** `buildCar` peak memory scales with file size (Helia unixfs); 600MB+ inputs OOM integrator daemons  

**Body bullets:**

- `createCarFromPath` + `CARWritingBlockstore` write CAR to disk, but Helia DAG build still drives high RSS.
- 614 MB example: ~1.2 GB+ on disk + page cache + Deno heap > 1.5 GB.
- Request: fast path / streaming CAR / documented limits / RSS tests.

### Issue 2 — synapse-sdk

**Title:** Legacy upload and React helpers buffer entire file despite streaming PDP API  

**Body bullets:**

- `uploadPiece`, `SP.upload(File[])`, `use-upload` use full `arrayBuffer`/`Uint8Array`.
- `uploadPieceStreaming` Safari/Firefox fallback materializes full `Blob`.
- `download` / `downloadAndValidate` buffer entire piece.
- Request: deprecations, stream-based React hook, optional streaming download.

---

## Code reference index (synapse-sdk vendored copy)

| Path | Role |
|------|------|
| `packages/synapse-core/src/sp/upload-streaming.ts` | Streaming PDP upload (preferred) |
| `packages/synapse-core/src/sp/upload.ts` | Legacy buffered upload |
| `packages/synapse-core/src/piece/piece.ts` | CommP calculate / stream |
| `packages/synapse-core/src/piece/download.ts` | Buffered download |
| `packages/synapse-core/src/utils/streams.ts` | `supportsStreamingFetchBody()` |
| `packages/synapse-sdk/src/storage/manager.ts` | Multi-copy orchestration |
| `packages/synapse-sdk/src/storage/context.ts` | `store` / `upload` / `download` |
| `packages/synapse-react/src/warm-storage/use-upload.ts` | Full-file buffer anti-pattern |
| `utils/example-storage-e2e.js` | Correct streaming upload example |
| `docs/.../upload-pipeline.mdx` | Documents streaming preference |

## Code reference index (filecoin-pin, local `filecoin-pin/`)

| Path | Role |
|------|------|
| `src/core/unixfs/car-builder.ts` | `createCarFromPath`, Helia `addByteStream` / `addFile` |
| `src/core/unixfs/index.ts` | `createUnixfsCarBuilder()` public API |
| `src/core/car/car-blockstore-base.ts` | `put` / `putMany` (+ `it-to-buffer`) |
| `src/core/car/car-file-backend.ts` | Node: disk streaming CAR writer |
| `src/core/car/car-memory-backend.ts` | Browser: in-memory CAR chunks |
| `src/core/car/browser-car-blockstore.ts` | Browser blockstore wrapper |
| `src/core/upload/synapse.ts` | `uploadToSynapse` → `synapse.storage.upload` |
| `src/add/add.ts` | CLI flow; explicit “full CAR on disk first” comment |
| `src/filecoin-pin-store.ts` | Pinning server; same upload pattern |
| `src/common/upload-flow.ts` | `performUpload` wrapper |
| `documentation/behind-the-scenes-of-adding-a-file.md` | Product doc (mentions streaming aspiration) |
| `CHANGELOG.md` | v0.20.x: “support stream uploads to Synapse” (#428) |

---

*Generated from haven-cli integration analysis against local `filecoin-pin/` and vendored `synapse-sdk/`.*
