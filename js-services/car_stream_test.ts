/**
 * Tests for CAR streaming helpers used by the Synapse upload bridge.
 */
import {
  assertEquals,
  assertRejects,
} from 'https://deno.land/std@0.200.0/testing/asserts.ts';
import {
  carFileByteLength,
  openCarReadableStream,
  streamReadableToFile,
} from './car_stream.ts';

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
      try {
        handle.close();
      } catch {
        // Reading the stream to completion auto-closes the file handle.
      }
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

function streamOf(...chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(chunk);
      }
      controller.close();
    },
  });
}

Deno.test('streamReadableToFile writes every chunk and reports progress', async () => {
  const dir = await Deno.makeTempDir();
  const path = `${dir}/out.bin`;
  const progress: Array<{ bytesDownloaded: number; totalBytes: number }> = [];
  try {
    const written = await streamReadableToFile(
      streamOf(new Uint8Array([1, 2]), new Uint8Array([3, 4, 5])),
      path,
      {
        timeoutMs: 1_000,
        totalBytes: 5,
        onProgress: (p) => progress.push(p),
      },
    );
    assertEquals(written, 5);
    assertEquals(await Deno.readFile(path), new Uint8Array([1, 2, 3, 4, 5]));
    assertEquals(progress.at(-1), { bytesDownloaded: 5, totalBytes: 5 });
  } finally {
    await Deno.remove(dir, { recursive: true });
  }
});

Deno.test('streamReadableToFile propagates stream errors', async () => {
  const dir = await Deno.makeTempDir();
  const path = `${dir}/out.bin`;
  let pulls = 0;
  const stream = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (pulls++ === 0) {
        controller.enqueue(new Uint8Array([1, 2]));
        return;
      }
      controller.error(new Error('boom'));
    },
  });
  try {
    await assertRejects(
      () => streamReadableToFile(stream, path, { timeoutMs: 1_000 }),
      Error,
      'boom',
    );
    // The first chunk was already written; callers must discard partial files.
    assertEquals((await Deno.stat(path)).size, 2);
  } finally {
    await Deno.remove(dir, { recursive: true });
  }
});

Deno.test('streamReadableToFile times out a stalled stream', async () => {
  const dir = await Deno.makeTempDir();
  const path = `${dir}/out.bin`;
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new Uint8Array([1]));
    },
  });
  try {
    await assertRejects(
      () => streamReadableToFile(stream, path, { timeoutMs: 25 }),
      Error,
      'timed out after 25ms',
    );
  } finally {
    await Deno.remove(dir, { recursive: true });
  }
});
