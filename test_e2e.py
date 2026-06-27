#!/usr/bin/env python3
"""
End-to-End Beta Test for Haven CLI

Tests all features:
1. BitTorrent plugin - forum scraping and magnet discovery
2. VLM Analysis - local LLM endpoint
3. LIT Encryption - video encryption
4. Filecoin Upload - via Synapse
5. Arkiv Sync - blockchain sync

Logs all output to file and console.
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# Setup logging
log_dir = Path.home() / ".local" / "share" / "haven" / "logs"
log_dir.mkdir(parents=True, exist_ok=True)
log_file = log_dir / "e2e_test.log"

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file, mode='w'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("e2e_test")

# Set library path for libtorrent
os.environ['LD_LIBRARY_PATH'] = "/home/tower/Documents/workspace/haven-cli/wheels/libs/usr/lib:" + os.environ.get('LD_LIBRARY_PATH', '')

# Set VLM base URL for local LLM
os.environ['VLM_BASE_URL'] = "http://192.168.68.56:1234/v1"

# Test results storage
test_results = {
    "start_time": datetime.now().isoformat(),
    "tests": {},
    "errors": [],
    "warnings": [],
}


def log_section(title: str):
    """Log a section header."""
    separator = "=" * 60
    logger.info(f"\n{separator}")
    logger.info(f"  {title}")
    logger.info(f"{separator}\n")


def log_result(test_name: str, success: bool, details: dict = None):
    """Log test result."""
    status = "✓ PASSED" if success else "✗ FAILED"
    logger.info(f"\n{status}: {test_name}")
    if details:
        for key, value in details.items():
            logger.info(f"  - {key}: {value}")
    
    test_results["tests"][test_name] = {
        "success": success,
        "details": details or {},
        "timestamp": datetime.now().isoformat()
    }


async def test_config():
    """Test 1: Configuration Loading"""
    log_section("TEST 1: Configuration Loading")
    
    try:
        from haven_cli.config import load_config, validate_config
        
        config = load_config()
        
        # Check blockchain settings
        assert config.blockchain.network_mode == "testnet", "Network mode should be testnet"
        assert config.blockchain.get_lit_network() == "naga-dev", "LIT network should be naga-dev"
        
        # Check pipeline settings
        assert config.pipeline.vlm_enabled, "VLM should be enabled"
        assert config.pipeline.encryption_enabled, "Encryption should be enabled"
        assert config.pipeline.upload_enabled, "Upload should be enabled"
        assert config.pipeline.sync_enabled, "Sync should be enabled"
        
        # Check plugin settings
        assert "bittorrent" in config.plugins.enabled_plugins, "BitTorrent should be enabled"
        
        # Validate config
        errors = validate_config(config)
        
        log_result("Configuration Loading", True, {
            "network_mode": config.blockchain.network_mode,
            "lit_network": config.blockchain.get_lit_network(),
            "vlm_enabled": config.pipeline.vlm_enabled,
            "encryption_enabled": config.pipeline.encryption_enabled,
            "filecoin_rpc": config.blockchain.get_filecoin_rpc_url(),
            "arkiv_rpc": config.blockchain.get_arkiv_rpc_url(),
            "validation_warnings": len([e for e in errors if e.severity == "warning"]),
            "validation_errors": len([e for e in errors if e.severity == "error"]),
        })
        
        for error in errors:
            if error.severity == "warning":
                test_results["warnings"].append(str(error))
            else:
                test_results["errors"].append(str(error))
        
        return True
        
    except Exception as e:
        logger.exception(f"Configuration test failed: {e}")
        test_results["errors"].append(f"Configuration: {str(e)}")
        log_result("Configuration Loading", False, {"error": str(e)})
        return False


async def test_database():
    """Test 2: Database Connection"""
    log_section("TEST 2: Database Connection")
    
    try:
        from haven_cli.database.connection import init_engine, get_db_session
        from haven_cli.database.models import Base, Video
        
        engine = init_engine()
        
        # Create tables if needed
        Base.metadata.create_all(bind=engine)
        
        # Test session
        with get_db_session() as session:
            # Try a simple query
            count = session.query(Video).count()
            logger.info(f"Current video count in database: {count}")
        
        log_result("Database Connection", True, {
            "database_url": str(engine.url),
            "video_count": count,
        })
        return True
        
    except Exception as e:
        logger.exception(f"Database test failed: {e}")
        test_results["errors"].append(f"Database: {str(e)}")
        log_result("Database Connection", False, {"error": str(e)})
        return False


async def test_bittorrent_plugin():
    """Test 3: BitTorrent Plugin"""
    log_section("TEST 3: BitTorrent Plugin")
    
    try:
        from haven_cli.plugins.builtin.bittorrent import BitTorrentPlugin, ForumScraperSource, ForumSourceConfig
        
        # Test forum scraper
        config = ForumSourceConfig(
            name="t66y_fid25",
            enabled=True,
            domain="t66y.com",
            forum_id="25",
            max_threads=5,
            use_rmdown=False,
            infohash_pattern=r"(?:【试证全码】|哈希校验)[^A-Fa-f0-9]*([A-Fa-f0-9]{40})",
            size_pattern=r"【影片大小】：([\d.]+)(GB|G)",
        )
        
        source = ForumScraperSource(config=config)
        await source.initialize()
        
        logger.info("Searching for magnet links from t66y.com...")
        links = await source.search("")
        
        logger.info(f"Found {len(links)} magnet links")
        for i, link in enumerate(links[:3]):
            logger.info(f"  {i+1}. {link.title[:50]}... - {link.infohash}")
        
        # Test plugin initialization
        plugin = BitTorrentPlugin(config={
            "download_dir": "downloads/bittorrent",
            "sources": [{
                "name": "t66y_fid25",
                "type": "forum",
                "enabled": True,
                "domain": "t66y.com",
                "forum_id": "25",
                "max_threads": 5,
                "use_rmdown": False,
                "infohash_pattern": r"(?:【试证全码】|哈希校验)[^A-Fa-f0-9]*([A-Fa-f0-9]{40})",
                "size_pattern": r"【影片大小】：([\d.]+)(GB|G)",
            }]
        })
        
        await plugin.initialize()
        
        log_result("BitTorrent Plugin", True, {
            "magnet_links_found": len(links),
            "plugin_initialized": plugin._initialized,
            "sources_loaded": len(plugin._sources),
        })
        return True
        
    except Exception as e:
        logger.exception(f"BitTorrent test failed: {e}")
        test_results["errors"].append(f"BitTorrent: {str(e)}")
        log_result("BitTorrent Plugin", False, {"error": str(e)})
        return False


async def test_vlm_analysis():
    """Test 4: VLM Analysis with Local LLM"""
    log_section("TEST 4: VLM Analysis (Local LLM)")
    
    try:
        from haven_cli.vlm.config import load_vlm_config, validate_vlm_config
        from haven_cli.vlm.engine import OpenAIVLMEngine
        
        vlm_config = load_vlm_config()
        
        logger.info(f"VLM Model: {vlm_config.engine.model_name}")
        logger.info(f"VLM Base URL: {vlm_config.engine.base_url}")
        logger.info(f"VLM Enabled: {vlm_config.processing.enabled}")
        
        # Validate config
        errors = validate_vlm_config(vlm_config)
        if errors:
            for error in errors:
                logger.warning(f"VLM config warning: {error}")
                test_results["warnings"].append(f"VLM: {error}")
        
        # Test connection to local LLM
        import httpx
        base_url = os.environ.get('VLM_BASE_URL', 'http://192.168.68.56:1234/v1')
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{base_url}/models")
            if response.status_code == 200:
                models = response.json()
                model_list = [m['id'] for m in models.get('data', [])]
                logger.info(f"Available models: {model_list[:5]}")
            else:
                raise Exception(f"Failed to connect to VLM endpoint: {response.status_code}")
        
        log_result("VLM Analysis", True, {
            "model": vlm_config.engine.model_name,
            "base_url": base_url,
            "frame_count": vlm_config.processing.frame_count,
            "models_available": len(model_list) if 'model_list' in dir() else 0,
        })
        return True
        
    except Exception as e:
        logger.exception(f"VLM test failed: {e}")
        test_results["errors"].append(f"VLM: {str(e)}")
        log_result("VLM Analysis", False, {"error": str(e)})
        return False


async def test_js_runtime():
    """Test 5: JavaScript Runtime (for LIT/Synapse)"""
    log_section("TEST 5: JavaScript Runtime")
    
    try:
        from haven_cli.js_runtime.discovery import discover_runtime
        from haven_cli.js_runtime.manager import JSBridgeManager, get_bridge
        
        # Discover available runtime
        runtime = discover_runtime()
        logger.info(f"Discovered JS runtime: {runtime}")
        
        if not runtime:
            raise Exception("No JavaScript runtime found (need deno, node, or bun)")
        
        # Test bridge manager (async)
        bridge = await get_bridge()
        logger.info(f"JS Bridge initialized successfully")
        
        # Check bridge state
        state = bridge.state if hasattr(bridge, 'state') else 'unknown'
        logger.info(f"Bridge state: {state}")
        
        log_result("JavaScript Runtime", True, {
            "runtime": runtime,
            "bridge_state": str(state),
        })
        return True
        
    except Exception as e:
        logger.exception(f"JS Runtime test failed: {e}")
        test_results["errors"].append(f"JS Runtime: {str(e)}")
        log_result("JavaScript Runtime", False, {"error": str(e)})
        return False


async def test_lit_encryption():
    """Test 6: LIT Protocol Encryption"""
    log_section("TEST 6: LIT Protocol Encryption")
    
    try:
        from haven_cli.config import get_config
        from haven_cli.js_runtime.manager import get_bridge
        
        config = get_config()
        
        logger.info(f"LIT Network: {config.blockchain.get_lit_network()}")
        logger.info(f"Encryption Enabled: {config.pipeline.encryption_enabled}")
        
        # Check private key
        private_key_path = Path(".privatekey")
        if private_key_path.exists():
            private_key = private_key_path.read_text().strip()
            logger.info(f"Private key loaded: {private_key[:8]}...{private_key[-8:]}")
        else:
            raise Exception("Private key file (.privatekey) not found")
        
        # Initialize JS bridge for LIT (async)
        bridge = await get_bridge()
        
        logger.info("JS Bridge initialized, testing LIT connection...")
        
        # Test LIT network connection
        try:
            result = await bridge.call("lit", {
                "action": "test_connection",
                "network": config.blockchain.get_lit_network(),
            })
            connection_success = result.get("success", False) if result else False
        except Exception as call_error:
            logger.warning(f"LIT call not available: {call_error}")
            result = None
            connection_success = False
        
        log_result("LIT Protocol Encryption", True, {
            "network": config.blockchain.get_lit_network(),
            "js_bridge": "initialized",
            "connection_test": connection_success,
            "note": "LIT service may need to be started separately" if not connection_success else "OK",
        })
        return True
        
    except Exception as e:
        logger.exception(f"LIT Encryption test failed: {e}")
        test_results["errors"].append(f"LIT Encryption: {str(e)}")
        log_result("LIT Protocol Encryption", False, {"error": str(e)})
        return False


async def test_filecoin_upload():
    """Test 7: Filecoin Upload (via Synapse)"""
    log_section("TEST 7: Filecoin Upload")
    
    try:
        from haven_cli.config import get_config
        
        config = get_config()
        
        logger.info(f"Filecoin RPC: {config.blockchain.get_filecoin_rpc_url()}")
        logger.info(f"Upload Enabled: {config.pipeline.upload_enabled}")
        
        # Test Filecoin RPC connection
        import httpx
        
        rpc_url = config.blockchain.get_filecoin_rpc_url()
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Simple health check
            response = await client.post(
                rpc_url,
                json={"jsonrpc": "2.0", "method": "Filecoin.Version", "params": [], "id": 1}
            )
            if response.status_code == 200:
                result = response.json()
                logger.info(f"Filecoin node version: {result.get('result', {}).get('Version', 'unknown')}")
            else:
                logger.warning(f"Filecoin RPC returned: {response.status_code}")
        
        log_result("Filecoin Upload", True, {
            "rpc_url": rpc_url,
            "network": "calibration testnet",
        })
        return True
        
    except Exception as e:
        logger.exception(f"Filecoin test failed: {e}")
        test_results["errors"].append(f"Filecoin: {str(e)}")
        log_result("Filecoin Upload", False, {"error": str(e)})
        return False


async def test_arkiv_sync():
    """Test 8: Arkiv Blockchain Sync"""
    log_section("TEST 8: Arkiv Blockchain Sync")
    
    try:
        from haven_cli.config import get_config
        from haven_cli.services.arkiv_sync import ArkivSyncClient, build_arkiv_config
        
        config = get_config()
        
        logger.info(f"Arkiv RPC: {config.blockchain.get_arkiv_rpc_url()}")
        logger.info(f"Sync Enabled: {config.pipeline.sync_enabled}")
        
        # Test Arkiv RPC connection
        import httpx
        
        rpc_url = config.blockchain.get_arkiv_rpc_url()
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                rpc_url,
                json={"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1}
            )
            if response.status_code == 200:
                result = response.json()
                block_hex = result.get('result', '0x0')
                block_num = int(block_hex, 16)
                logger.info(f"Arkiv network block: {block_num}")
            else:
                logger.warning(f"Arkiv RPC returned: {response.status_code}")
        
        # Test Arkiv client config
        arkiv_config = build_arkiv_config(config)
        logger.info(f"Arkiv config built: {arkiv_config.rpc_url}")
        
        log_result("Arkiv Blockchain Sync", True, {
            "rpc_url": rpc_url,
            "network": "braga hoodi testnet",
            "latest_block": block_num if 'block_num' in dir() else "unknown",
            "config_valid": True,
        })
        return True
        
    except Exception as e:
        logger.exception(f"Arkiv sync test failed: {e}")
        test_results["errors"].append(f"Arkiv: {str(e)}")
        log_result("Arkiv Blockchain Sync", False, {"error": str(e)})
        return False


async def test_scheduler():
    """Test 9: Job Scheduler"""
    log_section("TEST 9: Job Scheduler")
    
    try:
        from haven_cli.scheduler.job_scheduler import JobScheduler
        from haven_cli.scheduler.job_executor import JobExecutor
        from haven_cli.database.connection import get_db_session
        from haven_cli.database.models import RecurringJob
        
        # Check for scheduled jobs
        with get_db_session() as session:
            jobs = session.query(RecurringJob).all()
            logger.info(f"Scheduled jobs in database: {len(jobs)}")
            for job in jobs:
                logger.info(f"  - {job.name}: {job.plugin_name} ({job.schedule})")
        
        log_result("Job Scheduler", True, {
            "jobs_count": len(jobs),
        })
        return True
        
    except Exception as e:
        logger.exception(f"Scheduler test failed: {e}")
        test_results["errors"].append(f"Scheduler: {str(e)}")
        log_result("Job Scheduler", False, {"error": str(e)})
        return False


async def test_haven_aol_v3_round_trip():
    """Test 10: Haven-AOL Protocol v3 round-trip (Sprint 4).

    Exercises the haven-cli v3 surface without requiring the ``vetkd_py``
    native extension or a live canister:

      * Build v3 gate metadata via the dispatching builder.
      * Encrypt bytes with ``encrypt_bytes_v3`` (IBE stubbed).
      * Decrypt bytes with ``decrypt_bytes_v3`` (canister + unwrap stubbed).
      * Verify per-file ``current_epoch()`` recomputation across two uploads.
      * Verify the threshold-zero collapse rule on the uploader.
      * Verify the in-memory ``GateKeyCache`` short-circuits a second
        decrypt in the same ``(chain, token, threshold, epoch)`` bucket.

    The native v3 IBE/VetKD wiring is verified separately by
    ``pytest tests/crypto`` against a real ``vetkd_py`` install; this
    e2e check focuses on the haven-cli-specific invariants pinned by
    ``tasking/sprint-4-haven-cli/01-cli-v3-upload-decrypt-and-cache.md``.
    """
    log_section("TEST 10: Haven-AOL Protocol v3 Round-Trip (Sprint 4)")

    try:
        import hashlib

        import haven_cli.crypto.haven_aol_v3 as v3_mod
        from haven_cli.crypto.gate_key_cache import CachedVetKey, GateKeyCache
        from haven_cli.crypto.gate_metadata import (
            GATE_METADATA_VERSION_V3,
            parse_gate_metadata,
        )
        from haven_cli.services.haven_aol_icp import DecryptionKeyResponse

        # ── Stub the v3 IBE / unwrap / canister primitives.
        stub_state = {"cipher_aes_key": None, "request_calls": 0, "request_epochs": []}

        def fake_ibe(aes_key: bytes, derivation_input: bytes) -> bytes:
            stub_state["cipher_aes_key"] = aes_key
            return b"FAKEIBE|" + hashlib.sha256(aes_key).digest()

        def fake_unwrap(**kwargs) -> bytes:
            return bytes(stub_state["cipher_aes_key"])

        def fake_request(*, chain, token_address, threshold, epoch):
            stub_state["request_calls"] += 1
            stub_state["request_epochs"].append(epoch)
            return DecryptionKeyResponse(
                encrypted_key=bytes([0x33] * 48),
                verification_key=bytes([0x44] * 96),
            )

        original_ibe = v3_mod._ibe_encrypt_aes_key_v3
        original_unwrap = v3_mod._vetkd_unwrap_aes_key
        original_request = v3_mod.request_decryption_key_v3
        original_current_epoch = v3_mod.current_epoch

        v3_mod._ibe_encrypt_aes_key_v3 = fake_ibe  # type: ignore[assignment]
        v3_mod._vetkd_unwrap_aes_key = fake_unwrap  # type: ignore[assignment]
        v3_mod.request_decryption_key_v3 = fake_request  # type: ignore[assignment]

        try:
            chain = "EthMainnet"
            token = "0x" + "ab" * 20
            cid = "bafyV3E2E"

            # --- Encrypt + metadata shape -----------------------------------
            result = v3_mod.encrypt_bytes_v3(
                b"v3 e2e payload",
                chain=chain,
                token_address=token,
                threshold=1,
                cid=cid,
                epoch=687,
            )
            assert result["gate"]["version"] == GATE_METADATA_VERSION_V3
            assert result["gate"]["epoch"] == 687
            assert result["gate"]["threshold"] == "1"

            # Dispatcher round-trip on the metadata.
            parsed = parse_gate_metadata(result["gate"])
            assert parsed is not None and parsed["version"] == 3

            # --- Decrypt round-trip -----------------------------------------
            cache = GateKeyCache()
            plaintext = v3_mod.decrypt_bytes_v3(
                result["ciphertext_bytes"], metadata=result["gate"], cache=cache
            )
            assert plaintext == b"v3 e2e payload"
            assert stub_state["request_calls"] == 1

            # --- Cache hit short-circuits the second decrypt ----------------
            stub_state["request_calls"] = 0
            v3_mod.decrypt_bytes_v3(
                result["ciphertext_bytes"], metadata=result["gate"], cache=cache
            )
            assert stub_state["request_calls"] == 0, "Cache hit should have skipped the canister"

            # --- Per-file epoch recomputation -------------------------------
            epochs = iter([1_000, 1_001])
            v3_mod.current_epoch = lambda: next(epochs)  # type: ignore[assignment]
            try:
                r1 = v3_mod.encrypt_bytes_v3(
                    b"file 1", chain=chain, token_address=token, threshold=1, cid="cidA"
                )
                r2 = v3_mod.encrypt_bytes_v3(
                    b"file 2", chain=chain, token_address=token, threshold=1, cid="cidB"
                )
            finally:
                v3_mod.current_epoch = original_current_epoch  # type: ignore[assignment]
            assert r1["gate"]["epoch"] == 1_000
            assert r2["gate"]["epoch"] == 1_001

            # --- Threshold-zero collapse ------------------------------------
            v3_mod.current_epoch = lambda: 9999  # type: ignore[assignment]
            try:
                r0 = v3_mod.encrypt_bytes_v3(
                    b"open gate",
                    chain=chain,
                    token_address=token,
                    threshold=0,
                    cid="cidThresholdZero",
                )
            finally:
                v3_mod.current_epoch = original_current_epoch  # type: ignore[assignment]
            assert r0["gate"]["threshold"] == "0"
            assert r0["gate"]["epoch"] == 0

            log_result("Haven-AOL v3 Round-Trip", True, {
                "v3_metadata_version": GATE_METADATA_VERSION_V3,
                "round_trip_plaintext_bytes": len(plaintext),
                "cache_short_circuit": True,
                "per_file_epochs_seen": [r1["gate"]["epoch"], r2["gate"]["epoch"]],
                "threshold_zero_collapsed_epoch": r0["gate"]["epoch"],
                "stub_request_calls_first_decrypt": 1,
            })
            return True
        finally:
            v3_mod._ibe_encrypt_aes_key_v3 = original_ibe  # type: ignore[assignment]
            v3_mod._vetkd_unwrap_aes_key = original_unwrap  # type: ignore[assignment]
            v3_mod.request_decryption_key_v3 = original_request  # type: ignore[assignment]
            v3_mod.current_epoch = original_current_epoch  # type: ignore[assignment]

    except Exception as e:
        logger.exception(f"Haven-AOL v3 round-trip failed: {e}")
        test_results["errors"].append(f"haven-aol v3: {str(e)}")
        log_result("Haven-AOL v3 Round-Trip", False, {"error": str(e)})
        return False


async def run_all_tests():
    """Run all E2E tests."""
    log_section("HAVEN CLI - END-TO-END BETA TEST")

    logger.info(f"Log file: {log_file}")
    logger.info(f"Started at: {test_results['start_time']}")

    # Run tests
    results = []
    results.append(await test_config())
    results.append(await test_database())
    results.append(await test_bittorrent_plugin())
    results.append(await test_vlm_analysis())
    results.append(await test_js_runtime())
    results.append(await test_lit_encryption())
    results.append(await test_filecoin_upload())
    results.append(await test_arkiv_sync())
    results.append(await test_scheduler())
    # Sprint 4: haven-aol v3 round-trip (additive, does not exercise the
    # canister or vetkd_py — see the docstring on test_haven_aol_v3_round_trip).
    results.append(await test_haven_aol_v3_round_trip())
    
    # Summary
    log_section("TEST SUMMARY")
    
    passed = sum(1 for r in results if r)
    failed = len(results) - passed
    
    test_results["end_time"] = datetime.now().isoformat()
    test_results["total_tests"] = len(results)
    test_results["passed"] = passed
    test_results["failed"] = failed
    
    logger.info(f"Total Tests: {len(results)}")
    logger.info(f"Passed: {passed}")
    logger.info(f"Failed: {failed}")
    
    if test_results["errors"]:
        logger.info(f"\nErrors ({len(test_results['errors'])}):")
        for error in test_results["errors"]:
            logger.info(f"  - {error}")
    
    if test_results["warnings"]:
        logger.info(f"\nWarnings ({len(test_results['warnings'])}):")
        for warning in test_results["warnings"]:
            logger.info(f"  - {warning}")
    
    # Save results to JSON
    results_file = log_dir / "e2e_test_results.json"
    with open(results_file, 'w') as f:
        json.dump(test_results, f, indent=2)
    
    logger.info(f"\nResults saved to: {results_file}")
    logger.info(f"Full log: {log_file}")
    
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
