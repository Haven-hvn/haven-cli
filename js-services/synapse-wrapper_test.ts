/**
 * Tests for the Synapse SDK wrapper
 */
import { assertEquals, assertRejects } from 'https://deno.land/std@0.200.0/testing/asserts.ts';
import { CID } from 'multiformats/cid';
import {
  buildStorageFundingOptions,
  createSynapseWrapper,
  DEFAULT_STORAGE_RUNWAY_DAYS,
  filecoinWithCdnEnabled,
  formatBytes,
  isValidCid,
  resolveReuseDataSetId,
  synapseCopies,
  synapseProviderIds,
} from './synapse-wrapper.ts';

Deno.test('filecoinWithCdnEnabled defaults true unless env disables', () => {
  const prev = Deno.env.get('HAVEN_FILECOIN_WITH_CDN');
  try {
    Deno.env.delete('HAVEN_FILECOIN_WITH_CDN');
    assertEquals(filecoinWithCdnEnabled(), true);
    Deno.env.set('HAVEN_FILECOIN_WITH_CDN', 'false');
    assertEquals(filecoinWithCdnEnabled(), false);
    Deno.env.set('HAVEN_FILECOIN_WITH_CDN', '0');
    assertEquals(filecoinWithCdnEnabled(), false);
  } finally {
    if (prev === undefined) {
      Deno.env.delete('HAVEN_FILECOIN_WITH_CDN');
    } else {
      Deno.env.set('HAVEN_FILECOIN_WITH_CDN', prev);
    }
  }
});

Deno.test('createSynapseWrapper creates a wrapper instance', () => {
  const wrapper = createSynapseWrapper();
  assertEquals(typeof wrapper, 'object');
  assertEquals(wrapper.isConnected, false);
});

Deno.test('CID.parse roundtrips v1 CIDs used as upload root', () => {
  const s =
    'bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi';
  assertEquals(CID.parse(s).toString(), s);
});

Deno.test('isValidCid validates CID formats correctly', () => {
  // Valid CIDv0
  assertEquals(isValidCid('QmYwAPJzv5CZsnAzt8auqD9BKKy1CjV5v8wRWGW4Y8Y1zT'), true);
  
  // Valid CIDv1
  assertEquals(isValidCid('bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi'), true);
  
  // Invalid CIDs
  assertEquals(isValidCid('invalid'), false);
  assertEquals(isValidCid(''), false);
  assertEquals(isValidCid('Qm'), false); // Too short
  assertEquals(isValidCid('bafy'), false); // Too short
});

Deno.test('formatBytes formats bytes correctly', () => {
  assertEquals(formatBytes(0), '0 B');
  assertEquals(formatBytes(1024), '1 KB');
  assertEquals(formatBytes(1024 * 1024), '1 MB');
  assertEquals(formatBytes(1024 * 1024 * 1024), '1 GB');
});

Deno.test('SynapseWrapper.validateFileSize validates file sizes', () => {
  const wrapper = createSynapseWrapper();
  
  // Small file should be valid
  const smallResult = wrapper.validateFileSize(1024, false);
  assertEquals(smallResult.valid, true);
  
  // File that's too small
  const tooSmallResult = wrapper.validateFileSize(100, false);
  assertEquals(tooSmallResult.valid, false);
  assertEquals(tooSmallResult.reason, 'TOO_SMALL');
});

Deno.test('SynapseWrapper throws when not connected', async () => {
  const wrapper = createSynapseWrapper();
  
  await assertRejects(
    () => wrapper.upload({ filePath: '/tmp/test.mp4' }),
    Error,
    'Synapse not connected'
  );
  
  await assertRejects(
    () => wrapper.getStatus({ cid: 'test' }),
    Error,
    'Synapse not connected'
  );
  
  await assertRejects(
    () => wrapper.download({ cid: 'test', outputPath: '/tmp/test.mp4' }),
    Error,
    'Synapse not connected'
  );
});

Deno.test('upload tops up Filecoin Pay runway before readiness check', () => {
  // Regression: upload used to only check readiness and throw
  // "Deposit at least X USDFC" when the Filecoin Pay balance was below the
  // required amount, even with USDFC in the wallet. The wrapper must
  // auto-fund storage runway first.
  const opts = buildStorageFundingOptions(18505657, true);
  assertEquals(opts.targetRunwayDays, DEFAULT_STORAGE_RUNWAY_DAYS);
  assertEquals(opts.pieceSizeBytes, 18505657);
  assertEquals(opts.withCDN, true);
  assertEquals(opts.ensureAllowances, true);
  assertEquals(opts.newDataSetCount, 1);
  assertEquals(DEFAULT_STORAGE_RUNWAY_DAYS, 30);
  // Reusing an existing data set needs no new-dataset lockup funding.
  assertEquals(buildStorageFundingOptions(18505657, true, 0).newDataSetCount, 0);
});

Deno.test('synapseCopies defaults to 1, env overrides', () => {
  const prevCopies = Deno.env.get('HAVEN_SYNAPSE_COPIES');
  const prevProviders = Deno.env.get('HAVEN_SYNAPSE_PROVIDER_IDS');
  try {
    Deno.env.delete('HAVEN_SYNAPSE_COPIES');
    assertEquals(synapseCopies(), 1);
    Deno.env.set('HAVEN_SYNAPSE_COPIES', '2');
    assertEquals(synapseCopies(), 2);
    Deno.env.set('HAVEN_SYNAPSE_COPIES', 'garbage');
    assertEquals(synapseCopies(), 1);
    Deno.env.delete('HAVEN_SYNAPSE_PROVIDER_IDS');
    assertEquals(synapseProviderIds(), []);
    Deno.env.set('HAVEN_SYNAPSE_PROVIDER_IDS', '9, 4');
    assertEquals(synapseProviderIds(), [9n, 4n]);
  } finally {
    for (const [key, prev] of [['HAVEN_SYNAPSE_COPIES', prevCopies], ['HAVEN_SYNAPSE_PROVIDER_IDS', prevProviders]] as const) {
      if (prev === undefined) Deno.env.delete(key);
      else Deno.env.set(key, prev);
    }
  }
});

Deno.test('resolveReuseDataSetId picks oldest live set on pinned provider', () => {
  const sets = [
    { dataSetId: 52243n, providerId: 2n, isLive: true },
    { dataSetId: 52242n, providerId: 9n, isLive: true },
    { dataSetId: 52241n, providerId: 9n, isLive: false },
  ];
  assertEquals(resolveReuseDataSetId(sets, [9n]), 52242n);
  assertEquals(resolveReuseDataSetId(sets, []), 52242n);
  assertEquals(resolveReuseDataSetId(sets, [7n]), undefined);
  assertEquals(resolveReuseDataSetId([], []), undefined);
});

Deno.test('resolveReuseDataSetId prefers the recorded epoch set (item 12)', () => {
  const sets = [
    { dataSetId: 52242n, providerId: 9n, isLive: true },
    { dataSetId: 52244n, providerId: 9n, isLive: true },
  ];
  // Epoch 686 recorded 52244: reusing it beats the oldest-first rule.
  assertEquals(resolveReuseDataSetId(sets, [9n], 52244n), 52244n);
  // A dead or unknown preferred set falls back to oldest-live.
  assertEquals(resolveReuseDataSetId(sets, [9n], 52300n), 52242n);
  // Fresh (first upload of a new epoch) always creates a new set.
  assertEquals(resolveReuseDataSetId(sets, [9n], 52244n, true), undefined);
});
