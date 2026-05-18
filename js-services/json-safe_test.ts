import { assertEquals } from 'https://deno.land/std@0.200.0/testing/asserts.ts';
import { stringifyForRpc } from './json-safe.ts';

Deno.test('stringifyForRpc serializes bigint values as strings', () => {
  const payload = {
    dataSetId: 42n,
    copies: [{ pieceId: 7n, providerId: 3n }],
  };
  const json = stringifyForRpc(payload);
  assertEquals(json, '{"dataSetId":"42","copies":[{"pieceId":"7","providerId":"3"}]}');
  let threw = false;
  try {
    JSON.stringify(payload);
  } catch {
    threw = true;
  }
  assertEquals(threw, true);
});
