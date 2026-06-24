"""Phase 2 regression test (BATCH_SYNC_REMEDIATION_PLAN.md).

``haven upload file`` is a one-shot interactive command. By construction it
processes exactly one context per invocation, so routing it through
``create_batched_pipeline`` produced a singleton batch with no amortization
benefit *and* lost the ``find_existing_entity()`` dedup that ``sync_context()``
performs (re-uploading the same file created a duplicate Arkiv entity).

After Phase 2, the CLI module no longer imports ``create_batched_pipeline``
and never branches on ``batch_sync_enabled``. This test pins both invariants
at the static-import level so a future refactor that re-introduces the
batched path fails the test loud and early — before any user runs the
command and finds duplicates on Arkiv.
"""

from __future__ import annotations

from pathlib import Path


UPLOAD_MODULE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "haven_cli"
    / "cli"
    / "upload.py"
)


def test_upload_cli_does_not_import_create_batched_pipeline():
    """The static import set must not include ``create_batched_pipeline``.

    A later commit that re-imports it (presumably to bring the batched
    path back) will fail this assertion before reviewers get to the
    behavioral question. The right place to use the batched pipeline is
    long-lived contexts (daemon, scheduler), never one-shot CLI.

    Phase 2's bypass docstring inside ``upload.py`` *does* reference
    ``create_batched_pipeline`` by name to explain why the bypass is
    correct. We allow comment/docstring mentions but forbid the actual
    Python ``from … import …`` line and any call site.
    """
    source = UPLOAD_MODULE_PATH.read_text(encoding="utf-8")
    # The literal ``import create_batched_pipeline`` is the line we want
    # to forbid. (Both the bare ``from … import`` form and any
    # ``importlib`` lookup would catch through this substring.)
    assert "import create_batched_pipeline" not in source, (
        "haven_cli/cli/upload.py must not import create_batched_pipeline. "
        "See BATCH_SYNC_REMEDIATION_PLAN.md Phase 2 — singleton batches "
        "lose dedup parity. The default pipeline's SyncStep handles N=1 "
        "correctly via sync_context()."
    )
    # And it must not call it either, defending against the case where
    # someone re-adds the symbol via a different import shape.
    assert "create_batched_pipeline(" not in source, (
        "haven_cli/cli/upload.py must not call create_batched_pipeline. "
        "See BATCH_SYNC_REMEDIATION_PLAN.md Phase 2."
    )


def test_upload_cli_does_not_branch_on_batch_sync_enabled():
    """The CLI must not have any ``batch_sync_enabled`` branch.

    Even if the import is removed, leaving the flag check in place is a
    foot-gun: someone might re-add the import to "fix" the dead branch
    rather than realizing the branch itself is the bug. Strip both.
    """
    source = UPLOAD_MODULE_PATH.read_text(encoding="utf-8")
    # ``batch_sync_enabled`` may legitimately appear in comments
    # explaining why the bypass is correct (Phase 2's docstring does
    # mention it). What we really want to forbid is the *runtime* check.
    # Look for the specific assignment pattern that would re-enable the
    # branch.
    assert "batch_sync_enabled = get_config_value(" not in source, (
        "haven_cli/cli/upload.py must not read batch_sync_enabled from "
        "config — Phase 2 makes the CLI unconditional. Rip out the "
        "branch entirely; don't gate it on a flag."
    )
    assert "if batch_sync_enabled" not in source, (
        "haven_cli/cli/upload.py must not branch on batch_sync_enabled. "
        "See BATCH_SYNC_REMEDIATION_PLAN.md Phase 2."
    )


def test_upload_cli_uses_create_default_pipeline():
    """Sanity check that the bypass actually wires up the default pipeline.

    Asserts the positive: the module *does* import ``create_default_pipeline``
    so we know Phase 2 didn't accidentally remove the only path that makes
    the command work.
    """
    source = UPLOAD_MODULE_PATH.read_text(encoding="utf-8")
    assert "create_default_pipeline" in source
