/**
 * JSON serialization helpers for the Python JSON-RPC bridge.
 * FOC / viem types often use bigint, which JSON.stringify cannot encode.
 */

export function jsonReplacer(_key: string, value: unknown): unknown {
  if (typeof value === 'bigint') {
    return value.toString();
  }
  return value;
}

export function stringifyForRpc(value: unknown): string {
  return JSON.stringify(value, jsonReplacer);
}
