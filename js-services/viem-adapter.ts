/**
 * Viem Adapter - Bridge between ethers and viem for Lit SDK v8
 *
 * Lit SDK v8 requires viem accounts for AuthManager.
 * This adapter converts private keys to viem accounts.
 */

import { privateKeyToAccount } from 'viem/accounts';
import { ethers } from 'ethers';
import type { Account } from 'viem';

/**
 * Normalize private key to ensure 0x prefix
 */
function normalizePrivateKey(privateKey: string): `0x${string}` {
  const trimmed = privateKey.trim();
  const withPrefix = trimmed.startsWith('0x') ? trimmed : `0x${trimmed}`;
  return withPrefix as `0x${string}`;
}

/**
 * Create viem account from private key string
 * This is the bridge between Haven CLI's wallet management
 * and Lit SDK v8's viem-based AuthManager
 */
export function createViemAccount(privateKey: string): Account {
  const normalizedKey = normalizePrivateKey(privateKey);

  // Validate key length (0x + 64 hex chars = 66 characters)
  if (normalizedKey.length !== 66) {
    throw new Error(
      `Invalid private key length. Expected 32 bytes (64 hex characters) with 0x prefix, ` +
        `got ${normalizedKey.length} characters.`
    );
  }

  // Validate key format
  const hexRegex = /^0x[0-9a-fA-F]{64}$/;
  if (!hexRegex.test(normalizedKey)) {
    throw new Error(
      'Invalid private key format. Expected 0x prefix followed by 64 hexadecimal characters.'
    );
  }

  return privateKeyToAccount(normalizedKey);
}

/**
 * Create viem account from ethers.Wallet instance
 */
export function createViemAccountFromWallet(wallet: ethers.Wallet): Account {
  return createViemAccount(wallet.privateKey);
}

/**
 * Get wallet address from private key using ethers
 */
export function getWalletAddressFromPrivateKey(privateKey: string): string {
  const normalizedKey = normalizePrivateKey(privateKey);
  const wallet = new ethers.Wallet(normalizedKey);
  return wallet.address;
}

/**
 * Verify that an ethers address matches a viem account
 */
export function verifyAddressMatch(ethersAddress: string, viemAccount: Account): boolean {
  return ethersAddress.toLowerCase() === viemAccount.address.toLowerCase();
}

/**
 * Get the viem account address as ethers-compatible checksum address
 */
export function getEthersCompatibleAddress(viemAccount: Account): string {
  // viem returns lowercase address, ethers uses checksum address
  // We can just return the lowercase address as ethers handles both
  return viemAccount.address.toLowerCase();
}
