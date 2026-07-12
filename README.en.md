# Gallery

Gallery is a local media library for fnOS. The backend is written in Rust and distributed as a native fnOS FPK package (no Docker required). It indexes the media directories you connect, partitions the library by artist, and organizes images, videos, and source files by tags and characters within each artist.

## Features

- **Browse and preview**: images, videos (with preview and direct playback), source files, text (`.txt`/`.md`/`.html`), and archives.
- **Organization**: the library is partitioned by artist — your folders *are* the artists. After selecting an artist, you can browse and filter within that artist's library by tags and characters.
- **Character recognition**: AI character recognition is on by default (OpenVINO on GPU, with CPU support). Artist recognition is off by default, since folders already define artists.
- **Duplicate detection and path monitoring**: detects duplicate files by content hash; watches for broken media paths and automatically reconnects unambiguous moves within the same artist, flagging uncertain cases for manual confirmation.
- **Archive planning**: gathers scattered content into an archive plan that executes only after confirmation (a backup is taken before execution). Auto-execution is off by default and triggers only after the switch is enabled and a full scan completes successfully.
- **Index and file location**: the index lives in SQLite. Media files always stay within your fnOS-authorized media directories; organization and archiving move files only inside those directories, never outside the authorized scope.

The service listens on port `8899` by default and **has no built-in authentication**. Use it only on a trusted LAN; if exposed to the public internet, add authentication at the fnOS or reverse-proxy layer first. Pinyin search is supported for tags and artists.

## Installation

Download the prebuilt FPK from [Releases](https://github.com/hczhr/gallery-archive/releases) and install it via the fnOS app installer. Current package name:

```text
gallery_1.0.175_x86_64.fpk
```

The source repository **does not include** compiled binaries, models, databases, or caches — obtain those from Releases or build them yourself.

## Building

One-shot build (compiles the Rust runtime, then packages the FPK):

```powershell
python tools/build_release.py --fnpack .\output\fnpack\fnpack-1.2.1-windows-amd64.exe
```

Rust runtime only:

```powershell
cargo build --release --locked --manifest-path rust/gallery_accel/Cargo.toml
```

## Project structure

| Path | Description |
| --- | --- |
| `rust/gallery_accel/` | Rust runtime and Axum API |
| `app/static/` | Web UI |
| `fnpack/` | fnOS package config and startup scripts |
| `tools/` | Tools to build Rust, package the FPK, and generate the public source tree |
