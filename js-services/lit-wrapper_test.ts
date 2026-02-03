/**
 * Tests for Lit Protocol SDK Wrapper
 *
 * These tests verify the Lit Protocol integration including:
 * - Connection to Lit network
 * - String encryption/decryption
 * - File encryption/decryption
 * - Session management
 */

import { assertEquals, assertExists, assertRejects } from 'https://deno.land/std@0.200.0/testing/asserts.ts';
import { createLitWrapper, createOwnerOnlyAccessControlConditions } from './lit-wrapper.ts';
import type { LitWrapper } from './lit-wrapper.ts';
import { installBrowserShim } from './browser-shim.ts';

// Install browser shim before tests
installBrowserShim();

// Test private key (this is a test key, not a real one)
const TEST_PRIVATE_KEY = Deno.env.get('HAVEN_PRIVATE_KEY') ||
  '0x' + '1234567890abcdef'.repeat(4) + '1234567890abcdef'.repeat(4);

Deno.test('LitWrapper - create instance', () => {
  const wrapper = createLitWrapper();
  assertExists(wrapper);
  assertEquals(wrapper.isConnected, false);
});

Deno.test('LitWrapper - connect to Lit network', async () => {
  const wrapper = createLitWrapper();

  const result = await wrapper.connect({
    network: 'datil-dev',
    debug: true,
  });

  assertEquals(result.connected, true);
  assertEquals(result.network, 'datil-dev');
  assertEquals(wrapper.isConnected, true);

  // Cleanup
  await wrapper.disconnect();
});

Deno.test('LitWrapper - session management', async () => {
  const wrapper = createLitWrapper();

  // Before connection
  let session = await wrapper.getSession();
  assertEquals(session.active, false);

  // After connection
  await wrapper.connect({ network: 'datil-dev' });
  session = await wrapper.getSession();
  assertEquals(session.active, true);
  assertExists(session.expiresAt);
  assertExists(session.resourceAbilities);
  assertEquals(session.resourceAbilities?.includes('encryption'), true);
  assertEquals(session.resourceAbilities?.includes('decryption'), true);

  // After disconnect
  await wrapper.disconnect();
  session = await wrapper.getSession();
  assertEquals(session.active, false);
});

Deno.test('LitWrapper - encrypt without connection fails', async () => {
  const wrapper = createLitWrapper();

  await assertRejects(
    async () => {
      await wrapper.encrypt({
        data: btoa('test data'),
        accessControlConditions: createOwnerOnlyAccessControlConditions(
          '0x1234567890123456789012345678901234567890'
        ),
      });
    },
    Error,
    'not connected'
  );
});

Deno.test('LitWrapper - decrypt without connection fails', async () => {
  const wrapper = createLitWrapper();

  await assertRejects(
    async () => {
      await wrapper.decrypt({
        ciphertext: 'test',
        dataToEncryptHash: 'test',
        accessControlConditions: createOwnerOnlyAccessControlConditions(
          '0x1234567890123456789012345678901234567890'
        ),
      });
    },
    Error,
    'not connected'
  );
});

Deno.test('LitWrapper - encrypt file without connection fails', async () => {
  const wrapper = createLitWrapper();

  await assertRejects(
    async () => {
      await wrapper.encryptFile({
        filePath: '/tmp/test.txt',
        privateKey: TEST_PRIVATE_KEY,
      });
    },
    Error,
    'not connected'
  );
});

Deno.test('LitWrapper - decrypt file without connection fails', async () => {
  const wrapper = createLitWrapper();

  await assertRejects(
    async () => {
      await wrapper.decryptFile({
        encryptedFilePath: '/tmp/test.txt.encrypted',
        outputPath: '/tmp/test-out.txt',
        privateKey: TEST_PRIVATE_KEY,
      });
    },
    Error,
    'not connected'
  );
});

Deno.test('LitWrapper - helper functions', () => {
  const { createDefaultAccessControlConditions, createNFTAccessControlConditions } = await import(
    './lit-wrapper.ts'
  );

  // Test default access control conditions
  const defaultConditions = createDefaultAccessControlConditions('ethereum');
  assertEquals(defaultConditions.length, 1);
  assertEquals(defaultConditions[0].chain, 'ethereum');
  assertEquals(defaultConditions[0].returnValueTest.comparator, '=');

  // Test owner-only access control conditions
  const ownerConditions = createOwnerOnlyAccessControlConditions(
    '0x1234567890123456789012345678901234567890',
    'ethereum'
  );
  assertEquals(ownerConditions.length, 1);
  assertEquals(ownerConditions[0].returnValueTest.value, '0x1234567890123456789012345678901234567890');

  // Test NFT access control conditions
  const nftConditions = createNFTAccessControlConditions(
    '0xContractAddress',
    'ethereum'
  );
  assertEquals(nftConditions.length, 1);
  assertEquals(nftConditions[0].contractAddress, '0xContractAddress');
  assertEquals(nftConditions[0].standardContractType, 'ERC721');
  assertEquals(nftConditions[0].method, 'balanceOf');
});

// Integration test - requires network connectivity to Lit Protocol
// This test is skipped by default as it requires network access
Deno.test({
  name: 'LitWrapper - full encryption/decryption flow (integration)',
  ignore: true, // Set to false to run integration tests
  async fn() {
    const wrapper = createLitWrapper();

    // Connect
    await wrapper.connect({ network: 'datil-dev', debug: true });

    // Set up test data
    const testData = 'Hello, Lit Protocol!';
    const testDataB64 = btoa(testData);
    const walletAddress = '0x1234567890123456789012345678901234567890';

    // Create access control conditions
    const accessControlConditions = createOwnerOnlyAccessControlConditions(walletAddress);

    // Encrypt
    const encryptResult = await wrapper.encrypt({
      data: testDataB64,
      accessControlConditions,
      chain: 'ethereum',
    });

    assertExists(encryptResult.ciphertext);
    assertExists(encryptResult.dataToEncryptHash);
    assertExists(encryptResult.accessControlConditionHash);

    // Note: Decryption would fail without proper authentication
    // as it requires the private key matching the wallet address

    // Cleanup
    await wrapper.disconnect();
  },
});
