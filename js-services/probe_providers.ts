/** Probe calibration PDP providers: on-chain registry + liveness of each serviceURL. */
import { paginate, spRegistry } from "@filoz/synapse-core";
import { calibration } from "@filoz/synapse-core/chains";
import * as SP from "@filoz/synapse-core/sp";
import { createPublicClient, http } from "viem";

// The direct viem install and synapse-core 0.11.0 can resolve separate viem
// copies while synapse-sdk 2.x still uses core 0.10.3; the client is the same
// at runtime, so keep the boundary casts local to this probe.
const client = createPublicClient({ chain: calibration as never, transport: http() });

const providers = await Array.fromAsync(
  paginate(({ cursor }) => spRegistry.getPDPProviders(client as never, { cursor })),
);
console.log(`providers on-chain: ${providers.length}`);
for (const p of providers) {
  const url = p.pdp.serviceURL;
  let live = "unreachable";
  try {
    // synapse-core 0.11.0: canonical PDP liveness probe (GET /pdp/ping),
    // returns false instead of throwing for HTTP/network/timeout failures.
    live = (await SP.isReachable(url, { timeout: 12000 })) ? "reachable" : "unreachable";
  } catch (e) {
    live = `FAIL ${(e as Error).message.slice(0, 80)}`;
  }
  console.log(
    `id=${p.id} live=${live} url=${url} price=${p.pdp.storagePricePerTibPerDay}`,
  );
}
