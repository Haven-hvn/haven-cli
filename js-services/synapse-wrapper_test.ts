/**
 * Tests for the Synapse SDK wrapper
 */
import { assertEquals, assertRejects } from 'https://deno.land/std@0.200.0/testing/asserts.ts';
import { createSynapseWrapper, isValidCid, formatBytes } from './synapse-wrapper.ts';

Deno.test('createSynapseWrapper creates a wrapper instance', () => {
  const wrapper = createSynapseWrapper();
  assertEquals(typeof wrapper, 'object');
  assertEquals(wrapper.isConnected, false);
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
