#!/usr/bin/env python3
"""Offline checks: setup requires current prerequisites and explicit acceptance."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("fm_setup", Path(__file__).resolve().parents[1] / "scripts/fm_setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def args(command, agreed=False, digest="current"):
    return argparse.Namespace(command=command, user_agreed=agreed, terms_sha256=digest)


def kind(result):
    assert result["ok"] is False, result
    return result["errors"][0]["type"]


def main():
    with tempfile.TemporaryDirectory() as directory:
        receipt = Path(directory) / "state/setup.json"
        with patch.object(setup, "receipt_path", return_value=receipt), \
             patch.object(setup, "prerequisite_check", return_value={"ok": True, "checks": []}) as check, \
             patch.object(setup, "terms", return_value={"ok": True, "sha256": "current", "text": "Actual terms"}) as terms, \
             patch.object(setup, "native_acceptance", return_value={"ok": True}) as native:
            assert kind(setup.check_installation()) == "SetupRequired"
            assert kind(setup.decide(args("accept"))) == "ExplicitAgreementRequired"
            assert kind(setup.decide(args("accept", True, "stale"))) == "TermsChanged"
            assert not receipt.exists()
            native.return_value = setup.failure("LicenseRequired", "Read native terms")
            assert kind(setup.decide(args("accept", True))) == "LicenseRequired"
            assert not receipt.exists()
            native.return_value = {"ok": True}
            assert setup.decide(args("accept", True))["ok"]
            stored = json.loads(receipt.read_text())
            assert stored["decision"] == "agree" and stored["python"] == sys.executable
            assert setup.check_installation()["ready"]
            with patch.object(setup.sys, "executable", "/replacement/python3"):
                assert setup.check_installation()["ready"]
                rebound = json.loads(receipt.read_text())
                assert rebound == {**stored, "python": "/replacement/python3"}
            assert list(receipt.parent.iterdir()) == [receipt]
            check.return_value = setup.failure("PrerequisitesFailed", "Dependency removed")
            assert kind(setup.check_installation()) == "PrerequisitesFailed"
            assert kind(setup.decide(args("accept", True))) == "PrerequisitesFailed"
            check.return_value = {"ok": True, "checks": []}
            terms.return_value = {"ok": True, "sha256": "changed", "text": "New terms"}
            assert kind(setup.check_installation()) == "SetupRequired"
            assert kind(setup.decide(args("reject"))) == "LicenseRejected"
            assert not receipt.exists()
            assert kind(setup.check_installation()) == "SetupRequired"

    with patch.object(setup.shutil, "which", return_value="/usr/bin/fm"), \
         patch.object(setup, "run", return_value={"ok": True, "text": "Unrelated output"}):
        assert kind(setup.terms()) == "TermsUnavailable"
        assert kind(setup.native_acceptance()) == "LicenseRequired"
    with patch.object(setup.shutil, "which", return_value="/usr/bin/fm"), \
         patch.object(setup, "run", return_value={"ok": True, "text": "LEGAL NOTICE & TERMS\nExact text"}):
        assert len(setup.terms()["sha256"]) == 64
        assert setup.terms()["text"] == "LEGAL NOTICE & TERMS\nExact text"

    # Prerequisite boundaries are tested without importing or running the SDK.
    sdk = argparse.Namespace(SystemLanguageModel=lambda: argparse.Namespace(is_available=lambda: (True, None)))

    def command(argv):
        return {"ok": True, "text": {"xcode-select": "/tmp", "xcodebuild": "Xcode 26.0"}[argv[0]]}

    with patch.object(setup.platform, "system", return_value="Darwin"), \
         patch.object(setup.platform, "mac_ver", return_value=("26.0", (), "arm64")), \
         patch.object(setup.platform, "machine", return_value="arm64"), \
         patch.object(setup, "run", side_effect=command), \
         patch.object(setup.importlib, "import_module", return_value=sdk) as importer, \
         patch.object(setup.importlib.metadata, "version", side_effect=lambda name: "0.2.1" if name == "apple-fm-sdk" else "2.2.0"), \
         patch.object(setup.shutil, "which", return_value="/usr/bin/fm"):
        with patch.object(setup.sys, "version_info", (3, 10)):
            assert setup.prerequisite_check()["ok"]
        with patch.object(setup.sys, "version_info", (3, 9)):
            assert kind(setup.prerequisite_check()) == "PrerequisitesFailed"
        importer.side_effect = ImportError("SDK removed")
        result = setup.prerequisite_check()
        assert kind(result) == "PrerequisitesFailed"
        missing = next(item for item in result["checks"] if item["name"] == "sdk")
        assert missing["remediation"]["argv"] == [sys.executable, "-m", "pip", "install", "apple-fm-sdk==0.2.1"]
        missing_mcp = next(item for item in result["checks"] if item["name"] == "mcp")
        assert not missing_mcp["ok"]
        assert missing_mcp["remediation"]["argv"] == [sys.executable, "-m", "pip", "install", "mcp==2.2.0"]
    print("Setup checks passed (offline, no licenses accepted or dependencies installed).")


if __name__ == "__main__":
    main()
