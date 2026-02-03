/**
 * Haven JS Runtime - Main Entry Point
 *
 * JSON-RPC 2.0 server that provides browser SDK functionality
 * (Lit Protocol, Synapse) to the Python CLI via stdio.
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
import { createLitWrapper, type LitWrapper } from './lit-wrapper.ts';
import { createSynapseWrapper, type SynapseWrapper } from './synapse-wrapper.ts';

// ============================================================================
// Runtime State
// ============================================================================

const VERSION = '1.0.0';
const startTime = Date.now();

let litWrapper: LitWrapper | null = null;
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
    if (litWrapper) {
      await litWrapper.disconnect();
    }
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
      litConnected: litWrapper?.isConnected ?? false,
      synapseConnected: synapseWrapper?.isConnected ?? false,
      pendingRequests: 0,
    };
  },

  // Lit Protocol methods
  'lit.connect': async (params: unknown) => {
    litWrapper = createLitWrapper();
    return await litWrapper.connect(params as Record<string, unknown>);
  },

  'lit.encrypt': async (params: unknown) => {
    if (!litWrapper?.isConnected) {
      throw new Error('Lit Protocol not connected');
    }
    return await litWrapper.encrypt(params as Record<string, unknown>);
  },

  'lit.decrypt': async (params: unknown) => {
    if (!litWrapper?.isConnected) {
      throw new Error('Lit Protocol not connected');
    }
    return await litWrapper.decrypt(params as Record<string, unknown>);
  },

  'lit.getSession': async () => {
    if (!litWrapper) {
      return { active: false };
    }
    return await litWrapper.getSession();
  },

  'lit.encryptFile': async (params: unknown) => {
    if (!litWrapper?.isConnected) {
      throw new Error('Lit Protocol not connected');
    }
    return await litWrapper.encryptFile(params as Record<string, unknown>);
  },

  'lit.decryptFile': async (params: unknown) => {
    if (!litWrapper?.isConnected) {
      throw new Error('Lit Protocol not connected');
    }
    return await litWrapper.decryptFile(params as Record<string, unknown>);
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
      sendResponse(createErrorResponse(id!, ErrorCodes.INTERNAL_ERROR, message));
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
