# E900V22C (S905L3A) 运行飞牛 FnNAS 改造记录

> 目标：把创维 E900V22C 电视盒子刷成飞牛 NAS，**解锁 4GB 运存**、**修复 eMMC 写入**、**启用板载 WiFi**
>
> 最终状态：**全部达成** ✅（5G 频段除外，见「已知限制」）

---

## 一、硬件实际规格（实测，非标称）

| 部件 | 实测结果 |
|---|---|
| SoC | Amlogic Meson G12A = **S905L3A**，4 核 Cortex-A53 @ 1.91GHz |
| 内存 | **物理 4GB**（可用 3.83GiB，顶部 169MiB 保留给 BL31/TEE） |
| eMMC | **SCA128, 116.5 GiB**（128GB），寿命 0~10%，健康 |
| WiFi | **紫光展锐 UWE5621DS**（chipid `0x56630001`），SDIO 接口，双频 2.4G+5G |
| 网口 | 百兆 (100Mb/s 全双工) |
| 系统 | Debian 12 bookworm，内核 `6.12.41-trim`，飞牛 FnNAS |

---

## 二、三个核心问题的根因与解法

### 问题 1：4GB 运存只认到 2GB

**根因**：飞牛官方 `meson-g12a-s905l3a-e900v22c.dtb` 的 `memory` 节点写的是 `2GB`，虽然机型数据库标注 `4+64G/128G`。

**关键坑**：`memory` 节点的 `reg` 属性必须是 **4 个 cell**：

```
根节点 #address-cells = <2>, #size-cells = <2>
reg = <地址高 地址低 长度高 长度低>          ← 必须 4 个 cell！
```

写成 3 个 cell（`<0x00 0x00 0xf5700000>`）会让内核越界读取属性末尾的垃圾值当内存长度 → **必然内核崩溃/黑屏**。

**正确写法（4 个 cell）**：

```
memory@0 {
    device_type = "memory";
    reg = <0x00 0x00 0x00 0xfe000000>;    /* 3.97GB，保留顶部 32MiB */
};
```

**实测边界**（逐个试出来的）：

| reg 值 | 内存 | 结果 |
|---|---|---|
| `0x00 0x00 0x00 0x80000000` | 2GB | ✅（原版） |
| `0x00 0x00 0x00 0xf5700000` | 3.83GB | ✅（原厂安卓 4G 包用的值） |
| `0x00 0x00 0x00 0xfe000000` | **3.97GB** | ✅ **当前使用，最大值** |
| `0x00 0x00 0x01 0x00` | 4.00GB | ❌ 黑屏（撞 BL31 保留区） |
| `0x00 0x00 0xf5700000` | — | ❌ 黑屏（**只有 3 个 cell，畸形 dtb**） |

> 原厂安卓 4G 包内 dtb 声明 `linux,usable-memory = <0x00 0xf5700000>`，同样保留顶部 169MiB。
> **结论：4GB 是物理容量，Linux 可用 3.83~3.97GB，这就是"4G 内存"的完整形态。**

---

### 问题 2：eMMC 完全无法写入（读正常，写全部 I/O error）

**现象**：Linux 下任意偏移写入都返回 `I/O error`，但读取全部正常；USB 烧录工具偶尔卡在 `DiskInitial`。

**排查结论**（排除法）：

| 检查项 | 结果 | 结论 |
|---|---|---|
| eMMC 寿命 `EXT_CSD_DEVICE_LIFE_TIME_EST` | `0x01` (0~10%) | 芯片健康 |
| `EXT_CSD_PRE_EOL_INFO` | `0x01` (Normal) | 未达预警 |
| `USER_WP` / `BOOT_WP` / 保护组 | 全 0 / 无保护 | 无写保护 |
| 内核 `force_ro` / `ro` | 0 / 0 | 内核层没锁 |
| `oflag=direct` 绕过页缓存 | 仍失败 | 非缓存问题 |

**根因：eMMC 在 HS200 高速模式（100MHz）下写通道不稳定。**

**解法：把 dtb 里 eMMC 节点降到 DDR52（50MHz）**

```dts
sd_emmc_c: mmc@ffe07000 {
    bus-width = <0x08>;
    cap-mmc-highspeed;
    max-frequency = <0x3197500>;   /* 52MHz，原为 0x5f5e100 = 100MHz */
    mmc-ddr-1_8v;                  /* 保留 DDR52 */
    /* 删除 mmc-hs200-1_8v */
};
```

**全时序档实测对比**（128MiB direct 顺序写 + 1024MiB direct 顺序读，含写入回读校验）：

| 模式 | 协商实际时钟 | 写 | 读 | I/O error | 结论 |
|---|---|---|---|---|---|
| HS200 @50MHz | 50 MHz | 41 | 44 MB/s | 0 | ✅ 可用但有损 |
| HS200 @75MHz | 71.4 MHz | 53 | 62 MB/s | 0 | ✅ 可用 |
| HS200 @100MHz | 100 MHz | **0** | 45 MB/s | 5 | ❌ 写通道崩溃 |
| HS400（回退至 HS200@100） | 100 MHz | **0** | 84 MB/s | 5 | ❌ 写通道崩溃 |
| **DDR52 @52MHz（生产）** | 50 MHz | **70** | **81 MB/s** | **0** | ✅ **最高稳定档** |

**结论：DDR52@52MHz 就是这颗 eMMC 在正常使用下的最高稳定速度。**
- HS200 虽支持，但 50/75MHz 下单沿带宽反而低于 DDR52；100MHz 起写通道 I/O error（板级信号完整性瓶颈，实测坐实）。
- HS400 变体协商时直接回退到 HS200@100MHz，同样写废。
- 原因：DDR52 是**双沿**传输（52MHz×2 = 理论 104MB/s），单沿 HS200 需 104MHz+ 才持平，而那正是写通道崩溃点。
- 读写均在 80/70 MB/s 量级，已接近 DDR52 理论带宽 104MB/s 的 2/3，是板级稳定上限。

---

### 问题 3：WiFi 完全不可用

**现象**：`/sys/bus/sdio/devices/mmc0:8800:1` 存在，但 `vendor/device/class` 全是 `0x0000`，`aic8800_bsp` 上电超时，无 `wlan0`。

**三层根因，逐一攻破：**

#### (a) dtb 缺 4 项关键配置

| 缺失项 | 作用 | 修法 |
|---|---|---|
| `pwm_ef` (pwm@19000) 被 `disabled` | 提供 **32.768kHz LPO 时钟**，芯片初始化的前提 | `status = "okay"` + 补 `pinctrl-0` / `clock-names` |
| `sdio-pwrseq` 缺延时 | 上电后需等 1 秒芯片才响应 | `post-power-on-delay-ms = <0x3e8>` |
| `sdio-pwrseq` 缺 `ext_clock` | 绑定 32K 时钟 | `clocks = <&wifi32k>; clock-names = "ext_clock";` |
| `mmc@ffe03000` 缺 `cap-sdio-irq` | SDIO 带内中断，驱动收固件响应用 | 加 `cap-sdio-irq;` |

dtb 完整改动（`wifi32k`、`pwm_ef`、`sdio-pwrseq`、`wifi@1` 四个节点）：

```dts
wifi32k: wifi32k {
    compatible = "pwm-clock";
    #clock-cells = <0x00>;
    clock-frequency = <0x8000>;              /* 32768 Hz */
    pwms = <&pwm_ef 0x00 0x7736 0x00>;
};

pwm_ef: pwm@19000 {
    compatible = "amlogic,meson-g12-pwm-v2", "amlogic,meson8-pwm-v2";
    reg = <0x00 0x19000 0x00 0x20>;
    clocks = <0x08>;
    #pwm-cells = <0x03>;
    status = "okay";                          /* 原为 disabled */
    pinctrl-0 = <&pwm_e_pins>;
    pinctrl-names = "default";
    clock-names = "clkin0";
};

sdio_pwrseq: sdio-pwrseq {
    compatible = "mmc-pwrseq-simple";
    reset-gpios = <0x32 0x47 0x01>;           /* GPIO 71 */
    post-power-on-delay-ms = <0x3e8>;         /* 1000ms */
    power-off-delay-us = <0x2710>;            /* 10000us */
    clocks = <&wifi32k>;
    clock-names = "ext_clock";
};

/* mmc@ffe03000 内 */
cap-sdio-irq;

/* aliases */
wifi = "/soc/mmc@ffe03000/wifi@1";
```

#### (b) 内核里根本没有 UWE5621DS 驱动

飞牛内核有 4486 个模块（`rtw88_*`、`rsi_*` 等一大堆 WiFi 驱动），**唯独没有展锐 UWE5621 系列**。

**解法：从源码编译**（源码仓库：[NullYing/UWE5621DS-WIFI-Driver-For-Armbian](https://github.com/NullYing/UWE5621DS-WIFI-Driver-For-Armbian)）

```bash
# 内核头文件已自带，无需额外安装
apt-get install -y build-essential

cd unisocwcn
make -C /lib/modules/$(uname -r)/build M=$PWD CC=gcc HOSTCC=gcc \
     CFG_AML_WIFI_DEVICE_UWE5621=y modules
# → uwe5621_bsp_sdio.ko

cd ../unisocwifi
UNISOC_BSP_INCLUDE=$PWD/../unisocwcn/include \
KBUILD_EXTRA_SYMBOLS=$PWD/../unisocwcn/Module.symvers \
make -C /lib/modules/$(uname -r)/build M=$PWD CC=gcc HOSTCC=gcc modules
# → sprdwl_ng.ko
```

> **内核 6.12 需要回退 3 处 6.13+ 的新 API**（仓库是按更新内核改的）：
> ```
> timer_delete_sync      → del_timer_sync
> timer_container_of     → from_timer
> timer_delete           → del_timer
> set_wiphy_params(wiphy, radio_idx, changed) → (wiphy, changed)
> ```

驱动产物：

| 模块 | 大小 | 作用 |
|---|---|---|
| `uwe5621_bsp_sdio.ko` | 378,424 B | BSP/总线层，负责芯片上电、固件下载 |
| `sprdwl_ng.ko` | **542,744 B** | WiFi 网络层（cfg80211），**已打 dev_addr 补丁**（md5 `85529523...`） |

#### (c) 固件与自动加载

```bash
# 固件（仓库已内嵌在 ko 中，但驱动仍会尝试读文件系统）
cp /lib/firmware/uwe5621ds/wcnmodem.bin   /lib/firmware/wcnmodem.bin
cp /lib/firmware/uwe5621ds/wifi_5663*.ini /lib/firmware/

# 开机自动加载
echo -e "cfg80211\nuwe5621_bsp_sdio\nsprdwl_ng" > /etc/modules-load.d/uwe5621.conf
echo -e "softdep sprdwl_ng pre: cfg80211\nsoftdep sprdwl_ng pre: uwe5621_bsp_sdio" \
     > /etc/modprobe.d/uwe5621.conf
```

#### (d) 驱动 dev_addr 直写 bug（导致 `wlan0` 永久 DOWN）

> 这一层是**最隐蔽**的：芯片、固件、驱动全部正常，`iw scan` 甚至能扫到 17 个 AP，
> 但网卡就是打不开。**必须看内核源码才能定位。**

**现象**：

```
wlan0: start set random mac: 0e:1b:0c:e3:63:03
wlan0: Current addr:  0e 1b 0c e3 63 03 ...
wlan0: Expected addr: a0 67 20 18 b5 3f ...
netdevice: wlan0: Incorrect netdev->dev_addr
WARNING: at net/core/dev_addr_lists.c:519 dev_addr_check+0xac/0x13c
         __dev_open+0x40/0x204
         do_setlink / rtnl_newlink
```

`ip link set wlan0 up` 无效，`carrier` 恒为 0，NetworkManager 报 `The Wi-Fi network could not be found`。

**根因**：内核 **6.11 起** `struct net_device` 的地址管理改版：

```c
/* include/linux/netdevice.h (6.12.41-trim) */
const unsigned char  *dev_addr;              /* ← 变成 const 指针 */
u8                   dev_addr_shadow[MAX_ADDR_LEN];
/* 字段注释：Copy of @dev_addr to catch direct writes. */
```

`dev_addr` 指向 rbtree 节点，`dev_addr_shadow` 是内核影子副本。**所有修改必须走
`dev_addr_set()` / `eth_hw_addr_set()`**，因为它会同时更新两者：

```c
void dev_addr_mod(...)          /* net/core/dev_addr_lists.c */
{
	dev_addr_check(dev);
	ha = container_of(dev->dev_addr, struct netdev_hw_addr, addr[0]);
	rb_erase(&ha->node, &dev->dev_addrs.tree);
	memcpy(&ha->addr[offset], addr, len);             /* rbtree */
	memcpy(&dev->dev_addr_shadow[offset], addr, len); /* shadow  ← 关键 */
	WARN_ON(__hw_addr_insert(...));
}
```

内核 `dev_addr_check()`（`dev_addr_lists.c:511`，与日志逐字对应）：

```c
void dev_addr_check(struct net_device *dev)
{
	if (!memcmp(dev->dev_addr, dev->dev_addr_shadow, MAX_ADDR_LEN))
		return;
	netdev_warn(dev, "Current addr:  %*ph\n", MAX_ADDR_LEN, dev->dev_addr);
	netdev_warn(dev, "Expected addr: %*ph\n", MAX_ADDR_LEN, dev->dev_addr_shadow);
	netdev_WARN(dev, "Incorrect netdev->dev_addr\n");
}
```

而驱动 `unisocwifi/main.c` 的 `sprdwl_set_mac()`（`.ndo_set_mac_address`）仍在直写：

```c
memcpy(dev->dev_addr, sa->sa_data, ETH_ALEN);   /* 947 行 */
memcpy(dev->dev_addr, vif->mac, ETH_ALEN);      /* 952 行，且 vif->mac 从未被赋值(恒为全0) */
```

**触发链**：NetworkManager 用**一次 netlink `do_setlink` 同时设 MAC + 起网卡**
→ 直写导致 rbtree 与 shadow 失配
→ `__dev_open` 调 `dev_addr_check` 失败
→ **网卡永远打不开**，连带 `sprdwl_event_scan_done error!` 刷屏。

> 编译器其实早就警告了，只是被忽略了：
> ```
> warning: passing argument 3 of 'sprdwl_set_mac_addr' discards 'const' qualifier
>   expected 'u8 *' but argument is of type 'const unsigned char *'
> ```

**修复**（2 行核心改动，见 `wifi-fix/sprdwl-devaddr-fix.patch`）：

```diff
 		if (!is_zero_ether_addr(sa->sa_data)) {
 			vif->has_rand_mac = true;
 			memcpy(vif->random_mac, sa->sa_data, ETH_ALEN);
-			memcpy(dev->dev_addr, sa->sa_data, ETH_ALEN);
+			dev->addr_len = ETH_ALEN;
+			eth_hw_addr_set(dev, sa->sa_data);
 		} else {
 			vif->has_rand_mac = false;
 			netdev_info(dev, "need clear random mac for sta/softap mode\n");
 			memset(vif->random_mac, 0, ETH_ALEN);
-			memcpy(dev->dev_addr, vif->mac, ETH_ALEN);
+			dev->addr_len = ETH_ALEN;
+			if (is_valid_ether_addr(vif->mac))
+				eth_hw_addr_set(dev, vif->mac);
+			else if (is_valid_ether_addr(vif->priv->mac_addr))
+				eth_hw_addr_set(dev, vif->priv->mac_addr);
 		}
```

**配套加固**（`/etc/NetworkManager/conf.d/99-wifi-mac.conf`）：

```ini
[device]
wifi.scan-rand-mac-address=no     # 禁止 NM 扫描时随机化 MAC
```

**效果对比**：

| 指标 | 修复前 | 修复后 |
|---|---|---|
| `wlan0` 状态 | DOWN / carrier 0 | **UP / carrier 1** |
| `dev_addr_check` 报错 | 每次联网必报 | **0 次** |
| `scan_done error` | 刷屏 | **0 次** |
| 连接 | NM 报找不到网络 | **connected，-44dBm，38ms，0%丢包** |

#### 成功标志（dmesg）

```
WCN: marlin_get_wcn_chipid: chipid: 0x56630001     ← 读到真实芯片 ID
WCN: marlin_probe ok!
WCN: then marlin start to download
WCN: marlin download finished and run ok           ← 固件下载成功
wifi ini path = /lib/firmware/wifi_56630001_3ant.ini
sprdwl:chip_model:0x2355  fw_std:0x13  fw_capa:0x120f4f
sprdwl:mac_addr:a0:67:20:18:b5:3f                  ← 真实 MAC
wlan0 出现
```

---

## 三、WiFi 连接（飞牛 NM 限制的绕过方法）

**问题**：`nmcli dev wifi connect` 报 `settings plugin does not support adding connections`
（飞牛锁定了 NM 的配置写入接口）

**解法：手工写 keyfile 配置文件，再用 `nmcli con up` 激活**

```bash
cat > /etc/NetworkManager/system-connections/wifi-1F.nmconnection <<'EOF'
[connection]
id=1F
type=wifi
autoconnect=true
autoconnect-priority=10

[wifi]
mode=infrastructure
ssid=1F

[wifi-security]
key-mgmt=wpa-psk
psk=你的密码

[ipv4]
method=auto

[ipv6]
method=auto
EOF
chmod 600 /etc/NetworkManager/system-connections/wifi-1F.nmconnection
nmcli con reload
nmcli con up 1F
```

**实测结果**：

```
wlan0  connected → 1F (2412MHz, 2.4G)
IP: 192.168.3.83/24 + IPv6 (240e:...)
signal: -45 dBm          tx bitrate: 104 Mbit/s VHT-MCS5 NSS2   (2x2 MIMO, 11ac)
外网: ping 223.5.5.5 → 0% 丢包, 33ms
```

> ⚠️ **注意**：这颗驱动**不支持连接断开后重连**（`nmcli con down` 后再 `up` 会进入异常状态，只能重启恢复）。
> 配置好开机自动连接后，**不要手工反复断开/连接**。

---

## 四、已知限制

### 1. 5G 频段无法关联

| 项目 | 状态 |
|---|---|
| 芯片 5G 能力 | ✅ 支持（`Band 2`，5180~5805MHz） |
| 5G 扫描 | ✅ 能扫到 `1F-5G` @5220MHz |
| 5G 关联 | ❌ `ssid-not-found`（NM 扫描结果里找不到） |

**原因**：内核法规域是 `country 00: DFS-UNSET`，把 5G 频段标为 `PASSIVE-SCAN`（禁止主动探测/发射）。
`iw reg set CN` 无效（驱动有自己的 `SprdDB` 法规数据库，会覆盖内核设置）。

**可尝试的方向**：
- 修改 `/lib/firmware/wifi_56630001_3ant.ini` 的 `[Section 8: Reg Domain]`
- 把 5G AP 的信道改到 **非 DFS 信道**（149/153/157/161/165，即 5745~5825MHz），这类信道在多数法规域下允许主动扫描

### 2. U 盘供电

盒子 USB 口供电有限，**U 盘接触不良会每 30 秒掉线一次**，导致根文件系统 I/O 错误。
- 解决：插紧 / 换 USB 口 / 用更强的电源适配器
- **根治：装进 eMMC**（eMMC 已验证可写）

---

## 五、文件清单

### dtb 变体（`dtb-variants/`）

| 文件 | 内存 | eMMC | WiFi |
|---|---|---|---|
| **`v8b-uwe5621ds-3.97g-ddr52.dtb`** | **3.97GB** | **DDR52** | **✅ 完整** |
| `v7-wifi-ddr52.dtb` | 3.97GB | DDR52 | PWM 修复版 |
| `v5-mem397.dtb` | 3.97GB | HS200 | — |
| `v5-mem400.dtb` | 4.00GB | HS200 | ❌ 黑屏 |
| `dtb-original-stock.dtb` | 2GB | HS200 | ❌ 原版 |
| `v5-spd*.dtb` | 3.83GB | 各速度档 | — |

### 驱动源码（`uwe5621/`）

`driver.tar.gz`（完整源码）、`wifi-driver-build.md`（23 个会话的完整踩坑记录）、`m401a.dts`（参考设备树）

### WiFi 深度修复（`wifi-fix/`）★

| 文件 | 说明 |
|---|---|
| `sprdwl_ng.ko` | **打补丁后的驱动**（542,744 B，md5 `85529523...`） |
| `uwe5621_bsp_sdio.ko` | BSP 模块（378,424 B，md5 `7ef01ef8...`） |
| `main.c.patched` / `main.c.original` | 源码前后对照 |
| `sprdwl-devaddr-fix.patch` | **2 行核心补丁**（`memcpy(dev_addr)` → `eth_hw_addr_set()`） |
| `README-WiFi修复.md` | 根因分析完整版 |

### 构建脚本（`work/`）

| 脚本 | 作用 |
|---|---|
| `build_v8b.py` | **生成最终 dtb**（3.97G + DDR52 + WiFi） |
| `build_driver*.sh` | 编译驱动模块 |
| `patch_driver.sh` | **给驱动打 dev_addr 补丁** |
| `rebuild_driver.sh` | **重新编译并安装 sprdwl_ng.ko** |
| `remote_emmc.sh` | eMMC 写入测试 |
| `final_check.sh` | 全硬件验证 |
| `backup_full.sh` / `archive_wifi_fix.sh` | 备份与归档 |

---

## 六、刷机 / 部署步骤（复现）

### 1. 制作 U 盘（balenaEtcher 写入官方镜像）

镜像：`fnnas_amlogic_s905l3a_k6.12.41_2026.06.25.img`

### 2. 替换 dtb

把 `v8b-uwe5621ds-3.97g-ddr52.dtb` 覆盖到 U 盘 BOOT 分区：
```
/dtb/amlogic/meson-g12a-s905l3a-e900v22c.dtb
```

**同时确保**（原版镜像的正确状态）：
- `u-boot.ext` **不存在**（内核 `text_offset=0x1080000` → `NEED_OVERLOAD=no`）
- `uEnv.txt` 必须是 **纯 LF 换行**

### 3. 编译并安装驱动

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

### 4. 配置 WiFi

见第三节的 keyfile 方法。

### 5. 重启

```bash
reboot
```

启动后应看到：内存 3.8Gi、eMMC 可写、`wlan0` 自动连上。

---

## 七、关键经验总结

1. **dtb 的 `reg` 属性 cell 数必须匹配父节点 `#address-cells`/`#size-cells`** —— 少写会越界读取，表现为随机崩溃/黑屏，极难排查
2. **eMMC 写入失败 ≠ eMMC 坏了** —— 先查 `EXT_CSD` 寿命/写保护位，再考虑降速（HS200 → DDR52）
3. **SDIO WiFi 芯片 `ID = 0000:0000` 是"未初始化"的正常状态** —— 它在 bootrom 模式等驱动下载固件，不要误判为硬件故障
4. **飞牛锁定了 NM 写接口**，但手工写 keyfile + `nmcli con up` 可用
5. **UWE5621DS 驱动不支持连接断开重连** —— 配置好自动连接后别乱动
6. **Amlogic `aml_autoscript` 机制**：`u-boot.ext` 只在 `text_offset != 0x1080000` 时才需要；多数新版内核不需要它
