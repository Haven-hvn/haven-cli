/**
 * CAR file streaming helpers for upload: stat before opening a ReadableStream
 * so readiness / payment checks use a definitive byte length.
 */

export async function carFileByteLength(carPath: string): Promise<number> {
  const st = await Deno.stat(carPath);
  return st.size;
}

export async function openCarReadableStream(carPath: string): Promise<{
  handle: Deno.FsFile;
  stream: ReadableStream<Uint8Array>;
}> {
  const handle = await Deno.open(carPath, { read: true });
  return { handle, stream: handle.readable };
}
