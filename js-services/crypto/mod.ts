/**
 * Crypto Module
 *
 * Low-level cryptographic primitives for hybrid encryption.
 *
 * ## Submodules
 *
 * - **AES**: `aes.ts` - In-memory AES-256-GCM encryption
 * - **Streaming AES**: `aes-streaming.ts` - Memory-efficient streaming encryption
 * - **Utils**: `utils.ts` - Key generation, hashing, and data conversion utilities
 * - **Streaming Utils**: `utils-streaming.ts` - Streaming hash computation
 *
 * ## Usage Examples
 *
 * ### One-shot Encryption (Small Data)
 * ```typescript
 * import { generateAESKey, generateIV, aesEncrypt } from '@scope/crypto';
 *
 * const key = generateAESKey();
 * const iv = generateIV();
 * const encrypted = await aesEncrypt(data, key, iv);
 * ```
 *
 * ### Streaming Encryption (Large Files)
 * ```typescript
 * import { aesEncryptStream, sha256HashStream } from '@scope/crypto';
 *
 * const fileStream = Deno.openSync('large-file.bin').readable;
 * const key = generateAESKey();
 *
 * // Compute hash while streaming
 * const hash = await sha256HashStream(fileStream);
 *
 * // Encrypt with progress tracking
 * for await (const chunk of aesEncryptStream(fileStream, key, {
 *   onProgress: (p) => console.log(`${p.percent}% complete`)
 * })) {
 *   await writeChunk(chunk);
 * }
 * ```
 *
 * @module
 */

// ============================================================================
// Types
// ============================================================================

export type {
  // Encryption Metadata Types
  HybridEncryptionMetadata,
  HybridEncryptionResult,
  ChunkedEncryptionMetadata,
  ChunkedDecryptionResult,
  // Progress Callback Types
  EncryptionProgressCallback,
  MessageProgressCallback,
  StreamProgressCallback,
  // Streaming Types
  StreamingEncryptionResult,
  StreamingDecryptionResult,
  ChunkInfo,
  StreamHeader,
  FileStreamOptions,
  EncryptionStreamOptions,
  DecryptionStreamOptions,
  EncryptedChunk,
  DecryptedChunk,
} from './types.ts';

// ============================================================================
// Constants
// ============================================================================

export {
  /** AES-256 key size in bytes (32) */
  AES_KEY_SIZE,
  /** AES-GCM IV size in bytes (12) */
  AES_IV_SIZE,
  /** AES-GCM authentication tag size in bytes (16) */
  AES_AUTH_TAG_SIZE,
  /** Default chunk size for streaming operations: 1MB */
  DEFAULT_CHUNK_SIZE,
  /** Threshold for automatic chunked encryption: 50MB */
  CHUNKED_THRESHOLD,
  /** Header size for chunked encryption format: 16 bytes */
  CHUNKED_HEADER_SIZE,
  /** Per-chunk overhead: 24 bytes (index + length + auth tag) */
  CHUNK_OVERHEAD,
} from './constants.ts';

// ============================================================================
// Core AES Encryption
// ============================================================================

export {
  /** Generate a random AES-256 key */
  generateAESKey,
  /** Generate a random AES-GCM IV */
  generateIV,
  /** One-shot AES-256-GCM encryption */
  aesEncrypt,
  /** One-shot AES-256-GCM decryption */
  aesDecrypt,
  /** Chunked AES encryption with progress reporting */
  aesEncryptChunked,
  /** Chunked AES decryption with progress reporting */
  aesDecryptChunked,
  /** Calculate expected encrypted size for chunked format */
  getChunkedEncryptedSize,
  /** Check if data is in chunked encryption format */
  isChunkedEncryption,
  /** Calculate encrypted size for standard (non-chunked) encryption */
  getEncryptedSize,
} from './aes.ts';

// ============================================================================
// Streaming AES Encryption
// ============================================================================

export {
  /** Streaming AES-256-GCM encryption with header */
  aesEncryptStream,
  /** Streaming AES-256-GCM encryption with immediate output */
  aesEncryptStreamImmediate,
  /** Streaming AES-256-GCM decryption */
  aesDecryptStream,
  /** Error class for decryption failures */
  DecryptionError,
} from './aes-streaming.ts';

export type {
  /** Options for streaming AES encryption */
  AESStreamingEncryptOptions,
  /** Result of streaming encryption initialization */
  StreamingEncryptInit,
  /** Options for streaming AES decryption */
  AESStreamingDecryptOptions,
} from './aes-streaming.ts';

// ============================================================================
// Utilities
// ============================================================================

export {
  /** Convert ArrayBuffer to base64 string */
  arrayBufferToBase64,
  /** Convert base64 string to ArrayBuffer */
  base64ToArrayBuffer,
  /** Compute SHA-256 hash of data */
  sha256Hash,
  /** Securely clear sensitive data from memory */
  secureClear,
} from './utils.ts';

// ============================================================================
// Streaming Utilities
// ============================================================================

export {
  /** Compute SHA-256 hash of a data stream (true streaming) */
  sha256HashStream,
  /** Compute SHA-256 hash with chunk accumulation (deprecated) */
  sha256HashStreamAccumulated,
} from './utils-streaming.ts';

export type {
  /** Options for streaming SHA-256 hash computation */
  SHA256StreamOptions,
} from './utils-streaming.ts';
