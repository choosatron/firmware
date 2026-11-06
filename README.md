# Choosatron Firmware Releases

Published firmware images for the [Choosatron](https://choosatron.com), and the
`manifest.json` that devices check to find them.

**This repository holds build artifacts only — no source.** The source lives in a private
repository and its release workflow pushes here. Keeping the two apart means the private repo
never accumulates 1.7 MB binaries in its history, and it means these files can be public
without the source being public.

## What reads it

A Choosatron reads exactly one file:

```
https://choosatron.github.io/firmware/manifest.json
```

Everything else here exists for humans. The device (or rather, the update page it serves)
fetches that manifest, compares the version against what is installed, and — if you press
Install — downloads the `.bin` it points at.

## Layout

```
manifest.json               what the update page polls; written LAST on every release
flasher-<board>.json        the USB flasher's fixed address for the latest stable release
flasher-<board>-beta.json   ... and for the latest beta
index.html                  human-readable landing page
.nojekyll                   tells Pages to serve files verbatim
1.1.0/
  wroom-v0.1/
    choosatron.bin            OTA image - what gets installed over the air
    choosatron-factory.bin    merged bootloader + partition table + app, for USB recovery
  xiao-v0.1/
    ...                       the same, for the XIAO board
  flasher-<board>.json        ESP Web Tools manifest for this version
  SHA256SUMS
1.2.0/
  ...
```

**One image per board.** The buttons and printer are wired to different pins on the two
boards, and the pin map is chosen when the firmware is built, so an image for one board
leaves the other's buttons and printer dead. `<board>` is the name the firmware reports as
`board` in `/api/v1/device/health`: `wroom-v0.1` or `xiao-v0.1`.

The flasher files are read by the browser USB flasher (`choosatron.github.io/flasher/`, its
own repository). Same origin as this site, so it fetches the images here directly.

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

The manifest and the root `flasher-*.json` files are what say "this version exists". If they
land before the binaries have finished uploading, a device or the flasher checking in that
window follows a URL to a partial file. Push the version directory first, confirm it is live,
then push the manifest and the flasher files together.

**3. Never delete a published version directory.**

A device that failed mid-update may still be pointing at one, and installing an older build to
reproduce a bug depends on old binaries staying reachable. Pages allows 1 GB per site; at
~1.9 MB per release that is hundreds of releases before it is worth thinking about.

## Publishing a release

Nothing here is edited by hand. The release workflow in the private source repository
(`.github/workflows/release.yml`) builds an image for each board on a version tag, verifies
each signature, and calls `choosatron/tools/24_publish_release.py` there, which stages the
files above and writes the announcing ones. The workflow then commits in two steps, as rule 2
requires. The source repository's `docs/firmware-runbook.md` §3 is the procedure, including
publishing by hand.

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

Schema 2. A channel names one release; the release carries an image per board.

```json
{
  "schema": 2,
  "updated": "2026-11-05",
  "channels": {
    "stable": {
      "version": "1.1.0",
      "released": "2026-11-05",
      "min_version": "1.0.0",
      "critical": false,
      "notes": "One or two sentences, shown in the update panel.",
      "notes_url": "https://github.com/choosatron/firmware/releases/tag/v1.1.0",
      "boards": {
        "wroom-v0.1": {
          "url": "https://choosatron.github.io/firmware/1.1.0/wroom-v0.1/choosatron.bin",
          "size": 2953216,
          "sha256": "9f2c…",
          "signed_by": ["3f9a1c22"],
          "factory_url": "https://choosatron.github.io/firmware/1.1.0/wroom-v0.1/choosatron-factory.bin",
          "factory_size": 3018752,
          "factory_sha256": "71ab…",
          "flasher": "https://choosatron.github.io/firmware/1.1.0/flasher-wroom-v0.1.json"
        },
        "xiao-v0.1": { "…": "same shape" }
      }
    },
    "beta": { "…": "same shape" }
  }
}
```

| Field | Why it exists |
| --- | --- |
| `schema` | Lets a client reject a manifest it does not understand, rather than misreading it. Firmware that reads schema 1 says it does not understand this one |
| `version` | Compared as SemVer. Build metadata after `+` is ignored, and a `-prerelease` sorts below the release |
| `min_version` | The oldest version that may jump straight to this one. Always send it, even when it is `1.0.0` |
| `critical` | Lets the UI mark a security fix visually. It never forces an install |
| `notes`, `notes_url` | Short, human, shown inline; and a "read more" link, typically the GitHub Release |
| `boards` | One entry per board. The update page offers only its own board's image |
| `size` | Drives the "this will take about N seconds" estimate and the device's preflight check |
| `sha256` | Verified twice - in the browser before upload, and on the device before install |
| `signed_by` | Read from the image when publishing. Lets the update page refuse a build the device would reject, before uploading it |
| `factory_*`, `flasher` | For USB recovery: the merged image, and its ESP Web Tools manifest |

An empty `channels` object is valid and means "nothing published yet" — the update page says
so rather than erroring.
