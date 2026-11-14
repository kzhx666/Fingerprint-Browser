# KZ Fingerprint Browser

A **Chromium multi-instance + fingerprint isolation** solution for Windows, made of two parts:

| Part | Role |
|---|---|
| **WPF Manager** (`app/`) | Manages dozens of isolated browser profiles: fingerprint, proxy, timezone, language, cloning, snapshots, data sync |
| **Patched Chromium** (`kernel/`) | Rewrites the hardware and rendering values the browser reports, at the C++ level — not via JavaScript hooks |

> **Both halves are required.**
> Deploying only the manager gives you no fingerprint spoofing — a stock Chromium ignores every parameter it sends.
> The kernel must be compiled from source, or taken from a release package's `App/` directory.

Full walkthrough: [`docs/使用指南.md`](docs/使用指南.md) (Chinese).
Patch inventory: [`docs/内核补丁清单.md`](docs/内核补丁清单.md) (Chinese).

---

## How it works

Running many browsers is easy. Making each one **look like a different machine** — with every parameter mutually consistent — is the hard part.

**1. Instance isolation**
Every instance owns a separate `--user-data-dir`. Cookies, cache, extensions and bookmarks never leak across instances.

**2. Fingerprint derived from a seed**
Each instance has a single integer `seed`. CPU cores, memory, screen size, pixel ratio, Canvas noise, WebGL renderer, Audio noise, font list, timezone, language and geolocation are all derived deterministically from it.

```
same seed  → same machine
new seed   → new machine
```

Cloning an instance and changing its seed yields "the same model, a different unit". Keeping the seed stable keeps the identity stable — which is what long-lived accounts need.

**3. Enforced in the kernel**

| Surface | Where it is rewritten |
|---|---|
| `navigator.platform` / UA / UA-CH | all three, kept in sync |
| `Intl.DateTimeFormat().resolvedOptions().timeZone` | ICU layer |
| `navigator.language` + `Accept-Language` header | renderer + network layer |
| CPU cores / memory / screen / pixel ratio | hardware query interfaces |
| Canvas / WebGL / Audio | per-instance noise at read time |
| Font list | filtered per spoofed platform (macOS 110 / Windows 136 / Linux 87) |
| Geolocation | fully taken over |
| WebRTC | four modes: direct / replace with proxy IP / fixed IP / disabled |

---

## Features

### Instance management

- **Isolation** — independent `user-data-dir` per instance
- **Drag to reorder** with a live insertion indicator
- **Clone** — config only (auto new seed), or including data
- **Snapshots** — hard-link deduplicated; 6441 files in 6.3 MB
- **Data sync** — bookmarks / history / passwords / extensions from A to B, with automatic backup
- **Batch operations** — multi-select launch, batch packaging, cache cleanup
- **Live search** — by name, tag, platform, timezone or seed

### Fingerprint & consistency

- **One-click profile generation** — coherent CPU / GPU / memory / resolution / DPR combinations
- **Consistency matrix** — locks `UA ↔ UA-CH ↔ platform ↔ rendering pipeline ↔ screen ↔ fonts ↔ hardware` together
- **Real device library** — Windows 11/10, macOS (Apple Silicon / Intel), Linux desktop samples
- **Conflict diagnostics** — flags language-vs-exit-country, timezone-vs-latency contradictions with a risk score
- **Visual regression** — real-browser probe covering 16 single-instance metrics + 5 cross-instance independence checks over 3 random seeds

### Network & proxy

- **Proxy manager** — pooling, grouping, bulk import (`host:port:user:pass`), concurrent latency tests, auto assignment
- **Region alignment** — one click extracts the exit IP and matches timezone, coordinates and language
- **Kill switch** — kernel-level traffic blocking (DNS / WebRTC / IPv6 / QUIC) when the proxy fails; never falls back to a direct connection
- **Node library** — bulk import vless / hysteria2 / tuic / shadowsocks subscriptions
- **Node pool** — automatic failover based on real SOCKS5 CONNECT probes
- **Fraud pre-check** — distinguishes datacenter from residential IPs, flags high-risk proxies

### Accounts & data

- **Account profiles** — bind multiple platform accounts per instance, with username, memo and 2FA secret
- **Cookie manager** — Netscape / JSON import, export, dedupe, merge; written into the profile database before launch
- **Extension manager** — bulk preinstall from a local `_extensions` directory
- **Color tags** — custom labels with intersection / union filtering
- **Health overview** — proxy status, fingerprint score, cookie expiry, disk usage at a glance
- **Fine-grained cleanup** — clear cache / IndexedDB / ServiceWorker while keeping cookies, bookmarks and passwords

### Browser UI

- **Right vertical sidebar** — bookmarks, reading list, history, downloads, plus **custom site icons**
- **Custom sites** — press `+` to grab the current page's title and favicon; right-click to open / edit / remove
- **Default browser routing** — external links go to whichever instance is in the foreground
- **Dark / light theme** — instant switch

---

## Quick start

### Requirements

- Windows 10 / 11 x64
- Release package `KZBrowser-Portable-*.zip` (kernel + manager + tools)
- Building from source: Visual Studio 2022, depot_tools, .NET 10 SDK, Python 3.11+

### Deploy

Unpack to any path without spaces:

```
KZBrowser/
├── App/               patched Chromium
├── KZManager.exe      manager
├── node-proxy.py
└── ...
```

### Launch

Run `KZManager.exe` (or `启动管理器.bat`). First start creates `manager.json` and the `_proxies`, `_snapshots`, `_backup` directories.

### Create an instance

1. Click **New** — set name, platform, timezone, language
2. Click **Generate** to produce a coherent fingerprint profile
3. Fill in a proxy node if needed
4. Double-click the instance to launch

### Verify

Open the fingerprint self-check page from the manager:

```
navigator.platform      macOS ✅
UA / UA-CH consistency  ✅
Timezone                America/New_York ✅
Canvas noise            injected ✅
WebGL renderer          Apple M2 ✅
WebRTC private IP leak  none ✅
```

All green means the spoof is active.

---

## Kernel patches

```bash
# apply all patches (idempotent)
python kernel/apply_patches.py C:\chromium\src

# check only, no writes
python kernel/apply_patches.py C:\chromium\src --check

# self-test (first apply + idempotency + anchors)
python kernel/test_patches.py
```

**93 patches**, covering fingerprint, proxy, sidebar, portable key and UI behaviour.

Design principles:

- **Anchor based** — each patch locates a unique code fragment, never a line number
- **Idempotent** — already-applied patches report `ALREADY`
- **Verifiable** — `--check` performs a zero-write comparison against a production tree
- **Asset backed** — large blocks (e.g. the sidebar) live in `kernel/sidebar/*.txt` and are injected verbatim, so the patch script cannot drift from the real source

Acceptance gates (defined in `kernel/CHROMIUM_VERSION`):

```bash
python kernel/test_patches.py
python tests/test_data_integrity.py
python kernel/apply_patches.py C:\chromium\src --check
python kernel/run_browser_regression.py
```

---

## Building

### Manager

```bash
dotnet publish app/KZManager.csproj \
  -c Release -r win-x64 --self-contained true \
  -p:PublishSingleFile=true \
  -p:IncludeNativeLibrariesForSelfExtract=true \
  -p:EnableCompressionInSingleFile=true \
  -p:DebugType=none \
  -o out
```

Produces `out/KZManager.exe`, about 60 MB, self-contained (no .NET needed on the target).

### Kernel

```bash
# 1. check out the matching source
#    158.0.8127.18 / 6449d9f9a6a906edff724b7b220199b3fde4615c

# 2. apply patches
python kernel/apply_patches.py C:\chromium\src

# 3. configure
gn gen out/Default --args="is_official_build=true symbol_level=0 proprietary_codecs=true ffmpeg_branding=Chrome is_component_build=false target_cpu=x64"

# 4. build
ninja -C out/Default chrome.dll chrome.exe
```

Full build takes roughly 3 hours (~51,000 ninja steps). Incremental UI-only rebuilds take 2–6 minutes.

```bash
python kernel/verify_build.py --dll out/Default/chrome.dll
```

---

## Repository layout

```
.
├── app/          WPF manager (C#)
├── kernel/       Chromium patch system (93 patches)
│   └── sidebar/  sidebar implementation, injected verbatim
├── scripts/      CLI tools: node proxy, snapshot, backup, sync, packaging
├── tools/        fingerprint self-check page, default-browser registration, migration
├── tests/        data integrity tests + regression probe
├── assets/       icons
├── examples/     sample configs
├── docs/         detailed documentation (Chinese)
├── 启动管理器.bat / 同步数据.bat / 账号同步.bat
```

See the Chinese README for a fully annotated tree.

---

## Configuration

### `manager.json` (global)

| Field | Meaning |
|---|---|
| `chromeExe` | path to the patched kernel |
| `order` | instance ordering |
| `theme` | `dark` / `light` |
| `proxyPrecheck` | exit-IP check before launch |
| `regionCheck` / `regionCheckBlock` | compare exit country with declared region; optionally block |
| `nativeProxyAuth` | use the kernel's native SOCKS5 auth |
| `uaProfile` | `default` / `chrome-157` / `iOS-safari` / `Android-chrome` |
| `defaultInstance` | fallback target for external links |

### `profile.json` (per instance)

| Field | Meaning |
|---|---|
| `name` | display name, also the window title |
| `seed` | fingerprint seed |
| `platform` | `macos` / `linux` / `windows` / `host` |
| `timezone` | IANA timezone |
| `lang` | e.g. `en-US` |
| `webrtc` | `public` / `strict` / `off` / custom IP |
| `proxy` | proxy endpoint |
| `tags` | color tags |
| `accounts` | bound platform accounts |

---

## FAQ

**Fingerprint does not change after launching?**
The kernel is not active. Make sure `chromeExe` points to the patched `chrome.exe`, not a stock Chrome.

**Logins keep getting lost?**
Check that the kernel version matches the profile data. Opening an instance with an unpatched kernel can reset the profile key. Take snapshots of instance data regularly.

**WebRTC still leaks the local IP?**
Switch WebRTC to `strict` or `off` in the instance settings and restart. The self-check page reports leaks directly.

**`anchor not found` while patching?**
Source version mismatch. Verify the commit hash against `kernel/CHROMIUM_VERSION`.

**How many instances can the manager handle?**
50+ concurrently in practice; memory is the limit (roughly 200–400 MB per instance).

---

## Disclaimer

This project is intended for managing your own accounts, environment isolation, automated testing, fingerprinting research and anti-fraud exercises.

Users are responsible for ensuring their use complies with local law and the terms of service of any site they interact with. The authors accept no liability for consequences arising from its use.

---

## License

[MIT](LICENSE)
