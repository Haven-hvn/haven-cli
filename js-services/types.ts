/**
 * Shared types for the Haven JS runtime services.
 */

// ============================================================================
// JSON-RPC Types
// ============================================================================

export interface JSONRPCRequest {
  jsonrpc: '2.0';
  method: string;
  params?: unknown[] | Record<string, unknown>;
  id?: string | number | null;
}

export interface JSONRPCResponse {
  jsonrpc: '2.0';
  result?: unknown;
  error?: JSONRPCError;
  id: string | number | null;
}

export interface JSONRPCError {
  code: number;
  message: string;
  data?: unknown;
}

// Standard JSON-RPC error codes
export const ErrorCodes = {
  PARSE_ERROR: -32700,
  INVALID_REQUEST: -32600,
  METHOD_NOT_FOUND: -32601,
  INVALID_PARAMS: -32602,
  INTERNAL_ERROR: -32603,
  // Custom error codes
  SERVER_ERROR: -32000,
  TIMEOUT_ERROR: -32001,
  RUNTIME_NOT_READY: -32002,
  SDK_ERROR: -32003,
  ENCRYPTION_ERROR: -32004,
  UPLOAD_ERROR: -32005,
} as const;

// ============================================================================
// Lit Protocol Types
// ============================================================================

export interface LitConnectParams {
  network?: 'cayenne' | 'manzano' | 'habanero' | 'datil-dev' | 'datil-test' | 'datil';
  debug?: boolean;
}

export interface LitConnectResult {
  connected: boolean;
  network: string;
  nodeCount: number;
}

export interface LitEncryptParams {
  data: string; // Base64 encoded data
  accessControlConditions: AccessControlCondition[];
  chain?: string;
}

export interface LitEncryptResult {
  ciphertext: string; // Base64 encoded
  dataToEncryptHash: string;
  accessControlConditionHash: string;
}

export interface LitDecryptParams {
  ciphertext: string; // Base64 encoded
  dataToEncryptHash: string;
  accessControlConditions: AccessControlCondition[];
  chain?: string;
  authSig?: AuthSig;
}

export interface LitDecryptResult {
  decryptedData: string; // Base64 encoded
}

export interface AccessControlCondition {
  contractAddress?: string;
  standardContractType?: string;
  chain: string;
  method?: string;
  parameters?: string[];
  returnValueTest: {
    comparator: string;
    value: string;
  };
}

export interface AuthSig {
  sig: string;
  derivedVia: string;
  signedMessage: string;
  address: string;
}

export interface LitSessionResult {
  active: boolean;
  expiresAt?: string;
  resourceAbilities?: string[];
}

// ============================================================================
// Lit Protocol File Encryption Types
// ============================================================================

/**
 * Parameters for encrypting a file using hybrid encryption
 */
export interface LitEncryptFileParams {
  /** File path to encrypt (relative to working directory) */
  filePath: string;
  /** Access control conditions for decryption */
  accessControlConditions?: AccessControlCondition[];
  /** Blockchain chain (default: 'ethereum') */
  chain?: string;
  /** Private key for creating access control (if not provided, uses HAVEN_PRIVATE_KEY env) */
  privateKey?: string;
}

/**
 * Result of file encryption operation
 */
export interface LitEncryptFileResult {
  /** Path to the encrypted file */
  encryptedFilePath: string;
  /** Path to the metadata file */
  metadataPath: string;
  /** Encryption metadata */
  metadata: HybridEncryptionMetadata;
  /** Size of original file in bytes */
  originalSize: number;
  /** Size of encrypted file in bytes */
  encryptedSize: number;
}

/**
 * Parameters for decrypting a file
 */
export interface LitDecryptFileParams {
  /** Path to the encrypted file */
  encryptedFilePath: string;
  /** Path to the metadata file (optional, will use .meta.json suffix if not provided) */
  metadataPath?: string;
  /** Output path for decrypted file */
  outputPath: string;
  /** Private key for authentication (if not provided, uses HAVEN_PRIVATE_KEY env) */
  privateKey?: string;
}

/**
 * Result of file decryption operation
 */
export interface LitDecryptFileResult {
  /** Path to the decrypted file */
  outputPath: string;
  /** Size of decrypted file in bytes */
  size: number;
  /** Whether integrity check passed */
  integrityCheck: boolean;
}

/**
 * Hybrid encryption metadata stored alongside the encrypted file
 */
export interface HybridEncryptionMetadata {
  /** Version identifier for future compatibility */
  version: 'hybrid-v1';
  /** BLS-encrypted AES key (base64-encoded ciphertext from Lit) */
  encryptedKey: string;
  /** SHA-256 hash of the AES key (for verification) */
  keyHash: string;
  /** Base64-encoded 12-byte IV for AES-GCM */
  iv: string;
  /** AES algorithm identifier */
  algorithm: 'AES-GCM';
  /** Key length in bits */
  keyLength: 256;
  /** Access control conditions for Lit decryption */
  accessControlConditions: AccessControlCondition[];
  /** Blockchain chain identifier */
  chain: string;
  /** Optional: Original file MIME type */
  originalMimeType?: string;
  /** Optional: Original file size in bytes */
  originalSize?: number;
  /** Optional: SHA-256 hash of original file content */
  originalHash?: string;
}

// ============================================================================
// Synapse SDK Types
// ============================================================================

export interface SynapseConnectParams {
  apiKey?: string;
  endpoint?: string;
}

export interface SynapseConnectResult {
  connected: boolean;
  endpoint: string;
}

export interface SynapseUploadParams {
  filePath: string;
  metadata?: Record<string, string>;
  onProgress?: boolean; // If true, emit progress notifications
}

export interface SynapseUploadResult {
  cid: string;
  size: number;
  uploadedAt: string;
  dealId?: string;
}

export interface SynapseUploadProgress {
  bytesUploaded: number;
  totalBytes: number;
  percentage: number;
}

export interface SynapseStatusParams {
  cid: string;
}

export interface SynapseStatusResult {
  cid: string;
  status: 'pending' | 'active' | 'terminated' | 'unknown';
  deals: SynapseDeal[];
}

export interface SynapseDeal {
  dealId: string;
  provider: string;
  status: string;
  startEpoch?: number;
  endEpoch?: number;
}

export interface SynapseDownloadParams {
  cid: string;
  outputPath: string;
  onProgress?: boolean; // If true, emit progress notifications
}

export interface SynapseDownloadResult {
  success: boolean;
  size: number;
  cid: string;
  outputPath: string;
}

export interface SynapseDownloadProgress {
  bytesDownloaded: number;
  totalBytes: number;
  percentage: number;
}

export interface SynapseCreateCarParams {
  filePath: string;
  outputPath?: string;
}

export interface SynapseCreateCarResult {
  carPath: string;
  rootCid: string;
  size: number;
}

// ============================================================================
// Arkiv Types
// ============================================================================

export interface ArkivSyncParams {
  videoId: string;
  cid: string;
  metadata: ArkivMetadata;
}

export interface ArkivMetadata {
  title?: string;
  description?: string;
  duration?: number;
  phash?: string;
  encryptionKeyHash?: string;
  uploadTimestamp: string;
  sourcePlugin?: string;
  sourceId?: string;
}

export interface ArkivSyncResult {
  txHash: string;
  blockNumber?: number;
  recordId: string;
}

export interface ArkivVerifyParams {
  recordId: string;
}

export interface ArkivVerifyResult {
  verified: boolean;
  record?: ArkivRecord;
  error?: string;
}

export interface ArkivRecord {
  recordId: string;
  videoId: string;
  cid: string;
  metadata: ArkivMetadata;
  txHash: string;
  blockNumber: number;
  timestamp: string;
}

// ============================================================================
// Runtime Status Types
// ============================================================================

export interface RuntimeStatus {
  version: string;
  uptimeSeconds: number;
  litConnected: boolean;
  synapseConnected: boolean;
  pendingRequests: number;
}

// ============================================================================
// Method Handler Type
// ============================================================================

export type MethodHandler<TParams = unknown, TResult = unknown> = (
  params: TParams
) => Promise<TResult>;

export interface MethodRegistry {
  [method: string]: MethodHandler;
}
