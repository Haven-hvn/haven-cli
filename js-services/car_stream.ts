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
  const deadline = Date.now() + options.timeoutMs;

  const file = await Deno.open(outputPath, { write: true, create: true, truncate: true });
  const reader = response.body.getReader();
  let bytesDownloaded = 0;

  try {
    while (true) {
      if (Date.now() > deadline) {
        throw new Error(`Download timed out after ${options.timeoutMs}ms`);
      }

      const { done, value } = await reader.read();
      if (done) {
        break;
      }

      await file.write(value);
      bytesDownloaded += value.length;
      options.onProgress?.({ bytesDownloaded, totalBytes });
    }
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
