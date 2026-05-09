/**
 * Tests for CAR streaming helpers used by the Synapse upload bridge.
 */
import {
  assertEquals,
  assertRejects,
} from 'https://deno.land/std@0.200.0/testing/asserts.ts';
import { carFileByteLength, openCarReadableStream } from './car_stream.ts';

Deno.test('carFileByteLength returns file size', async () => {
  const dir = await Deno.makeTempDir();
  const path = `${dir}/test.car`;
  const content = new Uint8Array([1, 2, 3, 4, 5]);
  try {
    await Deno.writeFile(path, content);
    assertEquals(await carFileByteLength(path), 5);
  } finally {
    await Deno.remove(dir, { recursive: true });
  }
});

Deno.test('carFileByteLength rejects when path is missing', async () => {
  await assertRejects(
    async () => await carFileByteLength('/nonexistent/path/car-xyz-123'),
    Deno.errors.NotFound,
  );
});

Deno.test('openCarReadableStream yields full file bytes then closes cleanly', async () => {
  const dir = await Deno.makeTempDir();
  const path = `${dir}/test.car`;
  const content = new Uint8Array(10_000);
  for (let i = 0; i < content.length; i++) {
    content[i] = i % 256;
  }
  try {
    await Deno.writeFile(path, content);
    const { handle, stream } = await openCarReadableStream(path);
    try {
      const reader = stream.getReader();
      const chunks: Uint8Array[] = [];
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        if (value) chunks.push(value);
      }
      const total = chunks.reduce((sum, c) => sum + c.length, 0);
      assertEquals(total, content.length);
      const merged = new Uint8Array(total);
      let offset = 0;
      for (const c of chunks) {
        merged.set(c, offset);
        offset += c.length;
      }
      assertEquals(merged, content);
    } finally {
      handle.close();
    }
  } finally {
    await Deno.remove(dir, { recursive: true });
  }
});

Deno.test('openCarReadableStream rejects when path is missing', async () => {
  await assertRejects(
    async () => await openCarReadableStream('/nonexistent/path/car-abc-999'),
    Deno.errors.NotFound,
  );
});
