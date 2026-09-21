/**
 * Tests for single-block CAR packing.
 *
 * Strongest oracle: for a file that fits in one UnixFS chunk, our root CID
 * must equal the reference `filecoin-pin` builder's root (a single-chunk
 * raw-leaf UnixFS file has exactly this root). Weaker oracles (manual CAR
 * framing parse, independent SHA-256) cover the byte layout.
 */
import {
  assert,
  assertEquals,
  assertRejects,
} from 'https://deno.land/std@0.200.0/testing/asserts.ts';
import { createUnixfsCarBuilder } from 'filecoin-pin/core/unixfs';
import {
  buildSingleBlockCar,
  encodeVarint,
  packSingleBlockCar,
  removeSingleBlockCar,
  sha256Reference,
  shouldPackSingleBlock,
  SINGLE_BLOCK_THRESHOLD_BYTES,
} from './single-block-car.ts';

/** Deterministic pseudo-random fill (xorshift32) — avoids getRandomValues quotas. */
function fillPseudoRandom(data: Uint8Array, seed: number): void {
  let x = seed >>> 0 || 1;
  for (let i = 0; i < data.length; i++) {
    x ^= x << 13;
    x >>>= 0;
    x ^= x >> 17;
    x ^= x << 5;
    x >>>= 0;
    data[i] = x & 0xff;
  }
}

function decodeVarint(buf: Uint8Array, offset: number): { value: number; size: number } {
  let value = 0;
  let shift = 0;
  let size = 0;
  while (true) {
    const b = buf[offset + size];
    size++;
    value += (b & 0x7f) * 2 ** shift;
    if ((b & 0x80) === 0) break;
    shift += 7;
    assert(size < 10, 'varint too long');
  }
  return { value, size };
}

Deno.test('encodeVarint matches known vectors', () => {
  assertEquals([...encodeVarint(0)], [0x00]);
  assertEquals([...encodeVarint(1)], [0x01]);
  assertEquals([...encodeVarint(127)], [0x7f]);
  assertEquals([...encodeVarint(128)], [0x80, 0x01]);
  assertEquals([...encodeVarint(300)], [0xac, 0x02]);
});

Deno.test('sha256Reference matches FIPS 180-4 vector', () => {
  const got = [...sha256Reference(new TextEncoder().encode('abc'))]
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
  assertEquals(got, 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
});

Deno.test('packSingleBlockCar emits one raw block that round-trips', async () => {
  const data = new Uint8Array(100_000);
  fillPseudoRandom(data, 0x12345678);
  const { carBytes, rootCid } = await packSingleBlockCar(data);

  // Header frame.
  let off = 0;
  const headerLen = decodeVarint(carBytes, off);
  off += headerLen.size;
  const header = carBytes.slice(off, off + headerLen.value);
  off += headerLen.value;
  // dag-cbor {roots: [tag42 bytes(37) 0x00 <cid>], version: 1}: CID sits at
  // header[12 .. 12+37): 12-byte prefix then 0x00 0x01 0x55 0x12 0x20 + digest.
  assertEquals(header[0], 0xa2);
  const cidBytes = header.slice(12, 12 + 37);
  assertEquals([...cidBytes.slice(0, 5)], [0x00, 0x01, 0x55, 0x12, 0x20]);

  // Single block frame, then EOF.
  const blockLen = decodeVarint(carBytes, off);
  off += blockLen.size;
  assertEquals(blockLen.value, 36 + data.length);
  assertEquals(carBytes.slice(off, off + 36), cidBytes.slice(1));
  off += 36;
  assertEquals(carBytes.slice(off, off + data.length), data);
  off += data.length;
  assertEquals(off, carBytes.length, 'CAR must contain exactly one block');

  // Root CID recomputed independently of crypto.subtle.
  const digest = sha256Reference(data);
  assertEquals(cidBytes.slice(5), digest);
  assert(rootCid.startsWith('bafk'), `raw root CID expected, got ${rootCid}`);
});

Deno.test('single-block root matches UnixFS builder root for sub-chunk file', async () => {
  // Under the 1 MiB profile chunk size the reference builder emits a single
  // raw-leaf chunk, so its root must equal ours exactly.
  const dir = await Deno.makeTempDir();
  try {
    const path = `${dir}/small.bin`;
    const data = new Uint8Array(300_000);
    fillPseudoRandom(data, 0xdeadbeef);
    await Deno.writeFile(path, data);

    const { rootCid } = await packSingleBlockCar(data);

    const builder = createUnixfsCarBuilder();
    const built = await builder.buildCar(path);
    try {
      assertEquals(rootCid, built.rootCid, 'roots must match the reference builder');
    } finally {
      await builder.cleanup(built.carPath).catch(() => {});
    }
  } finally {
    await Deno.remove(dir, { recursive: true });
  }
});

Deno.test('buildSingleBlockCar writes a temp CAR and removes it', async () => {
  const dir = await Deno.makeTempDir();
  try {
    const path = `${dir}/audio.mp3`;
    await Deno.writeFile(path, new TextEncoder().encode('fake mp3 bytes'));
    assert(await shouldPackSingleBlock(path));

    const { carPath, rootCid } = await buildSingleBlockCar(path);
    assert(rootCid.startsWith('bafk'));
    assert((await Deno.stat(carPath)).size > 0);
    await removeSingleBlockCar(carPath);
    await assertRejects(() => Deno.stat(carPath), Deno.errors.NotFound);
  } finally {
    await Deno.remove(dir, { recursive: true });
  }
});

Deno.test('oversize files are rejected (fall back to chunked builder)', async () => {
  const dir = await Deno.makeTempDir();
  try {
    const path = `${dir}/big.bin`;
    await Deno.writeFile(path, new Uint8Array(8));
    // Sparse-extend past the threshold without writing 32 MiB.
    await Deno.truncate(path, SINGLE_BLOCK_THRESHOLD_BYTES + 1);
    assertEquals(await shouldPackSingleBlock(path), false);
    await assertRejects(() => buildSingleBlockCar(path), Error);
  } finally {
    await Deno.remove(dir, { recursive: true });
  }
});

Deno.test('missing path is not single-block eligible', async () => {
  assertEquals(await shouldPackSingleBlock('/nonexistent/haven-xyz-123'), false);
});
