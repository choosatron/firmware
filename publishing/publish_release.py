#!/usr/bin/env python3
"""
Stage a firmware release into this repository.

Copies the built images into <version>/, writes SHA256SUMS, and updates
manifest.json to point at them.

Deliberately does NOT run git. The version directory and the manifest have to be
committed separately, in that order: the manifest is the file that says "this
version exists", and if it lands before the binary has finished uploading, a
device that checks in that window follows a URL to a partial file. Leaving the
commits to the caller is what keeps that ordering visible instead of buried.

    python3 publishing/publish_release.py \\
        --version 1.1.0 \\
        --bin build/choosatron.bin \\
        --factory-bin build/choosatron-factory.bin \\
        --channel stable \\
        --notes "Chorded story selection."

The release workflow calls this same script rather than reimplementing it, for
the same reason the firmware's own build tooling has one implementation per
step: a second copy of a publishing rule is how the two quietly drift apart.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys

SITE_BASE = "https://choosatron.github.io/firmware"

# MAJOR.MINOR.PATCH, optional -prerelease, optional +build. Matches the device's
# own parser (main/ota_update.cpp) and the page's (js/ota.js).
SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")

ESP_IMAGE_MAGIC = 0xE9
APP_DESC_MAGIC = 0xABCD5432
APP_DESC_OFFSET = 0x20  # image header (24) + first segment header (8)


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_app_descriptor(path):
    """Pull version and project name out of an ESP-IDF app image.

    Publishing a binary whose embedded version disagrees with the release tag is
    the one mistake that cannot be un-published cleanly: devices compare what the
    manifest claims against what the image reports, and a mismatch means an
    update that installs and then looks like it did nothing.
    """
    with open(path, "rb") as f:
        head = f.read(APP_DESC_OFFSET + 0x100)

    if len(head) < APP_DESC_OFFSET + 0x100:
        fail(f"{path} is too small to be a firmware image.")
    if head[0] != ESP_IMAGE_MAGIC:
        fail(f"{path} does not start with 0xE9 - that is not an ESP32 firmware image.")

    (magic,) = struct.unpack_from("<I", head, APP_DESC_OFFSET)
    if magic != APP_DESC_MAGIC:
        fail(f"{path} has no application descriptor, so its version cannot be verified.")

    def field(offset, length):
        raw = head[APP_DESC_OFFSET + offset: APP_DESC_OFFSET + offset + length]
        return raw.split(b"\0")[0].decode("utf-8", "replace")

    return {"version": field(16, 32), "project": field(48, 32)}


def image_signing_keys(path):
    """Short fingerprints of the keys an image is signed with, read FROM the image.

    Derived rather than passed in on purpose: a hand-supplied value can drift
    from reality, and the one thing this field is for is letting the update page
    say "this device would refuse that build" before a two-minute upload. A wrong
    value is worse than an absent one.

    Returns [] when espsecure is unavailable, which is not fatal - the device
    still verifies the signature for itself. This is an early warning, not the
    security control.
    """
    # Absolute: sourcing export.sh runs in a subshell whose working directory is
    # not guaranteed to be ours, and a relative path silently resolves elsewhere.
    path = os.path.abspath(path)
    idf = os.environ.get("IDF_PATH", os.path.expanduser("~/.espressif/v5.4.4/esp-idf"))
    export = os.path.join(idf, "export.sh")
    cmd = f'espsecure.py signature_info_v2 "{path}"'
    if os.path.isfile(export):
        cmd = f'. "{export}" > /dev/null 2>&1; ' + cmd
    r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
    if r.returncode != 0:
        return []

    keys = []
    for line in (r.stdout or "").splitlines():
        m = re.search(r"Public key digest for block \d+:\s*([0-9a-fA-F ]+)", line)
        if m:
            octets = m.group(1).split()
            keys.append("".join(octets[:4]).lower())
    return keys


def main():
    parser = argparse.ArgumentParser(description="Stage a firmware release into this repository.")
    parser.add_argument("--version", required=True, help="release version, e.g. 1.1.0")
    parser.add_argument("--bin", required=True, help="the OTA image (build/choosatron.bin)")
    parser.add_argument("--factory-bin", help="merged image for USB recovery (optional but recommended)")
    parser.add_argument("--channel", default="stable", choices=["stable", "beta"])
    parser.add_argument("--notes", default="", help="one or two sentences, shown in the update panel")
    parser.add_argument("--notes-url", default="", help="link for 'read more', usually the GitHub Release")
    parser.add_argument("--min-version", default="1.0.0", help="oldest version that may update straight to this one")
    parser.add_argument("--critical", action="store_true", help="mark as a release the UI should highlight")
    parser.add_argument("--released", default=None, help="release date, YYYY-MM-DD (default: today, UTC)")
    parser.add_argument("--repo-root", default=None, help="path to this repository (default: the parent of this script)")
    parser.add_argument("--allow-overwrite", action="store_true",
                        help="replace an existing version directory (refused by default)")
    args = parser.parse_args()

    repo_root = args.repo_root or os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    manifest_path = os.path.join(repo_root, "manifest.json")

    if not SEMVER.match(args.version):
        fail(f"'{args.version}' is not a MAJOR.MINOR.PATCH version.")

    # A release build carries no build metadata - the firmware only appends
    # "+g<sha>" when HEAD is not an exact, clean tag. So a '+' here means someone
    # is about to publish a development build, and it would also put a '+' in
    # every published URL.
    if "+" in args.version:
        fail(
            f"'{args.version}' carries build metadata, so it is a development build.\n"
            "       Releases are built from a clean checkout of an exact tag, which produces a\n"
            "       bare version like 1.1.0. Tag the release and build from that."
        )
    if not os.path.isfile(args.bin):
        fail(f"{args.bin} does not exist.")

    # --- the image must agree with the version being published ------------
    desc = read_app_descriptor(args.bin)
    if desc["project"] != "choosatron":
        fail(f"{args.bin} reports project '{desc['project']}', not 'choosatron'.")
    if desc["version"] != args.version:
        fail(
            f"version mismatch.\n"
            f"       publishing as : {args.version}\n"
            f"       image reports : {desc['version']}\n"
            "       Devices compare these. Publishing them mismatched means an update that\n"
            "       installs and then appears to have done nothing."
        )

    # --- stage the files ---------------------------------------------------
    version_dir = os.path.join(repo_root, args.version)
    if os.path.isdir(version_dir) and not args.allow_overwrite:
        fail(
            f"{args.version}/ already exists.\n"
            "       Published versions are never rewritten: a device that failed mid-update may\n"
            "       still be pointing at it. Bump the version, or pass --allow-overwrite if this\n"
            "       release was never actually published."
        )
    os.makedirs(version_dir, exist_ok=True)

    staged = []
    shutil.copy2(args.bin, os.path.join(version_dir, "choosatron.bin"))
    staged.append("choosatron.bin")
    if args.factory_bin:
        if not os.path.isfile(args.factory_bin):
            fail(f"{args.factory_bin} does not exist.")
        shutil.copy2(args.factory_bin, os.path.join(version_dir, "choosatron-factory.bin"))
        staged.append("choosatron-factory.bin")

    sums = []
    for name in staged:
        sums.append(f"{sha256_of(os.path.join(version_dir, name))}  {name}")
    with open(os.path.join(version_dir, "SHA256SUMS"), "w", encoding="utf-8") as f:
        f.write("\n".join(sums) + "\n")

    ota_bin = os.path.join(version_dir, "choosatron.bin")
    digest = sha256_of(ota_bin)
    size = os.path.getsize(ota_bin)

    # --- update the manifest ----------------------------------------------
    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    except FileNotFoundError:
        manifest = {"schema": 1, "channels": {}}
    except json.JSONDecodeError as e:
        fail(f"manifest.json is not valid JSON: {e}")

    manifest.setdefault("schema", 1)
    manifest.setdefault("channels", {})
    manifest["updated"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    entry = {
        "version": args.version,
        "url": f"{SITE_BASE}/{args.version}/choosatron.bin",
        "size": size,
        "sha256": digest,
        "released": args.released or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d"),
        "min_version": args.min_version,
        "critical": bool(args.critical),
        "notes": args.notes,
    }
    if args.notes_url:
        entry["notes_url"] = args.notes_url

    signed_by = image_signing_keys(ota_bin)
    if signed_by:
        entry["signed_by"] = signed_by
    else:
        print("  note: could not read the image's signing keys (espsecure unavailable);")
        print("        'signed_by' omitted. The device still verifies the signature itself.")

    previous = manifest["channels"].get(args.channel)
    manifest["channels"][args.channel] = entry

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")

    # --- report -------------------------------------------------------------
    print(f"Staged {args.version} into {version_dir}")
    for name in staged:
        print(f"  {name}  ({os.path.getsize(os.path.join(version_dir, name)):,} bytes)")
    print(f"  SHA256SUMS")
    print(f"\nmanifest.json: channel '{args.channel}' "
          f"{previous['version'] + ' -> ' if previous else 'now '}{args.version}")
    print(f"  sha256 {digest}")
    print(f"  size   {size:,} bytes")
    print("\nCommit in two steps, binaries first - the manifest is what announces the release,")
    print("so it must not land before the files it points at:")
    print(f"    git add {args.version} && git commit -m 'Add {args.version} artifacts' && git push")
    print(f"    git add manifest.json && git commit -m 'Publish {args.version} to {args.channel}' && git push")


if __name__ == "__main__":
    main()
