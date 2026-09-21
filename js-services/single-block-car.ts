/**
 * Single-block CAR packing for small files.
 *
 * The default UnixFS importer (IPIP-499 profile, 1 MiB chunks) turns even a
 * small single file into a multi-block CAR: N raw-leaf chunks plus a dag-pb
 * root. Readers then have to reassemble the UnixFS DAG to get the bytes back.
 *
 * For files at or under {@link SINGLE_BLOCK_THRESHOLD_BYTES} we skip the
 * chunker entirely and pack the file as exactly one raw block
 * (CIDv1 + raw codec + sha2-256) in a CARv1 envelope. This is byte-identical
 * in shape to what the UnixFS importer emits for a file that fits in a
 * single chunk (single-chunk raw-leaf files have exactly this root), so all
 * existing readers accept it with no changes — it just never goes
 * multi-block, no matter the file type.
 *
 * Larger files keep the standard chunked UnixFS path (bounded memory, PDP
 * piece limits) and rely on readers that reassemble multi-block CARs.
 */

import { CID } from 'multiformats/cid';

/** Files at or under this size pack as one raw block. */
export const SINGLE_BLOCK_THRESHOLD_BYTES = 32 * 1024 * 1024;

export interface SingleBlockCarResult {
  carPath: string;
  /** Base32 CIDv1 string (raw codec, sha2-256). */
  rootCid: string;
}

/** True when the file at *filePath* qualifies for single-block packing. */
export async function shouldPackSingleBlock(filePath: string): Promise<boolean> {
  let stat: Deno.FileInfo;
  try {
    stat = await Deno.stat(filePath);
  } catch {
    return false;
  }
  return stat.isFile && stat.size <= SINGLE_BLOCK_THRESHOLD_BYTES;
}

/** Unsigned LEB128 (varint) encoding used by CARv1 length prefixes. */
export function encodeVarint(value: number): Uint8Array {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new Error(`encodeVarint: expected non-negative safe integer, got ${value}`);
  }
  const out: number[] = [];
  let v = value;
  do {
    let b = v % 128;
    v = Math.floor(v / 128);
    if (v > 0) b |= 0x80;
    out.push(b);
  } while (v > 0);
  return new Uint8Array(out);
}

/**
 * Pack already-loaded *data* as a single-raw-block CARv1.
 * Returns the CAR bytes and the root CID string.
 */
export async function packSingleBlockCar(
  data: Uint8Array,
): Promise<{ carBytes: Uint8Array; rootCid: string }> {
  const bytes = Uint8Array.from(data);
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes.buffer));
  // CIDv1 bytes: 0x01 (version) 0x55 (raw) 0x12 0x20 (sha2-256, 32 B) + digest.
  const cidBytes = new Uint8Array(4 + digest.length);
  cidBytes.set([0x01, 0x55, 0x12, 0x20], 0);
  cidBytes.set(digest, 4);
  const rootCid = CID.decode(cidBytes).toString();

  // CARv1 header: dag-cbor {roots: [cid], version: 1} (canonical key order:
  // "roots" before "version"). CID is tag 42 + 0x00-prefixed CID bytes.
  const headerBody = new Uint8Array(HEADER_PREFIX.length + digest.length + HEADER_SUFFIX.length);
  headerBody.set(HEADER_PREFIX, 0);
  headerBody.set(digest, HEADER_PREFIX.length);
  headerBody.set(HEADER_SUFFIX, HEADER_PREFIX.length + digest.length);

  const headerLen = encodeVarint(headerBody.length);
  const blockLen = encodeVarint(cidBytes.length + data.length);
  const carBytes = new Uint8Array(
    headerLen.length + headerBody.length + blockLen.length + cidBytes.length + data.length,
  );
  let o = 0;
  carBytes.set(headerLen, o);
  o += headerLen.length;
  carBytes.set(headerBody, o);
  o += headerBody.length;
  carBytes.set(blockLen, o);
  o += blockLen.length;
  carBytes.set(cidBytes, o);
  o += cidBytes.length;
  carBytes.set(data, o);
  return { carBytes, rootCid };
}

/**
 * Build a single-block CAR for the file at *filePath*.
 * Throws when the file is missing, not a regular file, or over the threshold
 * (callers must fall back to the chunked UnixFS builder in that case).
 */
export async function buildSingleBlockCar(filePath: string): Promise<SingleBlockCarResult> {
  const stat = await Deno.stat(filePath);
  if (!stat.isFile) {
    throw new Error(`buildSingleBlockCar: not a regular file: ${filePath}`);
  }
  if (stat.size > SINGLE_BLOCK_THRESHOLD_BYTES) {
    throw new Error(
      `buildSingleBlockCar: ${stat.size} B exceeds single-block threshold ` +
        `${SINGLE_BLOCK_THRESHOLD_BYTES} B: ${filePath}`,
    );
  }
  const data = await Deno.readFile(filePath);
  const { carBytes, rootCid } = await packSingleBlockCar(data);
  const carPath = await Deno.makeTempFile({ prefix: 'haven-single-block-', suffix: '.car' });
  try {
    await Deno.writeFile(carPath, carBytes);
  } catch (error) {
    await Deno.remove(carPath).catch(() => {});
    throw error;
  }
  return { carPath, rootCid };
}

/** Delete a temp CAR created by {@link buildSingleBlockCar}; never throws. */
export async function removeSingleBlockCar(carPath: string): Promise<void> {
  try {
    await Deno.remove(carPath);
  } catch {
    // Ignore cleanup errors (matches UnixFS builder cleanup semantics).
  }
}

// A2 map(2) | 65 "roots" | 81 array(1) | D8 2A tag(42) | 58 25 bytes(37) |
// 00 multibase-identity | 01 v1 | 55 raw | 12 20 sha2-256 32 B — then digest.
const HEADER_PREFIX = new Uint8Array([
  0xa2, 0x65, 0x72, 0x6f, 0x6f, 0x74, 0x73, 0x81,
  0xd8, 0x2a, 0x58, 0x25, 0x00, 0x01, 0x55, 0x12, 0x20,
]);
// 67 "version" | 01.
const HEADER_SUFFIX = new Uint8Array([
  0x67, 0x76, 0x65, 0x72, 0x73, 0x69, 0x6f, 0x6e, 0x01,
]);

/**
 * Synchronous SHA-256 over *data* (test oracle cross-check).
 *
 * Independent FIPS 180-4 implementation used only by the test suite to
 * verify `packSingleBlockCar` against something other than `crypto.subtle`
 * itself. Not on the upload path.
 */
export function sha256Reference(data: Uint8Array): Uint8Array {
  const K = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ]);
  let h0 = 0x6a09e667, h1 = 0xbb67ae85, h2 = 0x3c6ef372, h3 = 0xa54ff53a;
  let h4 = 0x510e527f, h5 = 0x9b05688c, h6 = 0x1f83d9ab, h7 = 0x5be0cd19;

  const bitLen = data.length * 8;
  const paddedLen = (((data.length + 8) >> 6) + 1) << 6;
  const msg = new Uint8Array(paddedLen);
  msg.set(data, 0);
  msg[data.length] = 0x80;
  const dv = new DataView(msg.buffer);
  dv.setUint32(paddedLen - 4, bitLen >>> 0, false);
  dv.setUint32(paddedLen - 8, Math.floor(bitLen / 0x100000000), false);

  const w = new Uint32Array(64);
  const rotr = (x: number, n: number) => (x >>> n) | (x << (32 - n));
  for (let off = 0; off < paddedLen; off += 64) {
    for (let i = 0; i < 16; i++) w[i] = dv.getUint32(off + i * 4, false);
    for (let i = 16; i < 64; i++) {
      const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
      const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
    }
    let a = h0, b = h1, c = h2, d = h3, e = h4, f = h5, g = h6, h = h7;
    for (let i = 0; i < 64; i++) {
      const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const t1 = (h + S1 + ch + K[i] + w[i]) | 0;
      const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (S0 + maj) | 0;
      h = g;
      g = f;
      f = e;
      e = (d + t1) | 0;
      d = c;
      c = b;
      b = a;
      a = (t1 + t2) | 0;
    }
    h0 = (h0 + a) | 0;
    h1 = (h1 + b) | 0;
    h2 = (h2 + c) | 0;
    h3 = (h3 + d) | 0;
    h4 = (h4 + e) | 0;
    h5 = (h5 + f) | 0;
    h6 = (h6 + g) | 0;
    h7 = (h7 + h) | 0;
  }
  const out = new Uint8Array(32);
  const odv = new DataView(out.buffer);
  [h0, h1, h2, h3, h4, h5, h6, h7].forEach((v, i) => odv.setUint32(i * 4, v >>> 0, false));
  return out;
}
