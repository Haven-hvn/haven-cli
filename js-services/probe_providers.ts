/** Probe calibration PDP providers: on-chain registry + liveness of each serviceURL. */
import { createPublicClient, http } from "viem";
import { calibration } from "@filoz/synapse-core/chains";
import { spRegistry } from "@filoz/synapse-core";

const client = createPublicClient({ chain: calibration, transport: http() });

const { providers } = await spRegistry.getPDPProviders(client as never);
console.log(`providers on-chain: ${providers.length}`);
for (const p of providers) {
  const url = p.pdp.serviceURL;
  let live = "ERROR";
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 12000);
    // PDP services expose /pdp/info; fall back to root path on 404.
    let r = await fetch(`${url}/pdp/info`, { signal: ctrl.signal });
    if (r.status === 404) r = await fetch(url, { signal: ctrl.signal });
    clearTimeout(t);
    live = `HTTP ${r.status}`;
  } catch (e) {
    live = `FAIL ${(e as Error).message.slice(0, 80)}`;
  }
  console.log(`id=${p.id} live=${live} url=${url} price=${p.pdp.storagePricePerTibPerDay}`);
}
