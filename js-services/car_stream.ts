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

export interface StreamToFileProgress {
  bytesDownloaded: number;
  totalBytes: number;
}

/**
 * Stream a byte stream to a file without buffering the full payload in RAM.
 *
 * `totalBytes` is optional: callers that know the exact payload size (for
 * example from a PieceCID) can pass it so progress percentages are accurate
 * even when the response has no Content-Length header.
 */
export async function streamReadableToFile(
  stream: ReadableStream<Uint8Array>,
  outputPath: string,
  options: {
    timeoutMs: number;
    totalBytes?: number;
    onProgress?: (progress: StreamToFileProgress) => void;
  }
): Promise<number> {
  const deadline = Date.now() + options.timeoutMs;

  const file = await Deno.open(outputPath, { write: true, create: true, truncate: true });
  const reader = stream.getReader();
  let bytesDownloaded = 0;

  try {
    while (true) {
      if (Date.now() > deadline) {
        throw new Error(`Download timed out after ${options.timeoutMs}ms`);
      }

      const { done, value } = await readWithDeadline(reader, deadline, options.timeoutMs);
      if (done) {
        break;
      }

      await file.write(value);
      bytesDownloaded += value.length;
      options.onProgress?.({ bytesDownloaded, totalBytes: options.totalBytes ?? 0 });
    }
  } catch (error) {
    // Unblock a stalled producer (and release the pending read) before the
    // caller sees the timeout/stream error.
    try {
      await reader.cancel();
    } catch {
      /* ignore */
    }
    throw error;
  } finally {
    try {
      reader.releaseLock();
    } catch {
      /* ignore */
    }
    file.close();
  }

  return bytesDownloaded;
}

/**
 * Read one chunk, rejecting if the absolute deadline passes while the read is
 * still pending. The per-read timer is cleared as soon as the chunk arrives.
 */
async function readWithDeadline(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  deadline: number,
  timeoutMs: number
): Promise<ReadableStreamReadResult<Uint8Array>> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      reader.read(),
      new Promise<never>((_, reject) => {
        const remaining = deadline - Date.now();
        timer = setTimeout(
          () => reject(new Error(`Download timed out after ${timeoutMs}ms`)),
          Math.max(0, remaining)
        );
      }),
    ]);
  } finally {
    if (timer !== undefined) {
      clearTimeout(timer);
    }
  }
}

/**
 * Stream an HTTP response body to a file without buffering the full payload in RAM.
 */
export async function streamHttpResponseToFile(
  response: Response,
  outputPath: string,
  options: {
    timeoutMs: number;
    onProgress?: (progress: StreamToFileProgress) => void;
  }
): Promise<number> {
  if (!response.body) {
    throw new Error('Response has no body');
  }

  const contentLength = response.headers.get('content-length');
  const totalBytes = contentLength ? parseInt(contentLength, 10) : 0;

  return streamReadableToFile(response.body, outputPath, {
    ...options,
    totalBytes,
  });
}
