# ⭐ FAST Service M3U Playlist Generator

![Build Status](https://github.com/BuddyChewChew/app-m3u-generator/actions/workflows/generate_playlists.yml/badge.svg)

This repository automatically generates M3U playlist files for various free ad-supported streaming television (FAST) services using a Python script and GitHub Actions. The playlists include embedded EPG (Electronic Program Guide) information via the `url-tvg` tag in the M3U header.

## 🟢 Service Status & Playlist URLs

| Service | Status | Region Support | Playlist URL (M3U) | EPG / Guide Source |
| :--- | :--- | :--- | :--- | :--- |
| **Pluto TV** | ✅ Online | Global (14+ Regions) | `plutotv_all.m3u` | `i.mjh.nz` (Auto-embedded) |
| **Plex TV** | ✅ Online | Global (8+ Regions) | `plex_all.m3u` | `i.mjh.nz` (Auto-embedded) |
| **Samsung TV Plus** | ✅ Online | Global (11+ Regions) | `samsungtvplus_all.m3u` | `i.mjh.nz` (Auto-embedded) |
| **Roku Channel** | ✅ Online | US / All | `roku_all.m3u` | `i.mjh.nz` (Auto-embedded) |
| **Tubi TV** | ✅ Online | US (Scraped) | `tubi_all.m3u` | `tubi_epg.xml` (Self-Generated) |
| **Stirr TV** | ❌ Offline | N/A | Removed | Service Discontinued |

---

## ▶️ How It Works

1. **Data Fetching:** A Python script (`generate_playlists.py`) fetches the latest channel data from reliable upstream sources (primarily [i.mjh.nz](https://i.mjh.nz)).
2. **M3U Generation:** The script parses the data and utilizes the `jmp2.uk` proxy to ensure Pluto and Samsung TV Plus streams remain functional in standard IPTV players.
3. **GitHub Action:** A scheduled workflow runs daily (03:00 UTC).
4. **The TiviMate Fix:** The workflow dynamically injects the full GitHub Raw URL into the Tubi M3U header. This ensures that players like TiviMate can find the `tubi_epg.xml` guide without manual configuration.

## ▶️ Services Included

### 🔹 Pluto TV
**File:** `plutotv_[region].m3u`
* **Regions:** ar, br, ca, cl, de, dk, es, fr, gb, it, mx, no, se, us, and all.

### 🔹 Plex TV
**File:** `plex_[region].m3u`
* **Regions:** au, ca, es, fr, gb, mx, nz, us, and all.

### 🔹 Samsung TV Plus
**File:** `samsungtvplus_[region].m3u`
* **Regions:** at, ca, ch, de, es, fr, gb, in, it, kr, us, and all.

### 🔹 Roku TV & Tubi
* **Roku:** `roku_all.m3u` (Full Roku Linear lineup)
* **Tubi:** `tubi_all.m3u` (Live TV scraper)

---

## ▶️ How to Use

The generated M3U files are located in the [`playlists/`](https://github.com/BuddyChewChew/app-m3u-generator/tree/main/playlists) directory.

**Direct URL Format:**
`https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/FILENAME.m3u`

*Example for Plex US:*
`https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/plex_us.m3u`

**To get the URL manually:**
1. Navigate to the `playlists/` folder.
2. Click on the desired `.m3u` file.
3. Click the **"Raw"** button.
4. Copy the URL from your browser's address bar.

## ▶️ Update Schedule

Playlists are automatically updated daily via GitHub Actions. Your IPTV player should periodically refresh the URL to get the latest channel updates.

## ▶️ Customization

If you want to modify the regions or script logic:
1. **Fork** this repository.
2. Modify `generate_playlists.py` or the `cron` schedule in the `.yml` file.
3. **Important:** Go to **Settings > Actions > General** and enable **"Read and write permissions"** under Workflow Permissions.

## ▶️ Disclaimer

* This repository aggregates publicly available channel information.
* Availability and stability of streams depend entirely on the original service providers.
* Ensure your use of these streams complies with the terms of service of the respective platforms.

Support these services by visiting their official sites:
[Pluto TV](https://pluto.tv), [Plex](https://www.plex.tv), [Samsung TV Plus](https://www.samsungtvplus.com), [Tubi TV](https://tubitv.com), [Roku](https://therokuchannel.roku.com/)
