/**
 * Memory-based Storage for Lit Protocol Auth in Deno CLI Environment
 *
 * This module provides a memory-based storage adapter for Lit Protocol's
 * AuthManager when running in a Deno/Node.js environment (no localStorage).
 *
 * NOTE: Session data is NOT persisted - auth signatures will be recreated
 * on each session. This is acceptable for CLI usage where the wallet
 * private key is always available.
 */

// Type definitions for Lit Protocol storage - simplified for SDK compatibility
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type LitAuthData = any;

interface PKPData {
  tokenId: string;
  publicKey: string;
  ethAddress: string;
}

interface StorageConfig {
  appName: string;
  networkName: string;
  storageType: 'memory' | 'localStorage';
}

/**
 * LitAuthStorageProvider interface
 * Matches the interface expected by Lit Protocol's AuthManager
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export interface LitAuthStorageProvider {
  config: StorageConfig;
  read<T extends { address: string }>(params: T): Promise<LitAuthData | null>;
  write<T extends { address: string; authData: LitAuthData }>(params: T): Promise<void>;
  writeInnerDelegationAuthSig(params: {
    publicKey: string;
    authSig: string;
  }): Promise<void>;
  readInnerDelegationAuthSig(params: { publicKey: string }): Promise<string | null>;
  writePKPTokens(params: {
    authMethodType: number | bigint;
    authMethodId: string;
    tokenIds: string[];
  }): Promise<void>;
  readPKPTokens(params: {
    authMethodType: number | bigint;
    authMethodId: string;
  }): Promise<string[] | null>;
  writePKPs(params: {
    authMethodType: number | bigint;
    authMethodId: string;
    pkps: PKPData[];
  }): Promise<void>;
  readPKPs(params: {
    authMethodType: number | bigint;
    authMethodId: string;
  }): Promise<PKPData[] | null>;
  writePKPDetails(params: {
    tokenId: string;
    publicKey: string;
    ethAddress: string;
  }): Promise<void>;
  readPKPDetails(params: {
    tokenId: string;
  }): Promise<{ publicKey: string; ethAddress: string } | null>;
  writePKPTokensByAddress(params: {
    ownerAddress: string;
    tokenIds: string[];
  }): Promise<void>;
  readPKPTokensByAddress(params: {
    ownerAddress: string;
  }): Promise<string[] | null>;
}

/**
 * Create a memory-based storage adapter for Deno/Node.js environments.
 *
 * This storage provider keeps all data in memory and is cleared when
 * the process exits. It's suitable for CLI usage where session
 * persistence across process restarts is not required.
 */
export function createMemoryStorage(
  appName: string,
  networkName: string
): LitAuthStorageProvider {
  // In-memory storage maps
  const authDataStore = new Map<string, LitAuthData>();
  const innerDelegationStore = new Map<string, string>();
  const pkpTokensStore = new Map<string, { tokenIds: string[]; timestamp: number }>();
  const pkpFullStore = new Map<string, { pkps: PKPData[]; timestamp: number }>();
  const pkpDetailsStore = new Map<string, { publicKey: string; ethAddress: string }>();
  const pkpAddressStore = new Map<string, { tokenIds: string[]; timestamp: number }>();

  const AUTH_PREFIX = 'lit-auth';
  const PKP_PREFIX = 'lit-pkp-tokens';
  const PKP_FULL_PREFIX = 'lit-pkp-full';
  const PKP_DETAILS_PREFIX = 'lit-pkp-details';
  const PKP_ADDRESS_PREFIX = 'lit-pkp-address';

  function buildLookupKey(address: string): string {
    return `${AUTH_PREFIX}:${appName}:${networkName}:${address}`;
  }

  function buildPKPCacheKey(authMethodType: number | bigint, authMethodId: string): string {
    return `${PKP_PREFIX}:${appName}:${networkName}:${authMethodType}:${authMethodId}`;
  }

  function buildPKPFullCacheKey(authMethodType: number | bigint, authMethodId: string): string {
    return `${PKP_FULL_PREFIX}:${appName}:${networkName}:${authMethodType}:${authMethodId}`;
  }

  function buildPKPDetailsCacheKey(tokenId: string): string {
    return `${PKP_DETAILS_PREFIX}:${appName}:${networkName}:${tokenId}`;
  }

  function buildPKPAddressCacheKey(ownerAddress: string): string {
    return `${PKP_ADDRESS_PREFIX}:${appName}:${networkName}:${ownerAddress}`;
  }

  return {
    config: { appName, networkName, storageType: 'memory' },

    async read<T extends { address: string }>(params: T): Promise<LitAuthData | null> {
      const key = buildLookupKey(params.address);
      return authDataStore.get(key) ?? null;
    },

    async write<T extends { address: string; authData: LitAuthData }>(params: T): Promise<void> {
      const key = buildLookupKey(params.address);
      authDataStore.set(key, params.authData);
    },

    async writeInnerDelegationAuthSig(params: {
      publicKey: string;
      authSig: string;
    }): Promise<void> {
      const key = buildLookupKey(`${appName}-inner-delegation:${params.publicKey}`);
      innerDelegationStore.set(key, params.authSig);
    },

    async readInnerDelegationAuthSig(params: { publicKey: string }): Promise<string | null> {
      const key = buildLookupKey(`${appName}-inner-delegation:${params.publicKey}`);
      return innerDelegationStore.get(key) ?? null;
    },

    async writePKPTokens(params: {
      authMethodType: number | bigint;
      authMethodId: string;
      tokenIds: string[];
    }): Promise<void> {
      const key = buildPKPCacheKey(params.authMethodType, params.authMethodId);
      pkpTokensStore.set(key, { tokenIds: params.tokenIds, timestamp: Date.now() });
    },

    async readPKPTokens(params: {
      authMethodType: number | bigint;
      authMethodId: string;
    }): Promise<string[] | null> {
      const key = buildPKPCacheKey(params.authMethodType, params.authMethodId);
      const value = pkpTokensStore.get(key);
      return value?.tokenIds ?? null;
    },

    async writePKPs(params: {
      authMethodType: number | bigint;
      authMethodId: string;
      pkps: PKPData[];
    }): Promise<void> {
      const key = buildPKPFullCacheKey(params.authMethodType, params.authMethodId);
      pkpFullStore.set(key, { pkps: params.pkps, timestamp: Date.now() });
    },

    async readPKPs(params: {
      authMethodType: number | bigint;
      authMethodId: string;
    }): Promise<PKPData[] | null> {
      const key = buildPKPFullCacheKey(params.authMethodType, params.authMethodId);
      const value = pkpFullStore.get(key);
      return value?.pkps ?? null;
    },

    async writePKPDetails(params: {
      tokenId: string;
      publicKey: string;
      ethAddress: string;
    }): Promise<void> {
      const key = buildPKPDetailsCacheKey(params.tokenId);
      pkpDetailsStore.set(key, {
        publicKey: params.publicKey,
        ethAddress: params.ethAddress,
      });
    },

    async readPKPDetails(params: {
      tokenId: string;
    }): Promise<{ publicKey: string; ethAddress: string } | null> {
      const key = buildPKPDetailsCacheKey(params.tokenId);
      return pkpDetailsStore.get(key) ?? null;
    },

    async writePKPTokensByAddress(params: {
      ownerAddress: string;
      tokenIds: string[];
    }): Promise<void> {
      const key = buildPKPAddressCacheKey(params.ownerAddress);
      pkpAddressStore.set(key, { tokenIds: params.tokenIds, timestamp: Date.now() });
    },

    async readPKPTokensByAddress(params: {
      ownerAddress: string;
    }): Promise<string[] | null> {
      const key = buildPKPAddressCacheKey(params.ownerAddress);
      const value = pkpAddressStore.get(key);
      return value?.tokenIds ?? null;
    },
  };
}
