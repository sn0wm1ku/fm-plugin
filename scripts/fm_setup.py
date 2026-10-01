#!/usr/bin/env python3
"""Check Foundation Models prerequisites and record an explicit user's decision."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile


SDK_VERSION = "0.2.1"


def receipt_path():
    return Path.home() / ".local/share/fm-plugin/setup.json"


def failure(kind, message, **extra):
    return {"ok": False, "installation": "failed", "errors": [{"type": kind, "message": message}], **extra}


def run(argv):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        return {"ok": result.returncode == 0, "text": (result.stdout + result.stderr).strip()}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"ok": False, "text": str(error)}


def prerequisite_check():
    checks = []

    def record(name, passed, detail, remediation):
        checks.append({"name": name, "ok": passed, "detail": detail,
                       **({} if passed else {"remediation": remediation})})

    record("python", sys.version_info >= (3, 10), platform.python_version(),
           {"message": "Install Python 3.10 or newer, then rerun with that interpreter.",
            "url": "https://www.python.org/downloads/macos/"})
    mac_version = platform.mac_ver()[0]
    record("macos", platform.system() == "Darwin" and
           mac_version.split(".")[0].isdigit() and int(mac_version.split(".")[0]) >= 26, mac_version,
           {"message": "Update to macOS 26 or newer in System Settings."})
    record("architecture", platform.machine() == "arm64", platform.machine(),
           {"message": "Use an Apple Intelligence compatible Apple silicon Mac and native arm64 Python.",
            "url": "https://support.apple.com/en-us/121115"})
    if not all(item["ok"] for item in checks):
        return failure("PrerequisitesFailed", "Complete the failed platform checks before setting up fm.", checks=checks)
    developer = run(["xcode-select", "-p"])
    record("developer_directory", developer["ok"] and Path(developer["text"]).is_dir(),
           developer["text"], {"message": "Install Xcode and select its developer directory in Xcode Settings.",
                               "url": "https://developer.apple.com/xcode/"})
    xcode = run(["xcodebuild", "-version"])
    match = re.search(r"^Xcode (\d+)", xcode["text"], re.MULTILINE)
    record("xcode", xcode["ok"] and match is not None and int(match.group(1)) >= 26,
           xcode["text"], {"message": "Install Xcode 26 or newer and open it to complete setup.",
                           "url": "https://developer.apple.com/xcode/"})
    license_check = run(["xcodebuild", "-license", "check"])
    record("xcode_license", license_check["ok"], license_check["text"],
           {"message": "Open Xcode and review and accept the Xcode and Apple SDKs agreement yourself.",
            "url": "https://www.apple.com/legal/sla/docs/xcode.pdf"})
    try:
        sdk = importlib.import_module("apple_fm_sdk")
        sdk_version = importlib.metadata.version("apple-fm-sdk")
        record("sdk", sdk_version == SDK_VERSION, sdk_version,
               {"message": "With your permission, install the tested SDK using this interpreter.",
                "argv": [sys.executable, "-m", "pip", "install", "apple-fm-sdk==" + SDK_VERSION]})
    except Exception as error:
        record("sdk", False, str(error),
               {"message": "With your permission, install the tested SDK using this interpreter. Then rerun checks.",
                "argv": [sys.executable, "-m", "pip", "install", "apple-fm-sdk==" + SDK_VERSION]})
    else:
        try:
            available, reason = sdk.SystemLanguageModel().is_available()
            detail = "available" if available else str(reason)
        except Exception as error:
            available, detail = False, str(error)
        record("model", bool(available), detail,
               {"message": "Enable Apple Intelligence in System Settings and wait for model downloads. Recheck Xcode setup if initialization failed."})
    native = shutil.which("fm")
    record("fm_cli", native is not None, native or "not found",
           {"message": "Make Apple's Foundation Models CLI available on PATH, then rerun checks. On this plugin's tested macOS release it is /usr/bin/fm."})
    if all(item["ok"] for item in checks):
        return {"ok": True, "checks": checks, "python": sys.executable}
    return failure("PrerequisitesFailed", "Complete the failed checks before setting up fm.", checks=checks)


def terms():
    native = shutil.which("fm")
    if native is None:
        return failure("TermsUnavailable", "Install Apple's fm CLI to display its current terms.")
    result = run([native, "license", "--show"])
    if not result["ok"] or "LEGAL NOTICE & TERMS" not in result["text"]:
        return failure("TermsUnavailable", "The fm CLI did not return its Legal Notice & Terms.")
    return {"ok": True, "source": [native, "license", "--show"], "text": result["text"],
            "sha256": hashlib.sha256(result["text"].encode("utf-8")).hexdigest()}


def native_acceptance():
    native = shutil.which("fm")
    if native is None:
        return failure("LicenseRequired", "Install Apple's fm CLI and review its license.")
    status = run([native, "license", "--status"])
    if status["ok"] and status["text"].startswith("Agreed to license "):
        return {"ok": True, "detail": status["text"]}
    return failure("LicenseRequired", "Review and agree to Apple's terms in its interactive license command yourself.",
                   remediation={"argv": ["sudo", native, "license"], "requires_human": True})


def save_receipt(value):
    path = receipt_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
            staged = Path(output.name)
            json.dump(value, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(staged, path)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def check_installation():
    """Recheck every use before importing user extensions or exposing host tools."""
    checked = prerequisite_check()
    if not checked["ok"]:
        return checked
    current = terms()
    if not current["ok"]:
        return current
    native = native_acceptance()
    if not native["ok"]:
        return native
    try:
        receipt = json.loads(receipt_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return failure("SetupRequired", "Run setup terms and obtain the user's explicit agreement before setup accept.")
    if not isinstance(receipt, dict) or receipt.get("terms_sha256") != current["sha256"] or receipt.get("decision") != "agree":
        return failure("SetupRequired", "Current terms require the user's explicit agreement. Run setup terms again.")
    return {"ok": True, "ready": True, "checks": checked["checks"], "receipt": str(receipt_path())}


def decide(args):
    if args.command == "reject":
        receipt_path().unlink(missing_ok=True)
        return failure("LicenseRejected", "Setup failed because the user rejected the terms. Tools remain disabled.")
    if args.command == "status":
        return check_installation()
    checked = prerequisite_check()
    if args.command == "check" or not checked["ok"]:
        return checked
    current = terms()
    if args.command == "terms" or not current["ok"]:
        return current
    if not args.user_agreed:
        return failure("ExplicitAgreementRequired", "Only pass --user-agreed after the human user explicitly agrees to the displayed terms.")
    if args.terms_sha256 != current["sha256"]:
        return failure("TermsChanged", "Display the current terms and obtain agreement to their exact SHA256 before accepting.")
    native = native_acceptance()
    if not native["ok"]:
        return native
    save_receipt({"decision": "agree", "terms_sha256": current["sha256"],
                  "accepted_at": datetime.now(timezone.utc).isoformat(), "python": sys.executable})
    return {"ok": True, "ready": True, "receipt": str(receipt_path()),
            "message": "Plugin setup recorded the user's agreement. Apple's native license status was checked separately."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "terms", "status", "reject"):
        commands.add_parser(name)
    accept = commands.add_parser("accept")
    accept.add_argument("--terms-sha256", required=True)
    accept.add_argument("--user-agreed", action="store_true")
    try:
        result = decide(parser.parse_args())
    except (OSError, ValueError) as error:
        result = failure("SetupFailed", str(error))
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
