# E900V22C（S905L3A）飞牛 FnNAS —— 4G 运存 + WiFi 定制固件

> 把创维 E900V22C 电视盒子刷成飞牛 FnNAS，解锁 **4GB 运存**、修复 **eMMC 写入**、启用板载 **WiFi**。
> 基于 [ophub/fnnas](https://github.com/ophub/fnnas) 官方 Amlogic 镜像定制。

---

## 特性

| 能力 | 状态 |
|---|---|
| 4GB 运存完整识别（可用 3.97GB） | ✅ |
| 板载 WiFi（紫光展锐 UWE5621DS，2.4G） | ✅ |
| eMMC 稳定写入（DDR52 @52MHz，写 70 / 读 81 MB/s） | ✅ |
| 5G 频段 | ⚠️ 受法规域限制（见下文） |

## 硬件规格（实测，非标称）

- SoC：Amlogic **S905L3A**，4 核 Cortex-A53
- 内存：物理 **4GB**（Linux 可用 3.97GB，顶部 32MB 保留给 BL31/TEE）
- eMMC：SCA128，116.5 GiB
- WiFi：紫光展锐 **UWE5621DS**（chipid `0x56630001`）
- 网口：百兆

---

## 发布产物（Release）

| 产物 | 说明 |
|---|---|
| `dtb-wifi-bootloader-collection.zip` | 全套 dtb 变体 + WiFi 驱动 `.ko` + 固件 + 补丁 + 部署脚本 |
| `fnnas-e900v22c-minbootloader.img` | 最小 Amlogic 烧录包（bootloader + dtb + platform.conf，4MB） |

> 完整 `.img` 请到 [ophub/fnnas Releases](https://github.com/ophub/fnnas/releases) 下载官方 `s905l3a` 镜像，再用本仓库的 dtb + 驱动覆盖部署。

---

## 快速开始（U 盘部署）

### 1. 制作 U 盘

用 **balenaEtcher**（整盘 DD 模式）把官方 `fnnas_amlogic_s905l3a_k6.12.41_*.img` 写入 U 盘。

### 2. 替换 dtb（核心定制）

把生产版 dtb 覆盖到 U 盘 BOOT 分区：

```bash
# 生产版 = dtb-variants/v8b-uwe5621ds-3.97g-ddr52.dtb
cp v8b-uwe5621ds-3.97g-ddr52.dtb                        \
   ${BOOT}/dtb/amlogic/meson-g12a-s905l3a-e900v22c.dtb
```

同时确保：
- BOOT 分区根目录**不保留** `u-boot.ext`（本设备 `NEED_OVERLOAD=no`）
- `uEnv.txt` 保持**纯 LF 换行**、FDT 指向 `meson-g12a-s905l3a-e900v22c.dtb`

### 3. 安装 WiFi 驱动（首次需编译）

```bash
cd uwe5621 && tar xzf driver.tar.gz && cd unisocwcn
make -C /lib/modules/$(uname -r)/build M=$PWD CFG_AML_WIFI_DEVICE_UWE5621=y modules
cd ../unisocwifi
UNISOC_BSP_INCLUDE=$PWD/../unisocwcn/include \
KBUILD_EXTRA_SYMBOLS=$PWD/../unisocwcn/Module.symvers \
make -C /lib/modules/$(uname -r)/build M=$PWD modules

mkdir -p /lib/modules/$(uname -r)/extra
cp ../unisocwcn/uwe5621_bsp_sdio.ko ../unisocwifi/sprdwl_ng.ko /lib/modules/$(uname -r)/extra/
depmod -a
cp /lib/firmware/uwe5621ds/wcnmodem.bin /lib/firmware/
cp /lib/firmware/uwe5621ds/wifi_5663*.ini /lib/firmware/
echo -e "cfg80211\nuwe5621_bsp_sdio\nsprdwl_ng" > /etc/modules-load.d/uwe5621.conf
```

> 已含 dev_addr 修复补丁（内核 6.11+ 必须，否则 `wlan0` 永远 DOWN），见 `wifi-fix/sprdwl-devaddr-fix.patch`。

### 4. 写入 eMMC

```bash
fnnas-install
# 选 304 (e900v22c)，rootfs 分区建议直接回车扩满整盘
```

装完 `poweroff`，拔 U 盘重新上电。

---

## 本次定制的根因与解法

### 问题 1：4GB 只认到 2GB

官方 `e900v22c.dtb` 的 `memory` 节点写死 2GB，且 `reg` 必须是 **4 个 cell**：

```dts
memory@0 {
    device_type = "memory";
    reg = <0x00 0x00 0x00 0xfe000000>;   /* 3.97GB，保留顶部 32MB */
};
```

3 个 cell（`<0x00 0x00 0xf5700000>`）会让内核把属性末尾垃圾值当内存长度 → 必黑屏。

### 问题 2：eMMC 无法写入

eMMC 在 **HS200@100MHz** 写通道不稳定（板级信号完整性瓶颈）。降到 **DDR52**：

```dts
mmc@ffe07000 {           /* sd_emmc_c */
    bus-width = <0x08>;
    cap-mmc-highspeed;
    max-frequency = <0x3197500>;   /* 52MHz，原 100MHz */
    mmc-ddr-1_8v;
    /* 删除 mmc-hs200-1_8v */
};
```

DDR52 是**双沿**传输，52MHz×2 ≈ 104MB/s 理论带宽，实测写 70 / 读 81 MB/s，是这颗 eMMC 的最高稳定档。

### 问题 3：WiFi 不可用（三层根因）

1. **dtb 缺 4 项**：`pwm_ef`（32k LPO 时钟）被 disabled、`sdio-pwrseq` 缺延时/时钟、`mmc@ffe03000` 缺 `cap-sdio-irq`。
2. **内核无驱动**：飞牛内核没有展锐 UWE5621 驱动，需从源码编译（仓库 `uwe5621/`）。
3. **dev_addr 直写 bug**（最隐蔽）：内核 6.11 起 `dev_addr` 变为 const 指针 + `dev_addr_shadow` 影子副本，所有修改必须走 `eth_hw_addr_set()`。驱动仍 `memcpy(dev_addr,...)` 直写 → rbtree 与 shadow 失配 → `wlan0` 永久 DOWN。补丁见 `wifi-fix/sprdwl-devaddr-fix.patch`。

---

## 仓库结构

```
dtb-variants/   所有 dtb 变体（生产版 = v8b-uwe5621ds-3.97g-ddr52.dtb）
dtb/            官方原版 dtb
out/            成品 dtb / uEnv.txt / 最小烧录包
wifi-fix/       编译产物 .ko + 补丁
uwe5621/        驱动源码（tar.gz）
写入U盘/        Windows 部署脚本（Deploy-ToUsb.ps1 等）
tools/          Amlogic 烧录包工具
work/           复现脚本（build_v8b.py 生成最终 dtb 等）
model_database.conf  ophub 机型-固件对应表
```

---

## 已知限制

- **5G 频段无法关联**：内核法规域 `country 00` 把 5G 标为 passive-scan。可尝试把 5G AP 改到非 DFS 信道（149~165），或改 `wifi_56630001_3ant.ini` 的 Reg Domain。

---

## License

**GPL-2.0**（本仓库定制部分）。基于 [ophub/fnnas](https://github.com/ophub/fnnas) 与内核/驱动生态，遵守各自许可证。见 [LICENSE](LICENSE)。

WiFi 驱动源码来自 [NullYing/UWE5621DS-WIFI-Driver-For-Armbian](https://github.com/NullYing/UWE5621DS-WIFI-Driver-For-Armbian)（GPL 家族许可）。

## 致谢

[ophub/fnnas](https://github.com/ophub/fnnas) · [7Ji/ampart](https://github.com/7Ji/ampart) · [superna9999/pyamlboot](https://github.com/superna9999/pyamlboot) · [NullYing/UWE5621DS-WIFI-Driver-For-Armbian](https://github.com/NullYing/UWE5621DS-WIFI-Driver-For-Armbian)