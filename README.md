# E900V22C (S905L3A) 4G RAM + WiFi Modded Build

> This repo is a modded FnNAS build made exclusively for the Skyworth
> **E900V22C (S905L3A, 4GB RAM)**. Based on `ophub/fnnas`, single board
> (`e900v22c`) only. Every Release carries exactly **2 attached files**.

## 1. What this fixes over stock images

Stock images have three problems on the E900V22C, all fixed here:

### 4G RAM seen as 2G

Stock dtb hardcodes a 2G `memory` node. This build uses **`0xf5700000`
(3927MiB usable)**, top 169MiB reserved for the BL31/TEE secure world.

> Warning: never change it to `0xfe000000` (3.97G). It looks like 143MiB more,
> but the kernel's 256MiB CMA then sits on the secure zone and core processes
> die on heavy copies. `free -m` showing about 3.7Gi is correct.

### eMMC writes fail

This eMMC's HS200 high-speed write path is unstable. This build clocks eMMC
down to **DDR52 50MHz** (about 81MB/s read, 70MB/s write, plenty for a
100Mbps NIC).

### No onboard WiFi driver

Unisoc UWE5621DS, shipped with dual modules plus four dtb fixes:

* `sprdwl_ng.ko` / `uwe5621_bsp_sdio.ko` (`vermagic = 6.12.41-trim`,
  with `dev_addr` + `conn-leak` patches)
* dtb: `wifi@1` node, `cap-sdio-irq`, `pwm_ef` 32k clock, `sdio-pwrseq` timing
* `99-wifi-mac.conf` (no scan-time MAC randomization),
  `99-uwe5621ds.conf` (autoload at boot)

## 2. Release contents

| File | Purpose |
|---|---|
| `fnnas_amlogic_e900v22c_k6.12.41_<date>.img.xz` | System image (about 1.6GB); flash to USB / TF card (16GB+) with balenaEtcher |
| `meson-g12a-s905l3a-e900v22c.dtb` | 4G dtb; can also replace the same file under `/boot/dtb/amlogic/` on an installed system |

## 3. Install

1. Flash USB / TF with balenaEtcher, plug into the box and power on
   (Ethernet and WiFi connect automatically).
2. Open `http://<box-IP>:5666`, create the admin account
   (first boot needs a few minutes for resize + init).
3. (Optional) Move off USB: `sudo fnnas-install -y` installs to eMMC unattended.
   Production installer: no board menu, reuses the running dtb, 4G-layout only,
   memory guard plus copy checks.

## 4. Online update policy

| Channel | Verdict |
|---|---|
| Trim app update from FnNAS web UI | Safe to apply (only replaces `/usr/trim`, keeps 4G/WiFi) |
| `apt update && apt upgrade` | Safe (stock repos carry no custom kernel) |
| `fnnas-update` kernel update | Blocked by default (a newer kernel overwrites the 4G dtb and invalidates the drivers). Same-version reinstall: `fnnas-update -k 6.12.41`; forcing anything else needs `--force-kernel-ota` at your own risk, then re-flash this image |

## 5. Known limits

* Prefer 2.4G WiFi (regulatory-domain limits on 5G); once connected, do not flap
  the connection (driver flaw, needs a reboot to recover).
* Kernel pinned to `6.12.41-trim`; any other kernel kills WiFi.
* Board telemetry `/etc/device_info/boot_board` reports `e900v22c`.

## 6. Build

Actions -> Build Image and 4G DTB -> Run workflow (or push an `e900v22c-*` tag),
single board, about 11 minutes. Blocking CI gates: `/bin|/sbin|/lib` must be
symlinks, dynamic loader present, both drivers present with matching `vermagic`,
in-image dtb boundary `0xf5700000`, OTA guard files present, or the build fails.

## 7. Credits

* [ophub/fnnas](https://github.com/ophub/fnnas) - FnNAS Amlogic port and build chain
* [7Ji/ampart](https://github.com/7Ji/ampart) - eMMC partition tool
* [KryptonLee/uwe5621ds-aml](https://github.com/KryptonLee/uwe5621ds-aml) - original UWE5621DS driver source
