/**
 * Haven JS Runtime - Main Entry Point
 *
 * JSON-RPC 2.0 server that provides Synapse SDK functionality
 * to the Python CLI via stdio.
 */

// Install browser shim FIRST before any other imports
import { installBrowserShim } from './browser-shim.ts';
installBrowserShim();

import type {
  JSONRPCRequest,
  JSONRPCResponse,
  JSONRPCError,
  MethodRegistry,
  RuntimeStatus,
} from './types.ts';
import { ErrorCodes } from './types.ts';
import { createSynapseWrapper, type SynapseWrapper, InsufficientBalanceError } from './synapse-wrapper.ts';

// ============================================================================
// Runtime State
// ============================================================================

const VERSION = '1.0.0';
const startTime = Date.now();

let synapseWrapper: SynapseWrapper | null = null;
let isShuttingDown = false;

// ============================================================================
// JSON-RPC Helpers
// ============================================================================

function createResponse(id: string | number | null, result: unknown): JSONRPCResponse {
  return {
    jsonrpc: '2.0',
    result,
    id,
  };
}

function createErrorResponse(
  id: string | number | null,
  code: number,
  message: string,
  data?: unknown
): JSONRPCResponse {
  const error: JSONRPCError = { code, message };
  if (data !== undefined) {
    error.data = data;
  }
  return {
    jsonrpc: '2.0',
    error,
    id,
  };
}

/**
 * Check if an error indicates insufficient balance/funds.
 * This checks for various error patterns from Filecoin SDK and RPC.
 */
function isInsufficientBalanceError(error: unknown): boolean {
  if (!error) return false;
  
  // Check for InsufficientBalanceError type
  if (error instanceof InsufficientBalanceError) return true;
  if (error instanceof Error && error.name === 'InsufficientBalanceError') return true;
  
  const errorMessage = error instanceof Error ? error.message : String(error);
  const lowerMessage = errorMessage.toLowerCase();
  
  // Filecoin-specific patterns
  const patterns = [
    'actor balance less than needed',
    'insufficient balance',
    'insufficient funds',
    'syserrsenderstateinvalid',
    'retcode=2',
    'sender has insufficient funds',
    'not enough funds',
    'balance too low',
    'insufficient usdfc',
  ];
  
  return patterns.some(pattern => lowerMessage.includes(pattern));
}

/**
 * Extract balance information from error messages.
 * Returns null if unable to parse.
 */
function extractBalanceInfo(error: unknown): { available?: string; required?: string; address?: string } | null {
  if (!error) return null;
  
  // If it's an InsufficientBalanceError, extract structured data
  if (error instanceof InsufficientBalanceError) {
    return {
      available: error.available.toString(),
      required: error.required.toString(),
      address: error.address,
    };
  }
  
  // Try to parse from error message
  const errorMessage = error instanceof Error ? error.message : String(error);
  
  // Pattern: "Actor balance less than needed 0.002286105823689615 < 0.069999999883052615 RetCode=2"
  const balanceMatch = errorMessage.match(/(?:balance|needed)[\s:=]*([\d.]+)\s*[<:+-]?\s*(?:need|required|than)?[\s:=]*([\d.]+)/i);
  if (balanceMatch) {
    return {
      available: balanceMatch[1].trim(),
      required: balanceMatch[2].trim(),
    };
  }
  
  return null;
}

function sendResponse(response: JSONRPCResponse): void {
  console.log(JSON.stringify(response));
}

function sendNotification(method: string, params?: unknown): void {
  const notification: JSONRPCRequest = {
    jsonrpc: '2.0',
    method,
    params: params as Record<string, unknown>,
  };
  console.log(JSON.stringify(notification));
}

// ============================================================================
// Method Handlers
// ============================================================================

const methods: MethodRegistry = {
  // Lifecycle methods
  ping: async () => 'pong',

  shutdown: async () => {
    isShuttingDown = true;
    // Cleanup
    if (synapseWrapper) {
      await synapseWrapper.disconnect();
    }
    // Exit after a short delay to allow response to be sent
    setTimeout(() => Deno.exit(0), 100);
    return { status: 'shutting_down' };
  },

  getStatus: async (): Promise<RuntimeStatus> => {
    return {
      version: VERSION,
      uptimeSeconds: (Date.now() - startTime) / 1000,
      synapseConnected: synapseWrapper?.isConnected ?? false,
      pendingRequests: 0,
    };
  },

  // Synapse SDK methods
  'synapse.connect': async (params: unknown) => {
    synapseWrapper = createSynapseWrapper();
    return await synapseWrapper.connect(params as Record<string, unknown>);
  },

  'synapse.upload': async (params: unknown) => {
    if (!synapseWrapper?.isConnected) {
      throw new Error('Synapse not connected');
    }
    const uploadParams = params as Record<string, unknown>;
    
    // If progress notifications are requested, set up callback
    if (uploadParams.onProgress) {
      return await synapseWrapper.upload(uploadParams, (progress) => {
        sendNotification('synapse.uploadProgress', progress);
      });
    }
    
    return await synapseWrapper.upload(uploadParams);
  },

  'synapse.getStatus': async (params: unknown) => {
    if (!synapseWrapper?.isConnected) {
      throw new Error('Synapse not connected');
    }
    return await synapseWrapper.getStatus(params as Record<string, unknown>);
  },

  'synapse.getCid': async (params: unknown) => {
    if (!synapseWrapper?.isConnected) {
      throw new Error('Synapse not connected');
    }
    return await synapseWrapper.getCid(params as Record<string, unknown>);
  },

  'synapse.download': async (params: unknown) => {
    if (!synapseWrapper?.isConnected) {
      throw new Error('Synapse not connected');
    }
    const downloadParams = params as Record<string, unknown>;
    
    // If progress notifications are requested, set up callback
    if (downloadParams.onProgress) {
      return await synapseWrapper.download(downloadParams, (progress) => {
        sendNotification('synapse.downloadProgress', progress);
      });
    }
    
    return await synapseWrapper.download(downloadParams);
  },

  'synapse.verifyPieceRetrieval': async (params: unknown) => {
    if (!synapseWrapper?.isConnected) {
      throw new Error('Synapse not connected');
    }
    return await synapseWrapper.verifyPieceRetrieval(params as Record<string, unknown>);
  },

  'synapse.createCar': async (params: unknown) => {
    if (!synapseWrapper?.isConnected) {
      throw new Error('Synapse not connected');
    }
    return await synapseWrapper.createCar(params as Record<string, unknown>);
  },

  'synapse.validateFileSize': async (params: unknown) => {
    if (!synapseWrapper) {
      throw new Error('Synapse wrapper not initialized');
    }
    const { fileSize, encryptionEnabled } = params as Record<string, unknown>;
    return synapseWrapper.validateFileSize(
      fileSize as number,
      encryptionEnabled as boolean
    );
  },

  // Arkiv methods (placeholder - would integrate with blockchain)
  'arkiv.sync': async (params: unknown) => {
    // TODO: Implement Arkiv blockchain sync
    const syncParams = params as Record<string, unknown>;
    return {
      txHash: `0x${crypto.randomUUID().replace(/-/g, '')}`,
      recordId: crypto.randomUUID(),
      videoId: syncParams.videoId,
    };
  },

  'arkiv.verify': async (params: unknown) => {
    // TODO: Implement Arkiv verification
    const verifyParams = params as Record<string, unknown>;
    return {
      verified: true,
      recordId: verifyParams.recordId,
    };
  },

  'arkiv.getRecord': async (params: unknown) => {
    // TODO: Implement Arkiv record retrieval
    const getParams = params as Record<string, unknown>;
    return {
      recordId: getParams.recordId,
      found: false,
    };
  },
};

// ============================================================================
// Request Handler
// ============================================================================

async function handleRequest(request: JSONRPCRequest): Promise<void> {
  const { method, params, id } = request;

  // Notifications don't need responses
  const isNotification = id === undefined || id === null;

  try {
    const handler = methods[method];
    if (!handler) {
      if (!isNotification) {
        sendResponse(
          createErrorResponse(id!, ErrorCodes.METHOD_NOT_FOUND, `Method not found: ${method}`)
        );
      }
      return;
    }

    const result = await handler(params);

    if (!isNotification) {
      sendResponse(createResponse(id!, result));
    }
  } catch (error) {
    if (!isNotification) {
      const message = error instanceof Error ? error.message : String(error);
      
      // Check for insufficient balance error - use dedicated error code
      if (isInsufficientBalanceError(error)) {
        const balanceInfo = extractBalanceInfo(error);
        sendResponse(createErrorResponse(
          id!,
          ErrorCodes.INSUFFICIENT_BALANCE,
          message,
          {
            errorType: 'InsufficientBalanceError',
            ...balanceInfo,
          }
        ));
      } else {
        sendResponse(createErrorResponse(id!, ErrorCodes.INTERNAL_ERROR, message));
      }
    }
  }
}

// ============================================================================
// Main Loop
// ============================================================================

async function main(): Promise<void> {
  // Signal ready
  sendNotification('ready', { version: VERSION });

  // Read from stdin line by line
  const decoder = new TextDecoder();
  const reader = Deno.stdin.readable.getReader();
  let buffer = '';

  try {
    while (!isShuttingDown) {
      const { value, done } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // Process complete lines
      let newlineIndex: number;
      while ((newlineIndex = buffer.indexOf('\n')) !== -1) {
        const line = buffer.slice(0, newlineIndex).trim();
        buffer = buffer.slice(newlineIndex + 1);

        if (!line) continue;

        try {
          const request = JSON.parse(line) as JSONRPCRequest;
          await handleRequest(request);
        } catch (parseError) {
          // Send parse error response
          sendResponse(
            createErrorResponse(
              null,
              ErrorCodes.PARSE_ERROR,
              'Parse error',
              parseError instanceof Error ? parseError.message : String(parseError)
            )
          );
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

// ============================================================================
// Entry Point
// ============================================================================

// Handle uncaught errors
globalThis.addEventListener('error', (event) => {
  console.error('[haven-js] Uncaught error:', event.error);
});

globalThis.addEventListener('unhandledrejection', (event) => {
  console.error('[haven-js] Unhandled rejection:', event.reason);
});

// Start the main loop
main().catch((error) => {
  console.error('[haven-js] Fatal error:', error);
  Deno.exit(1);
});
