"""The setup gate must prevent package copies and extension execution."""
from pathlib import Path
import json
import shutil
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fm_sdk
import fm_setup
import fm_bridge
import install


def checks():
    rejected = {"ok": False, "installation": "failed",
                "errors": [{"type": "LicenseRejected", "message": "Rejected"}]}
    with patch.object(install, "check_installation", return_value=rejected), \
            patch.object(install.subprocess, "run") as run:
        assert install.install("codex", "fm@apple-fm", Path(".")) == rejected
        run.assert_not_called()
    for command in (["tools", "--extension", "untrusted.py"],
                    ["respond", "hello", "--extension", "untrusted.py"]):
        with patch.object(fm_setup, "check_installation", return_value=rejected), \
                patch.object(fm_sdk.importlib, "import_module") as load, \
                patch.object(fm_sdk.runpy, "run_path") as extension, \
                patch.object(fm_sdk, "emit") as output:
            assert fm_sdk.execute(fm_sdk.parser().parse_args(command)) == 1
            output.assert_called_once_with(rejected)
            load.assert_not_called()
            extension.assert_not_called()
    success = SimpleNamespace(returncode=0, stdout="{}", stderr="")
    with patch.object(install, "check_installation", return_value={"ok": True}), \
            patch.object(install.shutil, "which", side_effect=lambda name: name), \
            patch.object(install.subprocess, "run", return_value=success) as run:
        assert install.install("codex", "fm@apple-fm", Path("."))["ok"]
        assert run.call_args.args[0] == ["codex", "plugin", "add", "fm@apple-fm", "--json"]
        run.reset_mock()
        assert not install.install("codex", "unrelated@other", Path("."))["ok"]
        run.assert_not_called()
        run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="Copy failed")
        assert not install.install("codex", "fm@apple-fm", Path("."))["ok"]
        assert run.call_count == 1
    with tempfile.TemporaryDirectory() as directory:
        root, cache = Path(directory) / "source", Path(directory) / "cache"
        (root / ".claude-plugin").mkdir(parents=True)
        (root / "scripts").mkdir()
        (root / ".claude-plugin" / "plugin.json").write_text('{"version":"0.7.0"}')
        (root / "scripts" / "runtime.py").write_text("print('current')")
        shutil.copytree(root, cache)
        entry = {"id": "fm@fm-local", "scope": "user", "version": "0.7.0", "installPath": str(cache)}
        listing = SimpleNamespace(returncode=0, stdout=json.dumps([entry]), stderr="")
        with patch.object(install, "check_installation", return_value={"ok": True}), \
                patch.object(install.shutil, "which", return_value="claude"), \
                patch.object(install.subprocess, "run", side_effect=[listing, success, success, listing]) as run:
            assert install.install("claude", "fm@fm-local", root)["ok"]
            assert run.call_args_list[2].args[0] == ["claude", "plugin", "update", "fm@fm-local", "--scope", "user", "--json"]
            assert run.call_args_list[1].args[0] == ["claude", "plugin", "marketplace", "add", str(root)]
        old = SimpleNamespace(returncode=0, stdout=json.dumps([{**entry, "version": "0.6.1"}]), stderr="")
        with patch.object(install, "check_installation", return_value={"ok": True}), \
                patch.object(install.shutil, "which", return_value="claude"), \
                patch.object(install.subprocess, "run", side_effect=[listing, success, success, old]):
            result = install.install("claude", "fm@fm-local", root)
            assert not result["ok"] and result["errors"][0]["type"] == "InstallationVerificationFailed"
        (cache / "scripts" / "runtime.py").write_text("print('stale')")
        with patch.object(install.subprocess, "run", return_value=listing):
            assert not install.verify_claude("claude", "fm@fm-local", root)["ok"]
        shutil.rmtree(cache)
        with patch.object(install.subprocess, "run", return_value=listing):
            assert not install.verify_claude("claude", "fm@fm-local", root)["ok"]
    with patch.object(sys, "argv", ["fm_sdk.py", "respond", "hello", "--bridge", "session", "--stream"]), \
            patch.object(fm_bridge, "close_session") as close, patch.object(fm_sdk, "emit"):
        assert fm_sdk.main() == 2
        close.assert_called_once_with(Path("session"))
    with patch.object(fm_bridge, "close_session", side_effect=ValueError("Invalid session")), \
            patch.object(fm_sdk, "emit") as output:
        assert not fm_sdk.close_bridge(SimpleNamespace(bridge=Path("session")))
        assert output.call_args.args[0]["errors"][0]["type"] == "BridgeCleanupFailed"
    print("PASS: failed setup blocks installation and extension execution; successful setup chooses install/update")


if __name__ == "__main__":
    checks()
