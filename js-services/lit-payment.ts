/**
 * Lit Protocol Payment Handler
 *
 * Handles Capacity Credits for Lit Protocol v8 on Naga mainnet.
 * Similar to how Synapse SDK handles Filecoin payments via checkUploadReadiness.
 *
 * Capacity Credits are NFT tokens on Chronicle Yellowstone blockchain that allow
 * users to reserve capacity (requests per second) on the Lit network.
 */

import { ethers } from 'ethers';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type LitContracts = any;

// Deno type declaration
declare const Deno: {
  env: {
    get(key: string): string | undefined;
  };
};

/**
 * Payment status result
 */
export interface PaymentStatus {
  /** Whether payment setup is complete and valid */
  ready: boolean;
  /** Status message */
  status: 'ready' | 'no_credits' | 'expired' | 'error';
  /** Human-readable message */
  message: string;
  /** Capacity credit token IDs if available */
  capacityTokenIds?: string[];
  /** Error details if status is 'error' */
  error?: string;
  /** Suggestions for fixing payment issues */
  suggestions?: string[];
}

/**
 * Configuration for Lit payment handling
 */
export interface PaymentConfig {
  /** Private key for the wallet that holds capacity credits */
  privateKey: string;
  /** Network name */
  network: 'naga' | 'naga-dev' | 'naga-staging';
  /** Whether to auto-configure/mint capacity credits if not available */
  autoConfigure: boolean;
  /** Minimum required requests per day */
  minRequestsPerDay?: number;
}

// Contract addresses for Chronicle Yellowstone
const CHRONICLE_YELLOWSTONE = {
  chainId: 175188,
  name: 'Chronicle Yellowstone',
  rpcUrl: 'https://yellowstone-rpc.litprotocol.com',
  explorer: 'https://yellowstone-explorer.litprotocol.com',
};

/**
 * Check if the wallet has valid capacity credits for Lit Protocol operations.
 * Similar to Synapse's checkUploadReadiness.
 *
 * @param config - Payment configuration
 * @returns Payment status
 */
export async function checkPaymentReadiness(
  config: PaymentConfig
): Promise<PaymentStatus> {
  try {
    // Initialize wallet
    const wallet = new ethers.Wallet(config.privateKey);
    const walletAddress = wallet.address;

    // For now, we return a warning that capacity credits are needed
    // Full implementation would query the Chronicle chain for capacity credits
    const status: PaymentStatus = {
      ready: false,
      status: 'no_credits',
      message: 'Capacity credits check not yet implemented.',
      suggestions: [
        'Mint capacity credits via Lit Explorer: https://explorer.litprotocol.com',
        'Or mint programmatically using LitContracts SDK',
        'Capacity credits are NFTs on Chronicle Yellowstone blockchain',
      ],
    };

    return status;
  } catch (error) {
    return {
      ready: false,
      status: 'error',
      message: 'Failed to check payment status',
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

/**
 * Get the Chronicle Yellowstone provider for capacity credit operations.
 */
export function getChronicleProvider(): ethers.JsonRpcProvider {
  return new ethers.JsonRpcProvider(CHRONICLE_YELLOWSTONE.rpcUrl);
}

/**
 * Create a wallet instance for capacity credit operations.
 *
 * @param privateKey - Private key (with or without 0x prefix)
 * @returns Ethers wallet instance
 */
export function createCapacityCreditWallet(privateKey: string): ethers.Wallet {
  const normalizedKey = privateKey.startsWith('0x') ? privateKey : `0x${privateKey}`;
  const provider = getChronicleProvider();
  return new ethers.Wallet(normalizedKey, provider);
}

/**
 * Check if wallet has LITKEY tokens for gas on Chronicle Yellowstone.
 *
 * @param walletAddress - Wallet address to check
 * @returns Balance in wei
 */
export async function checkLitKeyBalance(walletAddress: string): Promise<bigint> {
  const provider = getChronicleProvider();
  try {
    const balance = await provider.getBalance(walletAddress);
    return balance;
  } catch (error) {
    console.error('[Lit Payment] Failed to check LITKEY balance:', error);
    return BigInt(0);
  }
}

/**
 * Format payment status for user display.
 */
export function formatPaymentStatus(status: PaymentStatus): string {
  const lines = [
    `Payment Status: ${status.status.toUpperCase()}`,
    `Ready: ${status.ready ? '✓' : '✗'}`,
    `Message: ${status.message}`,
  ];

  if (status.capacityTokenIds && status.capacityTokenIds.length > 0) {
    lines.push(`Capacity Tokens: ${status.capacityTokenIds.join(', ')}`);
  }

  if (status.suggestions && status.suggestions.length > 0) {
    lines.push('');
    lines.push('Suggestions:');
    status.suggestions.forEach((s) => lines.push(`  - ${s}`));
  }

  if (status.error) {
    lines.push(`Error: ${status.error}`);
  }

  return lines.join('\n');
}

/**
 * Verify that the wallet is properly configured for Lit Protocol payments.
 * This should be called before any decryption operations on mainnet.
 *
 * @param privateKey - Private key to check
 * @param network - Lit network being used
 * @throws Error if payment setup is incomplete
 */
export async function verifyPaymentSetup(
  privateKey: string,
  network: string
): Promise<void> {
  // Only check payments for mainnet networks
  if (network === 'naga-dev' || network === 'datil-dev') {
    console.log('[Lit Payment] Dev network - skipping capacity credit check');
    return;
  }

  const wallet = createCapacityCreditWallet(privateKey);
  const walletAddress = wallet.address;

  console.log(`[Lit Payment] Checking payment setup for wallet: ${walletAddress}`);
  console.log(`[Lit Payment] Network: ${network}`);

  // Check LITKEY balance for gas
  const litKeyBalance = await checkLitKeyBalance(walletAddress);
  console.log(
    `[Lit Payment] LITKEY balance: ${ethers.formatEther(litKeyBalance)} LITKEY`
  );

  if (litKeyBalance === BigInt(0)) {
    console.warn('[Lit Payment] Warning: No LITKEY tokens for gas. Get some from the faucet.');
  }

  // Check for capacity credits
  // Note: Full implementation would query the CapacityCredits contract
  console.warn('[Lit Payment] Warning: Capacity credit check not fully implemented');
  console.warn('[Lit Payment] Ensure you have capacity credits minted for this wallet');
  console.warn('[Lit Payment] Mint at: https://explorer.litprotocol.com');
}

/**
 * Mint capacity credits for the wallet.
 * This is a placeholder - full implementation would use LitContracts.
 *
 * @param privateKey - Private key of the wallet to mint credits for
 * @param requestsPerDay - Number of requests per day to reserve
 * @param daysUntilExpiration - Days until the credits expire
 */
export async function mintCapacityCredits(
  privateKey: string,
  requestsPerDay: number = 14400,
  daysUntilExpiration: number = 30
): Promise<void> {
  const wallet = createCapacityCreditWallet(privateKey);

  console.log(`[Lit Payment] Minting capacity credits for wallet: ${wallet.address}`);
  console.log(`[Lit Payment] Requests per day: ${requestsPerDay}`);
  console.log(`[Lit Payment] Days until expiration: ${daysUntilExpiration}`);

  // Full implementation would:
  // 1. Import @lit-protocol/contracts-sdk
  // 2. Create LitContracts instance
  // 3. Call mintCapacityCreditsNFT

  throw new Error(
    'Capacity credit minting not yet implemented. ' +
    'Please mint manually via Lit Explorer: https://explorer.litprotocol.com'
  );
}
