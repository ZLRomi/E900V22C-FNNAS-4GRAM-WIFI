# UWE5621DS / UWE5622 WiFi 驱动 — Amlogic mainline 移植版

***经过AI多轮迭代和测试，该驱动目前在连接wifi上还有问题，详细原因请参考 wifi-driver-build.md 部分驱动文件已经传到Release；由于主板坏了，没有硬件继续测试，进度在此中断了***
 
把 Unisoc(展锐) **UWE5621DS / UWE5622** SDIO WiFi+BT 芯片的厂商驱动移植到 **mainline Linux 内核**（`meson-gx-mmc` 主控），并修复了在主线内核上 cmd 响应丢失、`wlan0` 无法注册的问题。

目标平台：**魔百盒 UNT431A**（Amlogic **S905L3A**，UWE5621DS 模组），运行 [ophub/amlogic-s9xxx-armbian](https://github.com/ophub/amlogic-s9xxx-armbian) 的 mainline 内核。驱动本身与 SoC 无强耦合，理论上可移植到其它跑 mainline `meson-gx-mmc` 的 Amlogic 板子。

经过验证的系统镜像（armbian 固件地址 <https://github.com/ophub/amlogic-s9xxx-armbian>）：

```
Armbian_26.05.0_amlogic_s905l3a_noble_6.18.29_server_2026.05.15.img.gz
```

即 Armbian 26.05.0（Ubuntu Noble 基底）、内核 `6.18.29`、s905l3a、aarch64。

> 📦 **刷机（装 mainline/armbian 系统）教程参考**：<https://www.kejiwanjia.net/jiaocheng/129177.html>
> 本仓库只负责"系统装好之后让 WiFi 能用"；如何给 UNT431A 刷入 ophub/armbian 系统请先看上面的教程。

> 这是一个社区移植项目。原始代码版权归 Spreadtrum/Unisoc 所有，按 **GPL-2.0** 授权（见各源文件头部）。本仓库的移植/修复部分同样以 GPL-2.0 发布。

> ⚠️ **必须加载本仓库提供的新设备树（DTS/DTB）**，否则驱动无法工作。
> 默认 mainline/ophub 的 dtb 既没有为 SDIO 打开 inband 中断能力，也没有 32.768 kHz LPO 时钟和 `/wifi` 电源节点，光 `insmod` 模块是起不来的。
> 设备树文件见 [`m401a/`](m401a/)，部署步骤见下文 [安装 → 2. 设备树（必须）](#2-设备树必须)。

---

## 目录结构

```
.
├── unisocwcn/        # BSP / WCN 通用层（SDIO 收发、固件下载、sleep、log、平台 stub）
│   ├── sdio/         #   SDIO HAL（核心修复 sdiohal_rx.c 在这里）
│   ├── sleep/        #   睡眠管理（slp_mgr.c 禁用 CP2 睡眠）
│   ├── platform/     #   平台适配 + Amlogic mainline stub（aml_stubs.c）
│   ├── boot/ fw/ log/ include/ ...
│   └── Makefile      #   产出 <chipid>_bsp_sdio.ko（默认 uwe5621_bsp_sdio.ko）
├── unisocwifi/       # WiFi 网络驱动（cfg80211 / 收发 / 命令），产出 sprdwl_ng.ko
├── tty-sdio/         # 基于 SDIO 的 tty（BT 等）
├── m401a/            # 本移植的板级文件（必须使用，见下文）
│   ├── m401a_uwe5621ds_wifi.dts   # 设备树源（SDIO caps + 32K LPO + wifi 节点）
│   ├── m401a_uwe5621ds_wifi.dtb   # 编译好的设备树二进制（UNT431A 可直接用；文件名沿用早期 m401a 代号）
│   └── loadwifi.sh                # 加载脚本
├── Makefile / Kconfig
└── README.md
```

---

## 编译

需要目标板对应的内核头文件 / build 目录（`/lib/modules/$(uname -r)/build`）。在板子上原生编译（aarch64）：

```bash
cd uwe5622_driver
make -C /lib/modules/$(uname -r)/build M=$PWD ARCH=arm64 \
    CONFIG_UWE5622=m CONFIG_SPRDWL_NG=m CONFIG_UNISOC_WIFI=m \
    CC=gcc modules
```

产出：

- `unisocwcn/uwe5621_bsp_sdio.ko` — BSP/WCN 层
- `unisocwifi/sprdwl_ng.ko` — WiFi 网络驱动

> 内核若用 `aarch64-none-linux-gnu-gcc` 构建，本机用系统 `gcc` 编模块会有一句 "compiler differs" 的告警，可忽略；如需严格一致可装对应 cross toolchain 并加 `CROSS_COMPILE=aarch64-none-linux-gnu-`。

只改了 BSP 层时，单独重编 `unisocwcn` 即可，`sprdwl_ng.ko` 无需重编。

---

## 安装 / 加载

### 1. 固件

把展锐固件与校准文件放到内核 `request_firmware` 能找到的位置：

```
/lib/firmware/uwe5622/wcnmodem.bin           # CP2 固件（MARLIN3E，本项目用 W21.03.3）
/lib/firmware/wifi_56630001_3ant.ini         # 校准 ini（按模组实际天线/型号）
```

> 固件/ini 属厂商二进制，不随本仓库分发，请从对应 BSP 包获取。

### 2. 设备树（必须）

**这一步不能省。** 默认 mainline/ophub 的设备树无法让本驱动工作——必须改用本仓库 [`m401a/`](m401a/) 里的设备树。仓库同时提供了源文件和编译好的二进制：

- `m401a/m401a_uwe5621ds_wifi.dts` — 设备树源（可读、可改）
- `m401a/m401a_uwe5621ds_wifi.dtb` — 已编译好的二进制（UNT431A 可直接用）

> 文件/目录名里的 `m401a` 是早期对该盒子的代号，实际机型为**魔百盒 UNT431A（S905L3A）**，沿用旧名只是为了不破坏已验证的工件。

相比默认 dtb，关键差异（缺一不可）：

- SDIO（`mmc@ffe07000`）打开 SDIO 中断能力（`MMC_CAP_SDIO_IRQ`，原生 inband IRQ）——驱动靠它收 fw 响应；
- 32.768 kHz LPO 时钟供给（本项目用 PWM 输出，详见 dts 内 `pwm`/`wifi32k` 节点）——没有它 CP2 起不来；
- `/wifi` 节点（电源等）。OOB IRQ 在本板 mainline pinctrl 下拿不到，驱动已不依赖它。

部署（UNT431A 直接用预编译 dtb；其它板子先按需改 dts 再 `dtc` 编译）：

```bash
# A) 直接用预编译好的（UNT431A / S905L3A）
sudo cp m401a/m401a_uwe5621ds_wifi.dtb /boot/dtb/amlogic/

# B) 或自行从源编译
dtc -I dts -O dtb -o m401a_uwe5621ds_wifi.dtb m401a/m401a_uwe5621ds_wifi.dts
sudo cp m401a_uwe5621ds_wifi.dtb /boot/dtb/amlogic/
```

然后在引导配置里指向该 dtb（ophub/armbian 一般改 `/boot/uEnv.txt` 的 `FDT=` 行），**重启后生效**。先换 dts 重启，再 `insmod` 模块。

### 3. 加载模块

```bash
sudo ./m401a/loadwifi.sh
# 等价于：
#   modprobe cfg80211
#   insmod unisocwcn/uwe5621_bsp_sdio.ko
#   insmod unisocwifi/sprdwl_ng.ko
```

### 4. 验证

```bash
ip -br link show wlan0
sudo ip link set wlan0 up
sudo iw dev wlan0 scan | grep -c '^BSS'   # 应能扫到周边 AP
```

> 注意：`rmmod` 在部分情况下无法干净卸载本驱动，需要重启（`sudo reboot`）后重新加载。

---

## 已知问题 / TODO

- 开机偶发一次**非致命**的 `didn't get CP2 version` + `get_fw_info TLV check failed`：是最早一两条命令在 fw/总线刚就绪窗口内丢了响应，驱动用默认 fw 信息继续，扫描正常。可在 `sprdwl_get_cp2_version` 失败时加重试来根除。
- 尚未做**实际关联 + DHCP** 的端到端联网验证（缺测试 AP 凭据），扫描已稳定，连接预期可用。
- BT（`tty-sdio`）未单独验证。
- OOB（带外）GPIO 中断在本板 mainline pinctrl 下不可用，驱动改用 inband SDIO IRQ；睡眠握手随之禁用。

---

## 致谢与许可

- 原始驱动：Spreadtrum / Unisoc，GPL-2.0（见源文件版权头）。
- 上游 fork 起点：[`SUISHUI/uwe5622_driver`](https://github.com/SUISHUI/uwe5622_driver)。
- 本仓库的 mainline / Amlogic G12A 移植与 SDMA-trailer 修复以 **GPL-2.0** 发布。

欢迎 issue / PR，尤其是其它 Amlogic 板子上的适配反馈。
