/**
 * Synapse SDK Wrapper
 *
 * Provides a simplified interface to the Synapse SDK for
 * Filecoin storage operations using the filecoin-pin package.
 */

import type {
  SynapseConnectParams,
  SynapseConnectResult,
  SynapseUploadParams,
  SynapseUploadResult,
  SynapseUploadProgress,
  SynapseStatusParams,
  SynapseStatusResult,
  SynapseDownloadParams,
  SynapseDownloadResult,
  SynapseDownloadProgress,
  SynapseCreateCarParams,
  SynapseCreateCarResult,
} from './types.ts';

// Import filecoin-pin modules
import {
  createUnixfsCarBuilder,
  type CarBuildResult,
  type CreateCarOptions,
} from 'filecoin-pin/core/unixfs';
import { initializeSynapse as initSynapse } from 'filecoin-pin/core/synapse';
import {
  executeUpload,
  checkUploadReadiness,
  type UploadExecutionResult,
} from 'filecoin-pin/core/upload';
import type { Synapse } from '@filoz/synapse-sdk';
import { CID } from 'multiformats/cid';

import { carFileByteLength, openCarReadableStream } from './car_stream.ts';

// Import pino Logger type
import type { Logger } from 'npm:pino@^10.0.0';
import { ethers } from 'npm:ethers@^6.0.0';

// ============================================================================
// Custom Error Types
// ============================================================================

/**
 * Error thrown when the actor/wallet has insufficient balance for the transaction.
 * This is a non-retryable error that should be immediately propagated to the user.
 */
export class InsufficientBalanceError extends Error {
  readonly available: bigint;
  readonly required: bigint;
  readonly address: string;
  readonly token: string;

  constructor(
    message: string,
    available: bigint,
    required: bigint,
    address: string,
    token: string = 'FIL'
  ) {
    super(message);
    this.name = 'InsufficientBalanceError';
    this.available = available;
    this.required = required;
    this.address = address;
    this.token = token;
  }

  /**
   * Get a human-readable formatted version of the error details.
   */
  getDetails(): string {
    const availableFormatted = ethers.formatEther(this.available);
    const requiredFormatted = ethers.formatEther(this.required);
    const shortfallFormatted = ethers.formatEther(this.required - this.available);
    return (
      `Insufficient funds for transaction. ` +
      `Required: ${requiredFormatted} ${this.token}, ` +
      `Available: ${availableFormatted} ${this.token}. ` +
      `Shortfall: ${shortfallFormatted} ${this.token}. ` +
      `Wallet: ${this.address}. ` +
      `Please fund your wallet to proceed.`
    );
  }
}

/**
 * Check if an error indicates insufficient balance/funds.
 * This checks for various error patterns from Filecoin SDK and RPC.
 */
function isInsufficientBalanceError(error: unknown): boolean {
  if (!error) return false;
  
  const errorMessage = error instanceof Error ? error.message : String(error);
  const lowerMessage = errorMessage.toLowerCase();
  
  // Check for InsufficientBalanceError type
  if (error instanceof InsufficientBalanceError) return true;
  
  // Filecoin-specific patterns
  const patterns = [
    'actor balance less than needed',
    'insufficient balance',
    'insufficient funds',
    'syserrsenderstateinvalid',
    'retcode=2',
    'retcode=\u0002',
    'sender has insufficient funds',
    'not enough funds',
    'balance too low',
  ];
  
  return patterns.some(pattern => lowerMessage.includes(pattern));
}

/**
 * Extract balance information from Filecoin error messages.
 * Returns null if unable to parse.
 */
function extractBalanceFromError(error: unknown): { available: bigint; required: bigint } | null {
  if (!error) return null;
  
  const errorMessage = error instanceof Error ? error.message : String(error);
  
  // Pattern: "Actor balance less than needed 0.002286105823689615 < 0.069999999883052615 RetCode=2"
  // or similar variations
  const balanceMatch = errorMessage.match(/(?:balance|needed)[\s:=]*([\d.]+)\s*[<:+-]?\s*(?:need|required|than)?[\s:=]*([\d.]+)/i);
  if (balanceMatch) {
    try {
      // Try to parse as FIL amounts (may need conversion from attoFIL)
      const val1 = balanceMatch[1].trim();
      const val2 = balanceMatch[2].trim();
      
      // Convert to BigInt (attoFIL = 10^-18 FIL)
      const parseFil = (val: string): bigint => {
        if (val.includes('.')) {
          // It's a decimal FIL amount
          return ethers.parseEther(val);
        }
        // It might already be in attoFIL or wei
        return BigInt(val.split('.')[0]);
      };
      
      const available = parseFil(val1);
      const required = parseFil(val2);
      
      return { available, required };
    } catch {
      // Parsing failed, return null
    }
  }
  
  return null;
}

// Deno type declaration
declare const Deno: {
  env: {
    get(key: string): string | undefined;
  };
  readFile(path: string): Promise<Uint8Array>;
  writeFile(path: string, data: Uint8Array): Promise<void>;
  stat(path: string): Promise<{ size: number; isFile: boolean }>;
  stdin: {
    readable: ReadableStream<Uint8Array>;
  };
  exit(code?: number): never;
};

// Filecoin upload size limits
const MAX_UPLOAD_SIZE = 1_065_353_216; // ~1 GiB (hard limit from Synapse SDK)
const MIN_UPLOAD_SIZE = 127; // Minimum size for PieceCIDv2 calculation
const ENCRYPTION_OVERHEAD_FACTOR = 1.35; // Base64 encoding overhead (~35%)
const CAR_OVERHEAD_FACTOR = 1.01; // CAR file overhead (~1%)
const SAFETY_MARGIN = 1.05; // Additional 5% safety margin

/**
 * Progress callback type for upload operations.
 */
export type ProgressCallback = (progress: SynapseUploadProgress) => void;
export type DownloadProgressCallback = (progress: SynapseDownloadProgress) => void;

/**
 * Size validation result type
 */
export type SizeValidationReason = 'TOO_SMALL' | 'TOO_LARGE' | 'ENCRYPTION_WOULD_EXCEED' | 'CAR_WOULD_EXCEED' | null;

export interface FilecoinSizeValidationResult {
  valid: boolean;
  reason: SizeValidationReason;
  originalSize: number;
  projectedSize: number;
  maxAllowed: number;
  encryptionEnabled: boolean;
  errorMessage?: string;
  userMessage?: string;
}

/**
 * Synapse SDK wrapper interface.
 */
export interface SynapseWrapper {
  readonly isConnected: boolean;
  connect(params: Record<string, unknown>): Promise<SynapseConnectResult>;
  disconnect(): Promise<void>;
  upload(
    params: Record<string, unknown>,
    onProgress?: ProgressCallback
  ): Promise<SynapseUploadResult>;
  getStatus(params: Record<string, unknown>): Promise<SynapseStatusResult>;
  getCid(params: Record<string, unknown>): Promise<{ cid: string }>;
  download(
    params: Record<string, unknown>,
    onProgress?: DownloadProgressCallback
  ): Promise<SynapseDownloadResult>;
  createCar(params: Record<string, unknown>): Promise<SynapseCreateCarResult>;
  validateFileSize(
    fileSize: number,
    encryptionEnabled?: boolean
  ): FilecoinSizeValidationResult;
}

/**
 * Create a new Synapse SDK wrapper instance.
 */
export function createSynapseWrapper(): SynapseWrapper {
  return new SynapseWrapperImpl();
}

// Logger factory for filecoin-pin
function createLogger(): Logger {
  // Check for debug mode via environment variables
  const isDebug = Deno.env.get('DEBUG') === '1' || 
                  Deno.env.get('LOG_LEVEL')?.toLowerCase() === 'debug';
  
  return {
    level: isDebug ? 'debug' : 'info' as const,
    info: (msg: unknown, ...args: unknown[]) => {
      console.error('[Synapse]', msg, ...args);
    },
    warn: (msg: unknown, ...args: unknown[]) => {
      console.warn('[Synapse]', msg, ...args);
    },
    error: (msg: unknown, ...args: unknown[]) => {
      console.error('[Synapse]', msg, ...args);
    },
    debug: (msg: unknown, ...args: unknown[]) => {
      if (isDebug) {
        console.debug('[Synapse]', msg, ...args);
      }
    },
    fatal: (msg: unknown, ...args: unknown[]) => {
      console.error('[Synapse] FATAL:', msg, ...args);
    },
    trace: (msg: unknown, ...args: unknown[]) => {
      if (isDebug) {
        console.trace('[Synapse]', msg, ...args);
      }
    },
    silent: (msg: unknown, ...args: unknown[]) => {
      // Silent - no output
    },
    msgPrefix: '[Synapse]',
  } as unknown as Logger;
}

/**
 * Synapse SDK wrapper implementation using filecoin-pin.
 */
class SynapseWrapperImpl implements SynapseWrapper {
  private _isConnected = false;
  private _endpoint = '';
  private _privateKey = '';
  private _rpcUrl = '';
  private _synapse: unknown = null;
  private _logger = createLogger();

  get isConnected(): boolean {
    return this._isConnected;
  }

  async connect(params: Record<string, unknown>): Promise<SynapseConnectResult> {
    const connectParams = params as unknown as SynapseConnectParams;
    
    // Support network mode for unified mainnet/testnet configuration
    const networkMode = (params.networkMode as string) ?? 'testnet';
    
    // Default RPC URLs based on network mode (WebSocket endpoints for Synapse SDK)
    // These are fallback defaults - HAVEN_FILECOIN_RPC_URL env var takes precedence
    const defaultRpcUrl = networkMode === 'mainnet'
      ? 'wss://node.glif.io/apiw/gw/lotus/rpc/v1'  // Filecoin mainnet WebSocket
      : 'wss://api.calibration.node.glif.io/rpc/v1';  // Filecoin calibration testnet WebSocket
    
    // params.endpoint and params.rpcUrl take precedence over networkMode defaults
    const endpoint = connectParams.endpoint ?? defaultRpcUrl;
    
    // Authentication: HAVEN_PRIVATE_KEY required for blockchain-based auth
    const privateKey = (params.privateKey as string) ?? Deno.env.get('HAVEN_PRIVATE_KEY') ?? '';
    // HAVEN_FILECOIN_RPC_URL is set by the Python bridge based on network configuration
    const rpcUrl = (params.rpcUrl as string) ?? Deno.env.get('HAVEN_FILECOIN_RPC_URL') ?? Deno.env.get('FILECOIN_RPC_URL') ?? defaultRpcUrl;
    
    if (params.debug) {
      console.error(`[synapse-wrapper] Network mode: ${networkMode}, using RPC: ${rpcUrl}`);
    }

    // Validate that we have private key
    if (!privateKey) {
      throw new Error('HAVEN_PRIVATE_KEY environment variable is required. Set it with: export HAVEN_PRIVATE_KEY=0x...');
    }

    this._endpoint = endpoint;
    this._privateKey = privateKey;
    this._rpcUrl = rpcUrl;

    // Test connection by pinging the endpoint
    try {
      const response = await fetch(`${endpoint}/health`, {
        method: 'GET',
      });
      
      if (!response.ok && response.status !== 404) {
        throw new Error(`Synapse endpoint health check failed: ${response.status} ${response.statusText}`);
      }
    } catch (error) {
      // If health endpoint doesn't exist (404), that's okay
      // If it's a network error, we should warn but not fail (allows offline testing)
      if (error instanceof Error && !error.message.includes('404')) {
        this._logger.warn(`Could not reach Synapse endpoint, continuing anyway: ${error.message}`);
      }
    }

    this._isConnected = true;

    return {
      connected: true,
      endpoint,
    };
  }

  async disconnect(): Promise<void> {
    this._isConnected = false;
    this._endpoint = '';
    
    // Synapse SDK has no explicit teardown in current versions; drop our reference.
    this._synapse = null;
  }

  /**
   * Calculate the effective maximum file size based on encryption setting
   */
  private getMaxFileSize(encryptionEnabled: boolean = false): number {
    const totalOverhead = encryptionEnabled
      ? ENCRYPTION_OVERHEAD_FACTOR * CAR_OVERHEAD_FACTOR * SAFETY_MARGIN
      : CAR_OVERHEAD_FACTOR * SAFETY_MARGIN;
    
    return Math.floor(MAX_UPLOAD_SIZE / totalOverhead);
  }

  /**
   * Calculate projected upload size after encryption and CAR creation
   */
  private calculateProjectedSize(
    fileSize: number,
    encryptionEnabled: boolean = false
  ): number {
    let projectedSize = fileSize;
    
    // Account for encryption overhead
    if (encryptionEnabled) {
      projectedSize = Math.ceil(fileSize * ENCRYPTION_OVERHEAD_FACTOR);
    }
    
    // Account for CAR overhead
    projectedSize = Math.ceil(projectedSize * CAR_OVERHEAD_FACTOR);
    
    // Account for safety margin
    projectedSize = Math.ceil(projectedSize * SAFETY_MARGIN);
    
    return projectedSize;
  }

  /**
   * Validate file size for Filecoin upload
   */
  validateFileSize(
    fileSize: number,
    encryptionEnabled: boolean = false
  ): FilecoinSizeValidationResult {
    // Check minimum size (for PieceCIDv2 calculation)
    if (fileSize < MIN_UPLOAD_SIZE) {
      const errorMessage = `File size (${fileSize} bytes) is below minimum required size (${MIN_UPLOAD_SIZE} bytes) for Filecoin upload`;
      return {
        valid: false,
        reason: 'TOO_SMALL',
        originalSize: fileSize,
        projectedSize: fileSize,
        maxAllowed: MIN_UPLOAD_SIZE,
        encryptionEnabled,
        errorMessage,
        userMessage: `File is too small. Minimum size is ${MIN_UPLOAD_SIZE} bytes.`,
      };
    }

    // Calculate projected size after encryption and CAR creation
    const projectedSize = this.calculateProjectedSize(fileSize, encryptionEnabled);
    
    // Check against maximum upload size
    if (projectedSize > MAX_UPLOAD_SIZE) {
      const maxOriginalSize = this.getMaxFileSize(encryptionEnabled);
      
      let errorMessage: string;
      let userMessage: string;
      let reason: SizeValidationReason;
      
      if (encryptionEnabled) {
        errorMessage = `File size (${fileSize} bytes) would exceed ${MAX_UPLOAD_SIZE} bytes limit after encryption. ` +
          `Projected size: ${projectedSize} bytes. ` +
          `Maximum allowed with encryption: ${maxOriginalSize} bytes.`;
        userMessage = `File would exceed upload limit after encryption. Try disabling encryption or compressing the file.`;
        reason = 'ENCRYPTION_WOULD_EXCEED';
      } else {
        errorMessage = `File size (${fileSize} bytes) exceeds maximum upload size of ${maxOriginalSize} bytes. ` +
          `Projected CAR size: ${projectedSize} bytes.`;
        userMessage = `File exceeds maximum upload size. Please compress or split the file.`;
        reason = 'TOO_LARGE';
      }
      
      return {
        valid: false,
        reason,
        originalSize: fileSize,
        projectedSize,
        maxAllowed: maxOriginalSize,
        encryptionEnabled,
        errorMessage,
        userMessage,
      };
    }

    // Validation passed
    return {
      valid: true,
      reason: null,
      originalSize: fileSize,
      projectedSize,
      maxAllowed: this.getMaxFileSize(encryptionEnabled),
      encryptionEnabled,
    };
  }

  async upload(
    params: Record<string, unknown>,
    onProgress?: ProgressCallback
  ): Promise<SynapseUploadResult> {
    if (!this._isConnected) {
      throw new Error('Synapse not connected');
    }

    const uploadParams = params as unknown as SynapseUploadParams & { 
      privateKey?: string;
      rpcUrl?: string;
      encryptionEnabled?: boolean;
    };
    const { filePath, metadata } = uploadParams;

    if (!filePath) {
      throw new Error('Missing required parameter: filePath');
    }

    // Check file exists and get size
    let fileStat;
    try {
      fileStat = await Deno.stat(filePath);
    } catch (error) {
      throw new Error(`Cannot read file: ${filePath} - ${error instanceof Error ? error.message : String(error)}`);
    }

    const fileSize = fileStat.size;

    // Validate file size
    const sizeValidation = this.validateFileSize(fileSize, uploadParams.encryptionEnabled ?? false);
    if (!sizeValidation.valid) {
      throw new Error(sizeValidation.errorMessage || 'File size validation failed');
    }

    // Report initial progress
    onProgress?.({
      bytesUploaded: 0,
      totalBytes: fileSize,
      percentage: 0,
    });

    // Initialize Synapse SDK
    const privateKey = uploadParams.privateKey ?? this._privateKey ?? Deno.env.get('HAVEN_PRIVATE_KEY');
    // HAVEN_FILECOIN_RPC_URL is set by the Python bridge based on network configuration
    const rpcUrl = uploadParams.rpcUrl ?? this._rpcUrl ?? Deno.env.get('HAVEN_FILECOIN_RPC_URL') ?? Deno.env.get('FILECOIN_RPC_URL') ?? 'wss://api.calibration.node.glif.io/rpc/v1';

    if (!privateKey) {
      throw new Error('Private key is required for Filecoin upload. Set HAVEN_PRIVATE_KEY environment variable or provide in params.');
    }

    // Normalize private key (add 0x prefix if missing)
    const normalizedPrivateKey = privateKey.startsWith('0x') ? privateKey : `0x${privateKey}`;

    try {
      // Initialize Synapse
      onProgress?.({
        bytesUploaded: 0,
        totalBytes: fileSize,
        percentage: 5,
      });

      const synapse = await initSynapse(
        {
          privateKey: normalizedPrivateKey as `0x${string}`,
          rpcUrl,
        },
        this._logger,
      );
      this._synapse = synapse;

      onProgress?.({
        bytesUploaded: 0,
        totalBytes: fileSize,
        percentage: 10,
      });

      // Create CAR file
      const unixfsCarBuilder = createUnixfsCarBuilder();
      const createCarOptions: CreateCarOptions = {
        logger: this._logger,
        bare: true, // Create bare file CID without directory wrapper
      };

      const carBuildResult: CarBuildResult = await unixfsCarBuilder.buildCar(
        filePath,
        createCarOptions
      );

      onProgress?.({
        bytesUploaded: 0,
        totalBytes: fileSize,
        percentage: 20,
      });

      // CAR byte length for readiness / payment (stat before streaming — no full-file buffer)
      console.error('[synapse-wrapper] CAR path for upload stream:', carBuildResult.carPath);
      const carFileSize = await carFileByteLength(carBuildResult.carPath);
      console.error('[synapse-wrapper] CAR file size (stat):', carFileSize);

      onProgress?.({
        bytesUploaded: 0,
        totalBytes: carFileSize,
        percentage: 25,
      });

      // Check upload readiness (payment validation)
      console.error('[synapse-wrapper] Starting checkUploadReadiness...');
      console.error('[synapse-wrapper] RPC URL:', rpcUrl);
      console.error('[synapse-wrapper] File size:', carFileSize);
      
      // Wrap checkUploadReadiness with timeout to prevent indefinite hanging on RPC calls
      const readiness = await this._withTimeout(
        'checkUploadReadiness',
        checkUploadReadiness({
          synapse,
          fileSize: carFileSize,
          autoConfigureAllowances: true,
        }),
        600000 // 600 second (10 minute) timeout for readiness check
      );

      console.error('[synapse-wrapper] Readiness check complete, status:', readiness.status);
      
      if (readiness.status === 'blocked') {
        const errorMessage =
          readiness.validation?.errorMessage ||
          (readiness.suggestions && readiness.suggestions.length > 0 
            ? readiness.suggestions.join('. ') 
            : 'Upload blocked: Payment setup incomplete');
        throw new Error(errorMessage);
      }

      // filecoin-pin 0.21+: executeUpload takes Synapse directly (uploadToSynapse → synapse.storage.upload).
      onProgress?.({
        bytesUploaded: 0,
        totalBytes: carFileSize,
        percentage: 35,
      });

      const rootCidString = carBuildResult.rootCid.toString();
      const rootCid = CID.parse(rootCidString);

      const uploadResult = await this._executeUploadWithTimeout(
        synapse,
        carBuildResult.carPath,
        carFileSize,
        rootCid,
        filePath,
        onProgress
      );

      // Clean up CAR file
      try {
        await unixfsCarBuilder.cleanup(carBuildResult.carPath, this._logger);
      } catch {
        // Ignore cleanup errors
      }

      onProgress?.({
        bytesUploaded: carFileSize,
        totalBytes: carFileSize,
        percentage: 100,
      });

      return {
        cid: rootCidString,
        size: carFileSize,
        uploadedAt: new Date().toISOString(),
        dealId: uploadResult.pieceCid,
      };
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      throw new Error(`Upload failed: ${errorMessage}`);
    }
  }

  /**
   * Wrap a promise with a timeout to prevent indefinite hanging on network calls.
   * 
   * This is critical for RPC calls that may hang if the Filecoin RPC endpoint
   * is unreachable, rate-limited, or down.
   * 
   * @param operationName - Name of the operation for error messages
   * @param promise - The promise to wrap
   * @param timeoutMs - Timeout in milliseconds
   * @returns The result of the promise
   * @throws Error if the timeout is exceeded
   */
  private async _withTimeout<T>(
    operationName: string,
    promise: Promise<T>,
    timeoutMs: number
  ): Promise<T> {
    let timeoutId: ReturnType<typeof setTimeout>;
    
    const timeoutPromise = new Promise<never>((_, reject) => {
      timeoutId = setTimeout(() => {
        reject(new Error(
          `${operationName} timed out after ${timeoutMs}ms. ` +
          `This may be due to Filecoin RPC connectivity issues. ` +
          `Check your network connection and RPC endpoint status.`
        ));
      }, timeoutMs);
    });
    
    try {
      const result = await Promise.race([promise, timeoutPromise]);
      clearTimeout(timeoutId!);
      return result;
    } catch (error) {
      clearTimeout(timeoutId!);
      throw error;
    }
  }

  /**
   * Execute upload with timeout and retry logic.
   * 
   * The filecoin-pin SDK can timeout during upload (especially during addPieces)
   * due to network congestion or RPC issues. This wrapper adds:
   * - Configurable timeout per attempt (default 180s)
   * - Automatic retry on timeout errors (max 3 attempts)
   * - Better error messages for debugging
   * - Fail-fast on insufficient balance errors (non-retryable)
   */
  private async _executeUploadWithTimeout(
    synapse: Synapse,
    carPath: string,
    carByteLength: number,
    rootCid: CID,
    filePath: string,
    onProgress?: ProgressCallback,
    maxRetries: number = 3,
    timeoutMs: number = 1800000 // 30 minutes per attempt
  ): Promise<UploadExecutionResult> {
    let lastError: Error | null = null;
    
    for (let attempt = 1; attempt <= maxRetries; attempt++) {
      const { handle, stream } = await openCarReadableStream(carPath);
      let timeoutId: ReturnType<typeof setTimeout> | undefined;
      try {
        this._logger.info(`Upload attempt ${attempt}/${maxRetries} for ${filePath}`);
        
        // Create a promise that rejects after the timeout
        const timeoutPromise = new Promise<never>((_, reject) => {
          timeoutId = setTimeout(() => {
            reject(new Error(`Upload timeout after ${timeoutMs}ms (attempt ${attempt})`));
          }, timeoutMs);
        });
        // Clean up timeout if timeoutPromise is rejected (but not by the timer)
        timeoutPromise.catch(() => {
          if (timeoutId !== undefined) clearTimeout(timeoutId);
        });
        
        // Execute upload with race against timeout
        const uploadPromise = executeUpload(synapse, stream, rootCid, {
          logger: this._logger,
          contextId: filePath.split('/').pop() || 'upload',
          onProgress: (event: { type: string }) => {
            // filecoin-pin 0.21+ UploadProgressEvents (legacy on* names kept for safety)
            if (event.type === 'stored' || event.type === 'copyComplete' || event.type === 'onUploadComplete') {
              onProgress?.({
                bytesUploaded: carByteLength,
                totalBytes: carByteLength,
                percentage: 80,
              });
            } else if (event.type === 'piecesAdded' || event.type === 'onPieceAdded') {
              onProgress?.({
                bytesUploaded: carByteLength,
                totalBytes: carByteLength,
                percentage: 90,
              });
            } else if (event.type === 'piecesConfirmed' || event.type === 'onPieceConfirmed') {
              onProgress?.({
                bytesUploaded: carByteLength,
                totalBytes: carByteLength,
                percentage: 95,
              });
            }
          },
          ipniValidation: {
            enabled: true,
          },
        });
        
        // Race between upload and timeout
        const result = await Promise.race([uploadPromise, timeoutPromise]);
        if (timeoutId !== undefined) clearTimeout(timeoutId);
        timeoutId = undefined;
        this._logger.info(`Upload succeeded on attempt ${attempt}`);
        return result;
        
      } catch (error) {
        const errorMessage = error instanceof Error ? error.message : String(error);
        lastError = error instanceof Error ? error : new Error(errorMessage);
        
        // CRITICAL: Check for insufficient balance error - DO NOT RETRY
        // These errors indicate the wallet lacks funds and retrying wastes time
        if (isInsufficientBalanceError(error)) {
          this._logger.error(`Upload failed with insufficient balance: ${errorMessage}`);
          // Extract and log balance details if available
          const balanceInfo = extractBalanceFromError(error);
          if (balanceInfo) {
            this._logger.error(
              `Balance details - Available: ${ethers.formatEther(balanceInfo.available)} FIL, ` +
              `Required: ${ethers.formatEther(balanceInfo.required)} FIL`
            );
          }
          // Re-throw immediately without retry
          throw error;
        }
        
        // Check if it's a timeout error
        const isTimeoutError = 
          errorMessage.includes('timeout') ||
          errorMessage.includes('TIMEOUT') ||
          errorMessage.includes('StorageContext addPieces failed');
        
        if (isTimeoutError) {
          this._logger.warn(`Upload attempt ${attempt} timed out: ${errorMessage}`);
          
          if (attempt < maxRetries) {
            const delayMs = attempt * 5000; // 5s, 10s, 15s backoff
            this._logger.info(`Retrying upload in ${delayMs}ms...`);
            await new Promise(resolve => setTimeout(resolve, delayMs));
            continue;
          }
        } else {
          // Non-timeout error, don't retry
          this._logger.error(`Upload failed with non-retryable error: ${errorMessage}`);
          throw error;
        }
      } finally {
        try {
          handle.close();
        } catch {
          // Ignore close errors (e.g. already closed)
        }
        if (timeoutId !== undefined) clearTimeout(timeoutId);
      }
    }
    
    // All retries exhausted
    throw new Error(
      `Upload failed after ${maxRetries} attempts. ` +
      `Last error: ${lastError?.message}. ` +
      `This may be due to network congestion or RPC issues. ` +
      `Consider increasing timeout or checking Filecoin network status.`
    );
  }

  async getStatus(params: Record<string, unknown>): Promise<SynapseStatusResult> {
    if (!this._isConnected) {
      throw new Error('Synapse not connected');
    }

    const statusParams = params as unknown as SynapseStatusParams;
    const { cid } = statusParams;

    if (!cid) {
      throw new Error('Missing required parameter: cid');
    }

    // For now, return a simulated status since we need to implement
    // the actual status check using filecoin-pin's APIs
    // In production, this would query the Filecoin chain
    return {
      cid,
      status: 'active',
      deals: [
        {
          dealId: `deal_${cid.slice(-12)}`,
          provider: 'f01234',
          status: 'active',
          startEpoch: 1000000,
          endEpoch: 2000000,
        },
      ],
    };
  }

  async getCid(params: Record<string, unknown>): Promise<{ cid: string }> {
    if (!this._isConnected) {
      throw new Error('Synapse not connected');
    }

    const { filePath } = params as { filePath?: string };

    if (!filePath) {
      throw new Error('Missing required parameter: filePath');
    }

    // Check file exists
    try {
      await Deno.stat(filePath);
    } catch (error) {
      throw new Error(`Cannot read file: ${filePath} - ${error instanceof Error ? error.message : String(error)}`);
    }

    // Create CAR file to get the CID
    const unixfsCarBuilder = createUnixfsCarBuilder();
    const createCarOptions: CreateCarOptions = {
      logger: this._logger,
      bare: true,
    };

    try {
      const carBuildResult: CarBuildResult = await unixfsCarBuilder.buildCar(
        filePath,
        createCarOptions
      );

      // Clean up the CAR file immediately (we just wanted the CID)
      try {
        await unixfsCarBuilder.cleanup(carBuildResult.carPath, this._logger);
      } catch {
        // Ignore cleanup errors
      }

      return { cid: carBuildResult.rootCid.toString() };
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      throw new Error(`Failed to calculate CID: ${errorMessage}`);
    }
  }

  async download(
    params: Record<string, unknown>,
    onProgress?: DownloadProgressCallback
  ): Promise<SynapseDownloadResult> {
    if (!this._isConnected) {
      throw new Error('Synapse not connected');
    }

    const downloadParams = params as unknown as SynapseDownloadParams;
    let { cid, outputPath } = downloadParams;

    if (!cid) {
      throw new Error('Missing required parameter: cid');
    }
    if (!outputPath) {
      throw new Error('Missing required parameter: outputPath');
    }

    // Strip whitespace from CID (handle potential newline issues)
    cid = cid.trim();

    // Validate CID format
    if (!isValidCid(cid)) {
      throw new Error(`Invalid CID format: ${cid}`);
    }

    // Download from IPFS gateway or Filecoin retrieval
    // For now, we use a public IPFS gateway
    const gateways = [
      `https://ipfs.io/ipfs/${cid}`,
      `https://gateway.ipfs.io/ipfs/${cid}`,
      `https://dweb.link/ipfs/${cid}`,
    ];

    let lastError: Error | null = null;

    for (const gateway of gateways) {
      try {
        onProgress?.({
          bytesDownloaded: 0,
          totalBytes: 0,
          percentage: 0,
        });

        const response = await fetch(gateway);
        
        if (!response.ok) {
          throw new Error(`Gateway returned ${response.status}: ${response.statusText}`);
        }

        const contentLength = response.headers.get('content-length');
        const totalBytes = contentLength ? parseInt(contentLength, 10) : 0;

        if (!response.body) {
          throw new Error('Response has no body');
        }

        // Read the stream
        const reader = response.body.getReader();
        const chunks: Uint8Array[] = [];
        let bytesDownloaded = 0;

        while (true) {
          const { done, value } = await reader.read();
          
          if (done) {
            break;
          }

          chunks.push(value);
          bytesDownloaded += value.length;

          if (totalBytes > 0) {
            onProgress?.({
              bytesDownloaded,
              totalBytes,
              percentage: Math.round((bytesDownloaded / totalBytes) * 100),
            });
          }
        }

        // Combine chunks
        const totalLength = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
        const data = new Uint8Array(totalLength);
        let offset = 0;
        for (const chunk of chunks) {
          data.set(chunk, offset);
          offset += chunk.length;
        }

        // Write to file
        await Deno.writeFile(outputPath, data);

        onProgress?.({
          bytesDownloaded: data.length,
          totalBytes: data.length,
          percentage: 100,
        });

        return {
          success: true,
          size: data.length,
          cid,
          outputPath,
        };
      } catch (error) {
        lastError = error instanceof Error ? error : new Error(String(error));
        this._logger.warn(`Gateway failed: ${gateway}, error: ${lastError.message}`);
        continue;
      }
    }

    throw new Error(`Failed to download from all gateways. Last error: ${lastError?.message}`);
  }

  async createCar(params: Record<string, unknown>): Promise<SynapseCreateCarResult> {
    const carParams = params as unknown as SynapseCreateCarParams;
    const { filePath, outputPath } = carParams;

    if (!filePath) {
      throw new Error('Missing required parameter: filePath');
    }

    // Check file exists
    let fileStat;
    try {
      fileStat = await Deno.stat(filePath);
    } catch (error) {
      throw new Error(`Cannot read file: ${filePath} - ${error instanceof Error ? error.message : String(error)}`);
    }

    // Create CAR file
    const unixfsCarBuilder = createUnixfsCarBuilder();
    const createCarOptions: CreateCarOptions = {
      logger: this._logger,
      bare: true,
    };

    try {
      const carBuildResult: CarBuildResult = await unixfsCarBuilder.buildCar(
        filePath,
        createCarOptions
      );

      // If outputPath is specified, copy the CAR file there
      if (outputPath && outputPath !== carBuildResult.carPath) {
        await Deno.copyFile(carBuildResult.carPath, outputPath);
        const carSize = await carFileByteLength(outputPath);
        
        // Clean up the original CAR file
        try {
          await unixfsCarBuilder.cleanup(carBuildResult.carPath, this._logger);
        } catch {
          // Ignore cleanup errors
        }

        return {
          carPath: outputPath,
          rootCid: carBuildResult.rootCid.toString(),
          size: carSize,
        };
      }

      return {
        carPath: carBuildResult.carPath,
        rootCid: carBuildResult.rootCid.toString(),
        size: fileStat.size,
      };
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      throw new Error(`Failed to create CAR file: ${errorMessage}`);
    }
  }
}

/**
 * Calculate the estimated cost for storing a file on Filecoin.
 */
export function estimateStorageCost(
  fileSizeBytes: number,
  durationDays: number = 365
): { estimatedCost: string; currency: string } {
  // This is a placeholder calculation
  // Real implementation would query current Filecoin storage prices
  const gbSize = fileSizeBytes / (1024 * 1024 * 1024);
  const costPerGbPerYear = 0.0001; // Placeholder price in FIL
  const cost = gbSize * costPerGbPerYear * (durationDays / 365);

  return {
    estimatedCost: cost.toFixed(8),
    currency: 'FIL',
  };
}

/**
 * Validate a CID format.
 */
export function isValidCid(cid: string): boolean {
  // Basic CID validation
  // CIDv0 starts with Qm (base58-encoded, 46 chars total)
  // CIDv1 starts with 'baf' (base32-encoded, typically 59+ chars)
  // - bafy = dag-pb codec (most common for files/directories)
  // - bafk = raw codec (for raw binary data)
  // - bafq = eth-account-snapshot
  // - etc.
  return /^(Qm[1-9A-HJ-NP-Za-km-z]{44}|baf[a-z2-7]{55,})$/.test(cid);
}

/**
 * Format bytes to human-readable string
 */
export function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}
