/**
 * stdout guard.
 *
 * The Python bridge speaks JSON-RPC over the Deno subprocess's stdout: it reads
 * stdout line-by-line and parses each line as JSON. Deno's `console.log`,
 * `console.info` and `console.debug` all write to *stdout*, and several
 * dependencies (ipfs-car, filecoin-pin, the Synapse SDK, ...) log through them.
 * During a large upload that is thousands of non-JSON lines; the bridge has to
 * skip them, and at DEBUG volume this can stall the protocol stream and leave a
 * pending request unanswered.
 *
 * This module reserves stdout exclusively for the JSON-RPC writer (`writeRpc`)
 * and redirects every console method to stderr. It must be imported before any
 * other module so the override is installed first.
 */

const encoder = new TextEncoder();
let writeChain: Promise<void> = Promise.resolve();

/**
 * Write one JSON-RPC message to stdout, serialized so messages can never
 * interleave. Fire-and-forget by design (callers are sync), but ordered.
 */
export function writeRpc(message: string): void {
  const bytes = encoder.encode(message + '\n');
  writeChain = writeChain
    .then(() => Deno.stdout.write(bytes))
    .then(() => undefined)
    .catch((err) => {
      // Never throw from here: we may be inside an SDK callback.
      console.error('[haven-js] stdout write failed:', err);
    });
}

// Redirect every stdout-based console method to stderr. `console.warn` and
// `console.error` already write to stderr and are left untouched.
console.log = (...args: unknown[]) => console.error(...args);
console.info = (...args: unknown[]) => console.error(...args);
console.debug = (...args: unknown[]) => console.error(...args);