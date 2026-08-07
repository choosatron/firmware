# Choosatron Firmware Releases

Published firmware images for the [Choosatron](https://choosatron.com), and the
`manifest.json` that devices check to find them.

**This repository holds build artifacts only — no source.** The source lives in a private
repository and its release workflow pushes here. Keeping the two apart means the private repo
never accumulates 1.7 MB binaries in its history, and it means these files can be public
without the source being public.

## What a Choosatron actually reads

Exactly one file:

```
https://choosatron.github.io/firmware/manifest.json
```

Everything else here exists for humans. The device (or rather, the update page it serves)
fetches that manifest, compares the version against what is installed, and — if you press
Install — downloads the `.bin` it points at.

## Layout

```
manifest.json          the only file that is polled; written LAST on every release
index.html             human-readable landing page
.nojekyll              tells Pages to serve files verbatim
publishing/            the release tooling (see below)
1.1.0/
  choosatron.bin           OTA image — this is what gets installed over the air
  choosatron-factory.bin   merged bootloader + partition table + app, for USB recovery
  SHA256SUMS
1.2.0/
  ...
```

## Three rules that are easy to get wrong

**1. Serve from GitHub Pages, never from a release-asset URL.**

The update page runs on the device at `http://192.168.4.1` and fetches this manifest
cross-origin, so the host must send `Access-Control-Allow-Origin: *`. GitHub Pages does.
`raw.githubusercontent.com` and jsDelivr do. **GitHub release-asset downloads do not** — a
browser cannot read them at all, and the failure is an opaque CORS error that looks like a
network fault. Never put a `/releases/download/…` URL in `manifest.json`.

Attaching the same files to a GitHub Release for humans to click is fine and encouraged. Just
do not point the manifest at them.

**2. Write `manifest.json` last, in its own commit.**

The manifest is the file that says "this version exists". If it lands before the binary has
finished uploading, a device that checks in that window follows a URL to a partial file. Push
the version directory first, confirm it is live, then push the manifest.

**3. Never delete a published version directory.**

A device that failed mid-update may still be pointing at one, and installing an older build to
reproduce a bug depends on old binaries staying reachable. Pages allows 1 GB per site; at
~1.9 MB per release that is hundreds of releases before it is worth thinking about.

## Publishing a release

Normally CI does this. `publishing/release.yml` is the workflow — it belongs in the **private
source repository** at `.github/workflows/release.yml`, and is kept here so the whole recipe
lives in one place.

To publish by hand, or for the very first release:

```bash
python3 publishing/publish_release.py \
    --version 1.1.0 \
    --bin /path/to/build/choosatron.bin \
    --factory-bin /path/to/build/choosatron-factory.bin \
    --channel stable \
    --notes "Chorded story selection, printer presence detection."

git add 1.1.0 && git commit -m "Add 1.1.0 artifacts" && git push
git add manifest.json && git commit -m "Publish 1.1.0 to stable" && git push
```

Two commits, in that order, on purpose — see rule 2. The script writes the manifest to disk
but deliberately does not commit anything; staging it separately is what keeps the ordering
visible.

## Repository setup

**Pages:** Settings → Pages → Source: *Deploy from a branch* → `main` / `/ (root)`.
The site becomes `https://choosatron.github.io/firmware/`.

**Publishing credentials**, so the private repo's workflow can push here:

Create a **fine-grained** personal access token scoped to **this repository only**, with
`Contents: read and write`. Add it to the *source* repository as the secret
`FIRMWARE_REPO_TOKEN`.

Do not use a classic token with the `repo` scope — that would also grant write access to the
private source repository, which is exactly what splitting the repos was meant to avoid.
Fine-grained tokens expire, so set a reminder: an expired token fails a release at the worst
possible moment.

## Signed images

Firmware is signed (RSA-3072, ESP-IDF Secure Boot V2 scheme, without hardware secure boot). A
device that is already running signed firmware will **only** accept an update signed with the
same key.

That means an unsigned or differently-signed `.bin` published here is not a cosmetic mistake —
every device in the field will refuse it, and the failure shows up in the field rather than in
CI. The workflow runs `espsecure.py verify_signature` before publishing for exactly this
reason. Do not remove that step.

## `manifest.json` format

```json
{
  "schema": 1,
  "updated": "2026-08-07",
  "channels": {
    "stable": {
      "version": "1.1.0",
      "url": "https://choosatron.github.io/firmware/1.1.0/choosatron.bin",
      "size": 1970176,
      "sha256": "9f2c…",
      "released": "2026-08-07",
      "min_version": "1.0.0",
      "critical": false,
      "notes": "One or two sentences, shown in the update panel.",
      "notes_url": "https://github.com/choosatron/firmware/releases/tag/v1.1.0"
    },
    "beta": { "…": "same shape" }
  }
}
```

| Field | Why it exists |
| --- | --- |
| `schema` | Lets a future client reject a manifest it does not understand, rather than misreading it |
| `version` | Compared as SemVer. Build metadata after `+` is ignored, and a `-prerelease` sorts below the release |
| `size` | Drives the "this will take about N seconds" estimate and the device's preflight check |
| `sha256` | Verified twice — in the browser before upload, and on the device before install |
| `min_version` | The oldest version that may jump straight to this one. Always send it, even when it is `1.0.0` |
| `critical` | Lets the UI mark a security fix visually. It never forces an install |
| `notes` | Short, human, shown inline |
| `notes_url` | "Read more" link, typically the GitHub Release page |

An empty `channels` object is valid and means "nothing published yet" — the update page says
so rather than erroring.
