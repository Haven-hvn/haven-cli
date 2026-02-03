/**
 * Browser Environment Shim for Deno.
 *
 * Provides minimal browser API stubs required by browser-dependent SDKs
 * (Lit Protocol, Synapse) to function in a Deno environment.
 *
 * This shim creates fake implementations of:
 * - window object
 * - localStorage
 * - sessionStorage
 * - document (minimal)
 * - navigator
 * - crypto (uses Deno's crypto)
 */

// Deno type declaration for TypeScript
declare const Deno: {
  build: {
    os: string;
  };
};

// ============================================================================
// Storage Shim
// ============================================================================

class MemoryStorage implements Storage {
  private data: Map<string, string> = new Map();

  get length(): number {
    return this.data.size;
  }

  clear(): void {
    this.data.clear();
  }

  getItem(key: string): string | null {
    return this.data.get(key) ?? null;
  }

  key(index: number): string | null {
    const keys = Array.from(this.data.keys());
    return keys[index] ?? null;
  }

  removeItem(key: string): void {
    this.data.delete(key);
  }

  setItem(key: string, value: string): void {
    this.data.set(key, value);
  }

  // Index signature for Storage interface
  [name: string]: unknown;
}

// ============================================================================
// Document Shim
// ============================================================================

const documentShim = {
  createElement: (_tagName: string) => ({
    style: {},
    setAttribute: () => {},
    getAttribute: () => null,
    appendChild: () => {},
    removeChild: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    click: () => {},
  }),
  createTextNode: (_text: string) => ({}),
  getElementById: (_id: string) => null,
  getElementsByTagName: (_tagName: string) => [],
  getElementsByClassName: (_className: string) => [],
  querySelector: (_selector: string) => null,
  querySelectorAll: (_selector: string) => [],
  body: {
    appendChild: () => {},
    removeChild: () => {},
  },
  head: {
    appendChild: () => {},
    removeChild: () => {},
  },
  documentElement: {
    style: {},
  },
  cookie: '',
  readyState: 'complete',
  addEventListener: () => {},
  removeEventListener: () => {},
  dispatchEvent: () => true,
};

// ============================================================================
// Navigator Shim
// ============================================================================

const navigatorShim = {
  userAgent: 'Haven-CLI/1.0 (Deno)',
  platform: typeof Deno !== 'undefined' ? Deno.build.os : 'unknown',
  language: 'en-US',
  languages: ['en-US', 'en'],
  onLine: true,
  cookieEnabled: false,
  hardwareConcurrency: navigator?.hardwareConcurrency ?? 4,
  maxTouchPoints: 0,
  vendor: 'Haven',
  vendorSub: '',
  productSub: '20030107',
  appCodeName: 'Mozilla',
  appName: 'Netscape',
  appVersion: '5.0',
  // Clipboard API stub
  clipboard: {
    writeText: async (_text: string) => {},
    readText: async () => '',
  },
  // Permissions API stub
  permissions: {
    query: async (_descriptor: { name: string }) => ({
      state: 'granted' as PermissionState,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => true,
    }),
  },
};

// ============================================================================
// Location Shim
// ============================================================================

const locationShim = {
  href: 'http://localhost',
  protocol: 'http:',
  host: 'localhost',
  hostname: 'localhost',
  port: '',
  pathname: '/',
  search: '',
  hash: '',
  origin: 'http://localhost',
  assign: () => {},
  reload: () => {},
  replace: () => {},
  toString: () => 'http://localhost',
};

// ============================================================================
// History Shim
// ============================================================================

const historyShim = {
  length: 1,
  scrollRestoration: 'auto' as ScrollRestoration,
  state: null,
  back: () => {},
  forward: () => {},
  go: () => {},
  pushState: () => {},
  replaceState: () => {},
};

// ============================================================================
// Window Shim
// ============================================================================

const localStorageShim = new MemoryStorage();
const sessionStorageShim = new MemoryStorage();

const windowShim = {
  // Storage
  localStorage: localStorageShim,
  sessionStorage: sessionStorageShim,

  // Document
  document: documentShim,

  // Navigator
  navigator: navigatorShim,

  // Location
  location: locationShim,

  // History
  history: historyShim,

  // Crypto (use Deno's crypto)
  crypto: globalThis.crypto,

  // Console
  console: globalThis.console,

  // Timers (use Deno's)
  setTimeout: globalThis.setTimeout,
  clearTimeout: globalThis.clearTimeout,
  setInterval: globalThis.setInterval,
  clearInterval: globalThis.clearInterval,

  // Fetch (use Deno's)
  fetch: globalThis.fetch,
  Request: globalThis.Request,
  Response: globalThis.Response,
  Headers: globalThis.Headers,

  // URL (use Deno's)
  URL: globalThis.URL,
  URLSearchParams: globalThis.URLSearchParams,

  // Events
  addEventListener: () => {},
  removeEventListener: () => {},
  dispatchEvent: () => true,

  // Window properties
  innerWidth: 1920,
  innerHeight: 1080,
  outerWidth: 1920,
  outerHeight: 1080,
  screenX: 0,
  screenY: 0,
  scrollX: 0,
  scrollY: 0,
  devicePixelRatio: 1,

  // Window methods
  alert: () => {},
  confirm: () => false,
  prompt: () => null,
  open: () => null,
  close: () => {},
  focus: () => {},
  blur: () => {},
  print: () => {},
  scroll: () => {},
  scrollTo: () => {},
  scrollBy: () => {},

  // Animation
  requestAnimationFrame: (callback: FrameRequestCallback) => {
    return setTimeout(() => callback(Date.now()), 16) as unknown as number;
  },
  cancelAnimationFrame: (handle: number) => {
    clearTimeout(handle);
  },

  // Performance
  performance: globalThis.performance,

  // Encoding
  TextEncoder: globalThis.TextEncoder,
  TextDecoder: globalThis.TextDecoder,
  atob: globalThis.atob,
  btoa: globalThis.btoa,

  // Blob/File
  Blob: globalThis.Blob,
  File: globalThis.File,
  FileReader: globalThis.FileReader,

  // Self-reference
  self: undefined as unknown,
  window: undefined as unknown,
  globalThis: globalThis,
};

// Self-references
windowShim.self = windowShim;
windowShim.window = windowShim;

// ============================================================================
// Install Shims
// ============================================================================

/**
 * Install browser shims into the global scope.
 * Call this before importing any browser-dependent SDKs.
 */
export function installBrowserShim(): void {
  const g = globalThis as Record<string, unknown>;

  // Only install if not already present
  if (typeof g.window === 'undefined') {
    g.window = windowShim;
  }

  if (typeof g.document === 'undefined') {
    g.document = documentShim;
  }

  if (typeof g.navigator === 'undefined' || !g.navigator) {
    g.navigator = navigatorShim;
  }

  if (typeof g.localStorage === 'undefined') {
    g.localStorage = localStorageShim;
  }

  if (typeof g.sessionStorage === 'undefined') {
    g.sessionStorage = sessionStorageShim;
  }

  if (typeof g.location === 'undefined') {
    g.location = locationShim;
  }

  if (typeof g.history === 'undefined') {
    g.history = historyShim;
  }

  console.log('[browser-shim] Browser environment shim installed');
}

/**
 * Remove browser shims from the global scope.
 */
export function uninstallBrowserShim(): void {
  const g = globalThis as Record<string, unknown>;

  if (g.window === windowShim) {
    delete g.window;
  }

  if (g.document === documentShim) {
    delete g.document;
  }

  if (g.localStorage === localStorageShim) {
    delete g.localStorage;
  }

  if (g.sessionStorage === sessionStorageShim) {
    delete g.sessionStorage;
  }

  console.log('[browser-shim] Browser environment shim removed');
}

// Export shims for direct access if needed
export {
  windowShim,
  documentShim,
  navigatorShim,
  locationShim,
  historyShim,
  localStorageShim,
  sessionStorageShim,
  MemoryStorage,
};
