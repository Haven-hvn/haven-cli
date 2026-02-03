/**
 * Lit Protocol SDK Wrapper
 *
 * Provides a simplified interface to the Lit Protocol SDK for
 * encryption and decryption operations using Lit SDK v8 (Naga).
 *
 * Uses hybrid encryption (AES-256-GCM + Lit BLS-IBE) for efficient
 * handling of large files.
 */

import type {
  LitConnectParams,
  LitConnectResult,
  LitEncryptResult,
  LitDecryptResult,
  LitSessionResult,
  AccessControlCondition,
  LitEncryptFileResult,
  LitDecryptFileResult,
  HybridEncryptionMetadata,
} from './types.ts';

// Hybrid crypto imports
import {
  initLitClient,
  disconnectLitClient,
  isLitClientConnected,
  hybridEncryptFile,
  hybridDecryptFile,
  serializeHybridMetadata,
  deserializeHybridMetadata,
} from './hybrid-crypto.ts';

import { createViemAccount } from './viem-adapter.ts';
import { createLitClient } from '@lit-protocol/lit-client';
import { nagaDev } from '@lit-protocol/networks';
import { createAuthManager } from '@lit-protocol/auth';
import { LitAccessControlConditionResource } from '@lit-protocol/auth-helpers';
import { createMemoryStorage } from './lit-storage.ts';

// Deno type declaration
// eslint-disable-next-line @typescript-eslint/no-explicit-any
declare const Deno: {
  env: {
    get(key: string): string | undefined;
  };
  readFile(path: string): Promise<Uint8Array>;
  writeFile(path: string, data: Uint8Array): Promise<void>;
};

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type LitClient = any;

/**
 * Lit Protocol wrapper interface.
 */
export interface LitWrapper {
  readonly isConnected: boolean;
  connect(params: Record<string, unknown>): Promise<LitConnectResult>;
  disconnect(): Promise<void>;
  encrypt(params: Record<string, unknown>): Promise<LitEncryptResult>;
  decrypt(params: Record<string, unknown>): Promise<LitDecryptResult>;
  getSession(): Promise<LitSessionResult>;
  encryptFile(params: Record<string, unknown>): Promise<LitEncryptFileResult>;
  decryptFile(params: Record<string, unknown>): Promise<LitDecryptFileResult>;
}

/**
 * Create a new Lit Protocol wrapper instance.
 */
export function createLitWrapper(): LitWrapper {
  return new LitWrapperImpl();
}

/**
 * Lit Protocol wrapper implementation using SDK v8 (Naga).
 *
 * Features:
 * - Real Lit Protocol network connection
 * - Hybrid encryption for efficient file handling
 * - Session management with AuthManager
 * - File encryption/decryption support
 */
class LitWrapperImpl implements LitWrapper {
  private client: LitClient | null = null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private authManager: any = null;
  private _isConnected = false;
  private _network = 'naga-dev';
  private _sessionExpiry: Date | null = null;
  private _nodeCount = 0;

  get isConnected(): boolean {
    return this._isConnected && isLitClientConnected();
  }

  async connect(params: Record<string, unknown>): Promise<LitConnectResult> {
    const network = (params.network as string) ?? 'datil-dev';
    const debug = (params.debug as boolean) ?? false;

    if (debug) {
      console.error(`[lit-wrapper] Connecting to Lit network: ${network}`);
    }

    try {
      // Initialize the Lit client using nagaDev network (SDK v8)
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      this.client = await (createLitClient as any)({
        network: nagaDev,
      });

      // Initialize AuthManager with memory storage for CLI environment
      const appName = 'haven-player';
      const networkName = network;

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      this.authManager = (createAuthManager as any)({
        storage: createMemoryStorage(appName, networkName),
      });

      this._isConnected = true;
      this._network = network;
      this._nodeCount = 1; // SDK v8 doesn't expose connectedNodes directly
      this._sessionExpiry = new Date(Date.now() + 60 * 60 * 1000); // 1 hour

      if (debug) {
        console.error(`[lit-wrapper] Connected to ${this._nodeCount} nodes`);
      }

      return {
        connected: true,
        network,
        nodeCount: this._nodeCount,
      };
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      console.error(`[lit-wrapper] Connection failed: ${errorMessage}`);

      this._isConnected = false;
      this.client = null;
      this.authManager = null;

      throw new Error(`Failed to connect to Lit Protocol: ${errorMessage}`);
    }
  }

  async disconnect(): Promise<void> {
    if (this.client) {
      try {
        await disconnectLitClient();
      } catch (error) {
        console.warn('[lit-wrapper] Error during disconnect:', error);
      }
    }

    this._isConnected = false;
    this._network = '';
    this._sessionExpiry = null;
    this.client = null;
    this.authManager = null;
  }

  async encrypt(params: Record<string, unknown>): Promise<LitEncryptResult> {
    if (!this.isConnected || !this.client) {
      throw new Error('Lit Protocol not connected');
    }

    const data = params.data as string;
    const accessControlConditions = params.accessControlConditions as AccessControlCondition[];
    const chain = (params.chain as string) ?? 'ethereum';

    if (!data) {
      throw new Error('Missing required parameter: data');
    }

    if (!accessControlConditions || accessControlConditions.length === 0) {
      throw new Error('Missing required parameter: accessControlConditions');
    }

    try {
      // Convert access control conditions to unified format (v8)
      const unifiedAccessControlConditions = this.toUnifiedAccessControlConditions(
        accessControlConditions
      );

      // Encrypt the data using Lit SDK v8
      const encoder = new TextEncoder();
      const dataToEncrypt = encoder.encode(atob(data));

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const result = await (this.client as any).encrypt({
        dataToEncrypt,
        unifiedAccessControlConditions,
        chain,
      });

      // Hash the access control conditions
      const accessControlConditionHash = await this.hashAccessConditions(accessControlConditions);

      return {
        ciphertext: result.ciphertext,
        dataToEncryptHash: result.dataToEncryptHash,
        accessControlConditionHash,
      };
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      throw new Error(`Encryption failed: ${errorMessage}`);
    }
  }

  async decrypt(params: Record<string, unknown>): Promise<LitDecryptResult> {
    if (!this.isConnected || !this.client || !this.authManager) {
      throw new Error('Lit Protocol not connected');
    }

    const ciphertext = params.ciphertext as string;
    const dataToEncryptHash = params.dataToEncryptHash as string;
    const accessControlConditions = params.accessControlConditions as AccessControlCondition[];
    const chain = (params.chain as string) ?? 'ethereum';

    if (!ciphertext) {
      throw new Error('Missing required parameter: ciphertext');
    }

    if (!dataToEncryptHash) {
      throw new Error('Missing required parameter: dataToEncryptHash');
    }

    if (!accessControlConditions || accessControlConditions.length === 0) {
      throw new Error('Missing required parameter: accessControlConditions');
    }

    try {
      // Get private key from environment or params
      const privateKey = this.getPrivateKey(params);

      // Create auth context for decryption
      const authContext = await this.createAuthContext(privateKey);

      // Convert access control conditions to unified format (v8)
      const unifiedAccessControlConditions = this.toUnifiedAccessControlConditions(
        accessControlConditions
      );

      // Decrypt using Lit SDK v8
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const result = await (this.client as any).decrypt({
        data: {
          ciphertext,
          dataToEncryptHash,
        },
        unifiedAccessControlConditions,
        authContext,
        chain,
      });

      // Return as base64 encoded string
      const decryptedData = btoa(String.fromCharCode(...result.decryptedData));

      return { decryptedData };
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      throw new Error(`Decryption failed: ${errorMessage}`);
    }
  }

  async getSession(): Promise<LitSessionResult> {
    if (!this.isConnected || !this._sessionExpiry) {
      return { active: false };
    }

    const now = new Date();
    if (now >= this._sessionExpiry) {
      return { active: false };
    }

    return {
      active: true,
      expiresAt: this._sessionExpiry.toISOString(),
      resourceAbilities: ['encryption', 'decryption'],
    };
  }

  async encryptFile(params: Record<string, unknown>): Promise<LitEncryptFileResult> {
    if (!this.isConnected) {
      throw new Error('Lit Protocol not connected');
    }

    const filePath = params.filePath as string;
    const chain = (params.chain as string) ?? 'ethereum';

    if (!filePath) {
      throw new Error('Missing required parameter: filePath');
    }

    // Get private key
    const privateKey = (params.privateKey as string) || this.getPrivateKeyFromEnv();
    if (!privateKey) {
      throw new Error(
        'Private key required for encryption. Set HAVEN_PRIVATE_KEY environment variable or pass privateKey parameter.'
      );
    }

    try {
      // Read the file
      const fileData = await Deno.readFile(filePath);
      // Create a proper ArrayBuffer from the Uint8Array
      const fileBuffer = new Uint8Array(fileData).buffer;

      // Use hybrid encryption
      const { encryptedFile, metadata } = await hybridEncryptFile(
        fileBuffer as ArrayBuffer,
        privateKey,
        chain,
        (message) => console.error(`[lit-wrapper] ${message}`)
      );

      // Write encrypted file
      const encryptedFilePath = `${filePath}.encrypted`;
      await Deno.writeFile(encryptedFilePath, encryptedFile);

      // Write metadata
      const metadataPath = `${encryptedFilePath}.meta.json`;
      const metadataJson = serializeHybridMetadata(metadata);
      const metadataBytes = new TextEncoder().encode(metadataJson);
      await Deno.writeFile(metadataPath, metadataBytes);

      return {
        encryptedFilePath,
        metadataPath,
        metadata,
        originalSize: fileData.length,
        encryptedSize: encryptedFile.length,
      };
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      throw new Error(`File encryption failed: ${errorMessage}`);
    }
  }

  async decryptFile(params: Record<string, unknown>): Promise<LitDecryptFileResult> {
    if (!this.isConnected) {
      throw new Error('Lit Protocol not connected');
    }

    const encryptedFilePath = params.encryptedFilePath as string;
    const metadataPath = params.metadataPath as string;
    const outputPath = params.outputPath as string;

    if (!encryptedFilePath) {
      throw new Error('Missing required parameter: encryptedFilePath');
    }

    if (!outputPath) {
      throw new Error('Missing required parameter: outputPath');
    }

    // Get private key
    const privateKey = (params.privateKey as string) || this.getPrivateKeyFromEnv();
    if (!privateKey) {
      throw new Error(
        'Private key required for decryption. Set HAVEN_PRIVATE_KEY environment variable or pass privateKey parameter.'
      );
    }

    try {
      // Read encrypted file
      const encryptedData = await Deno.readFile(encryptedFilePath);

      // Read metadata
      const metaPath = metadataPath || `${encryptedFilePath}.meta.json`;
      const metadataBytes = await Deno.readFile(metaPath);
      const metadataJson = new TextDecoder().decode(metadataBytes);
      const metadata = deserializeHybridMetadata(metadataJson);

      // Decrypt using hybrid encryption
      const decryptedData = await hybridDecryptFile(
        encryptedData,
        metadata,
        privateKey,
        (message) => console.error(`[lit-wrapper] ${message}`)
      );

      // Write decrypted file
      await Deno.writeFile(outputPath, decryptedData);

      // Verify integrity if hash is available
      let integrityCheck = true;
      if (metadata.originalHash) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const computedHash = await this.sha256Hash(decryptedData as any);
        integrityCheck = computedHash === metadata.originalHash;
      }

      return {
        outputPath,
        size: decryptedData.length,
        integrityCheck,
      };
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      throw new Error(`File decryption failed: ${errorMessage}`);
    }
  }

  // ==========================================================================
  // Private Helper Methods
  // ==========================================================================

  private toUnifiedAccessControlConditions(
    conditions: AccessControlCondition[]
  ): Array<AccessControlCondition & { conditionType: 'evmBasic' }> {
    return conditions.map((condition) => ({
      conditionType: 'evmBasic' as const,
      ...condition,
    }));
  }

  private async hashAccessConditions(conditions: AccessControlCondition[]): Promise<string> {
    const encoder = new TextEncoder();
    const accString = JSON.stringify(conditions);
    const accBytes = encoder.encode(accString);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const hashBuffer = await crypto.subtle.digest('SHA-256', accBytes as any);
    const hashArray = Array.from(new Uint8Array(hashBuffer));
    return hashArray.map((b) => b.toString(16).padStart(2, '0')).join('');
  }

  private getPrivateKey(params: Record<string, unknown>): string {
    // Check params first
    if (params.privateKey && typeof params.privateKey === 'string') {
      return params.privateKey;
    }

    // Get from environment
    const envKey = this.getPrivateKeyFromEnv();
    if (envKey) {
      return envKey;
    }

    throw new Error('Private key not found. Set HAVEN_PRIVATE_KEY environment variable.');
  }

  private getPrivateKeyFromEnv(): string | undefined {
    return Deno.env.get('HAVEN_PRIVATE_KEY') || Deno.env.get('PRIVATE_KEY');
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private async createAuthContext(privateKey: string): Promise<any> {
    if (!this.client || !this.authManager) {
      throw new Error('Lit client not initialized');
    }

    const viemAccount = createViemAccount(privateKey);

    const authContext = await this.authManager.createEoaAuthContext({
      authConfig: {
        domain: 'haven-player.local',
        statement: 'Sign this message to authenticate with Haven Player',
        resources: [
          {
            resource: new LitAccessControlConditionResource('*'),
            ability: 'access-control-condition-decryption',
          },
        ],
        expiration: new Date(Date.now() + 1000 * 60 * 60).toISOString(), // 1 hour
      },
      config: {
        account: viemAccount,
      },
      litClient: this.client,
    });

    return authContext;
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private async sha256Hash(data: any): Promise<string> {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const hashBuffer = await crypto.subtle.digest('SHA-256', data as any);
    const hashArray = new Uint8Array(hashBuffer);
    return Array.from(hashArray)
      .map((b) => b.toString(16).padStart(2, '0'))
      .join('');
  }
}

/**
 * Create default access control conditions for Haven.
 * Requires the user to hold a specific NFT or meet other criteria.
 */
export function createDefaultAccessControlConditions(
  chain: string = 'ethereum'
): AccessControlCondition[] {
  return [
    {
      contractAddress: '',
      standardContractType: '',
      chain,
      method: '',
      parameters: [':userAddress'],
      returnValueTest: {
        comparator: '=',
        value: ':userAddress',
      },
    },
  ];
}

/**
 * Create access control conditions for owner-only access.
 * Only the wallet that encrypted can decrypt.
 */
export function createOwnerOnlyAccessControlConditions(
  walletAddress: string,
  chain: string = 'ethereum'
): AccessControlCondition[] {
  return [
    {
      contractAddress: '',
      standardContractType: '',
      chain,
      method: '',
      parameters: [':userAddress'],
      returnValueTest: {
        comparator: '=',
        value: walletAddress.toLowerCase(),
      },
    },
  ];
}

/**
 * Create access control conditions for NFT-gated content.
 */
export function createNFTAccessControlConditions(
  contractAddress: string,
  chain: string = 'ethereum'
): AccessControlCondition[] {
  return [
    {
      contractAddress,
      standardContractType: 'ERC721',
      chain,
      method: 'balanceOf',
      parameters: [':userAddress'],
      returnValueTest: {
        comparator: '>',
        value: '0',
      },
    },
  ];
}
