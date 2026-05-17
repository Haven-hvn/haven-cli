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
  INSUFFICIENT_BALANCE: -32006, // Actor/wallet has insufficient funds for transaction
} as const;

/**
 * Hybrid encryption metadata stored alongside the encrypted file
 */
export interface HybridEncryptionMetadata {
  /** Version identifier for future compatibility */
  version: 'hybrid-v1';
  /** Wrapped AES key (base64) */
  encryptedKey: string;
  /** SHA-256 hash of the AES key (for verification) */
  keyHash: string;
  /** Base64-encoded 12-byte IV for AES-GCM */
  iv: string;
  /** AES algorithm identifier */
  algorithm: 'AES-GCM';
  /** Key length in bits */
  keyLength: 256;
  /** Access control conditions */
  accessControlConditions: Record<string, unknown>[];
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

export interface SynapseUploadCopy {
  providerId?: string;
  dataSetId?: string | number;
  pieceId?: string | number;
  role?: string;
  retrievalUrl?: string;
  serviceProvider?: string;
}

export interface SynapseUploadResult {
  cid: string;
  pieceCid: string;
  size: number;
  uploadedAt: string;
  dealId?: string;
  complete: boolean;
  copyCount: number;
  copies: SynapseUploadCopy[];
  dataSetId?: string;
  serviceProvider?: string;
  catalogOwner?: string;
}

export interface SynapseUploadProgress {
  bytesUploaded: number;
  totalBytes: number;
  percentage: number;
}

export interface SynapseStatusParams {
  cid: string;
  pieceCid?: string;
  catalogOwner?: string;
}

export interface SynapseStatusResult {
  cid: string;
  status: 'pending' | 'active' | 'terminated' | 'unknown';
  retrievable?: boolean;
  retrievalUrl?: string;
  pieceCid?: string;
  dataSetId?: string;
  serviceProvider?: string;
  copyCount?: number;
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
  pieceCid?: string;
  catalogOwner?: string;
  timeoutMs?: number;
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
