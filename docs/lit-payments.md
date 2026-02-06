# Lit Protocol Payment Handling

## Overview

This document explains how Lit Protocol payments work in Haven CLI and compares it to Synapse (Filecoin) payment handling.

## Quick Summary

| Aspect | Synapse (Filecoin) | Lit Protocol |
|--------|-------------------|--------------|
| **Payment Check** | ✅ `checkUploadReadiness()` | ✅ `verifyPaymentSetup()` (new) |
| **Auto-Configuration** | ✅ `autoConfigureAllowances: true` | ❌ Manual minting required |
| **Error Handling** | Clear payment errors | Now warns about missing credits |
| **Wallet Integration** | Full wallet support | Basic (key for auth only) |

## Synapse SDK (Filecoin) - Reference Implementation

The Synapse SDK properly handles payments via the `filecoin-pin` package:

```typescript
// From js-services/synapse-wrapper.ts
const readiness = await checkUploadReadiness({
  synapse: synapse as any,
  fileSize: carBytes.length,
  autoConfigureAllowances: true,  // Automatically sets up payments
});

if (readiness.status === 'blocked') {
  throw new Error('Upload blocked: Payment setup incomplete');
}
```

**Key Features:**
- Proactively checks payment status before operations
- Auto-configures allowances if needed
- Provides actionable error messages

## Lit Protocol Payment Model

### What are Capacity Credits?

Capacity Credits are NFT tokens on the **Chronicle Yellowstone** blockchain that allow users to reserve capacity (requests per second) on the Lit network.

**Operations requiring Capacity Credits on mainnet:**
- Decrypting data
- Signing using PKPs (Programmable Key Pairs)
- Executing Lit Actions

**Operations NOT requiring credits:**
- Connecting to the network
- Encrypting data (on most networks)

### Networks and Payment Requirements

| Network | Payment Required | Notes |
|---------|-----------------|-------|
| `naga` (mainnet) | ✅ Yes | Production network, requires Capacity Credits |
| `naga-dev` | ❌ No | Development network, free to use |
| `naga-staging` | ❌ No | Staging network |
| `datil-dev` | ❌ No | Legacy dev network |

### Current Implementation

```typescript
// From js-services/lit-payment.ts
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

  // Check LITKEY balance for gas
  const litKeyBalance = await checkLitKeyBalance(walletAddress);
  console.log(`[Lit Payment] LITKEY balance: ${ethers.formatEther(litKeyBalance)} LITKEY`);

  if (litKeyBalance === BigInt(0)) {
    console.warn('[Lit Payment] Warning: No LITKEY tokens for gas');
  }

  // Note: Full capacity credit checking requires LitContracts SDK
  console.warn('[Lit Payment] Ensure you have capacity credits minted for this wallet');
}
```

## Setting Up Capacity Credits

### Option 1: Lit Explorer (Recommended for beginners)

1. Visit https://explorer.litprotocol.com
2. Connect your wallet (the same one used for Haven CLI)
3. Navigate to "Capacity Credits"
4. Mint a new capacity credit NFT
5. Configure requests per day and expiration

### Option 2: Programmatic Minting (Advanced)

```typescript
import { LitContracts } from '@lit-protocol/contracts-sdk';
import { LIT_NETWORK } from '@lit-protocol/constants';

const wallet = new Wallet(privateKey);
const contractClient = new LitContracts({
  signer: wallet,
  network: LIT_NETWORK.Naga,
});

await contractClient.connect();

const { capacityTokenIdStr } = await contractClient.mintCapacityCreditsNFT({
  requestsPerDay: 14400,
  daysUntilUTCMidnightExpiration: 30,
});

console.log(`Minted capacity credit: ${capacityTokenIdStr}`);
```

### Option 3: Faucet for Dev Networks

For `naga-dev` network, you can get test tokens from the Lit faucet:
- Visit: https://faucet.litprotocol.com
- Request LITKEY tokens for gas

## Environment Variables

The following environment variables are used for Lit Protocol payments:

```bash
# Required for authentication
export HAVEN_PRIVATE_KEY="your-private-key"

# Optional: Specify Lit network (default: naga)
export HAVEN_LIT_NETWORK="naga"  # or "naga-dev" for free testing

# Note: There is no separate payment configuration like Synapse's SYNAPSE_API_KEY
# Payments are handled directly via the wallet's capacity credits
```

## Comparing Wallet Usage

### Synapse (Filecoin)

```typescript
// Wallet is used for:
// 1. Signing Filecoin transactions
// 2. Paying for storage (via allowances)
// 3. Creating storage deals

const initConfig = {
  privateKey: normalizedPrivateKey,
  rpcUrl,
  telemetry: { sentryInitOptions: { enabled: false } },
};

const synapse = await initSynapse(initConfig, logger);

// Payment check happens automatically
checkUploadReadiness({ synapse, fileSize, autoConfigureAllowances: true });
```

### Lit Protocol

```typescript
// Wallet is used for:
// 1. Creating authentication context (signing SIWE messages)
// 2. Proving ownership for decryption access
// 3. Gas payments on Chronicle Yellowstone (for capacity credits)

const authContext = await authManager.createEoaAuthContext({
  authConfig: {
    domain: 'haven-player.local',
    statement: 'Sign this message to authenticate with Haven Player',
    resources: [...],
    expiration: new Date(Date.now() + 1000 * 60 * 60).toISOString(),
  },
  config: { account: viemAccount },
  litClient,
});

// Payment verification (new addition)
await verifyPaymentSetup(privateKey, network);
```

## Testing Payment Setup

### Check if you have capacity credits:

```bash
# Run the Lit encryption test
python test_lit_encryption.py

# If you see this warning, you need capacity credits:
# "[Lit Payment] Warning: Capacity credit check not fully implemented"
```

### Check your wallet's LITKEY balance:

```bash
# Using ethers.js (via Deno)
deno eval --allow-net "
import { ethers } from 'ethers';
const provider = new ethers.JsonRpcProvider('https://yellowstone-rpc.litprotocol.com');
const balance = await provider.getBalance('YOUR_WALLET_ADDRESS');
console.log('LITKEY balance:', ethers.formatEther(balance));
"
```

## Troubleshooting

### "Payment/Capacity credits required on mainnet"

**Cause:** You're using `naga` mainnet without capacity credits.

**Solutions:**
1. Switch to `naga-dev` for testing (free):
   ```python
   await bridge.call("lit.connect", {"network": "naga-dev"})
   ```

2. Mint capacity credits via Lit Explorer

3. Ensure your wallet has LITKEY tokens for gas

### "No LITKEY tokens for gas"

**Cause:** Your wallet needs LITKEY tokens to pay for gas on Chronicle Yellowstone.

**Solutions:**
- Get test LITKEY from: https://faucet.litprotocol.com
- For mainnet, acquire LITKEY via exchanges

### "Lit Protocol not connected"

**Cause:** The connection to Lit network failed.

**Solutions:**
- Check network connectivity
- Verify you're using the correct network name
- Check Lit Protocol status page

## Future Improvements

To achieve parity with Synapse's payment handling, we could:

1. **Add LitContracts SDK dependency** for full capacity credit management
2. **Implement auto-minting** similar to `autoConfigureAllowances`
3. **Add payment estimation** before operations
4. **Cache capacity credit status** to avoid repeated checks
5. **Add payment alerts** when credits are running low

## References

- [Lit Protocol Capacity Credits Documentation](https://developer.litprotocol.com/paying-for-lit/capacity-credits)
- [Lit SDK Capacity Credits Guide](https://developer.litprotocol.com/sdk/capacity-credits)
- [Chronicle Yellowstone Explorer](https://yellowstone-explorer.litprotocol.com)
- [Lit Protocol Explorer](https://explorer.litprotocol.com)
