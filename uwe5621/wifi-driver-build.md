# M401A (S905L3A) UWE5621DS WiFi 驱动构建与部署流程

## 0. 环境基线

| 项目 | 值 |
| --- | --- |
| 硬件 | Amlogic S905L3A（魔百盒 UNT431A；早期代号 M401A，dts/dtb 文件名沿用旧名） |
| 系统 | Armbian OS 26.05.0 (Ubuntu 24.04 noble) |
| 内核 | 6.18.29-ophub (aarch64) |
| 内核编译器 | aarch64-none-linux-gnu-gcc 15.2.1 |
| 主机编译器 | 必须使用 `gcc-15`（默认 `gcc` 是 13.3，不支持 `-fmin-function-alignment=4`） |
| WiFi 模组 | Unisoc UWE5621DS（SDIO，chipid `0x56630001`） |
| 驱动源 | `~/uwe5622_driver/`（从 linux-orangepi 6.6/6.11 移植版本） |
| 备用驱动 | `~/uwe5621ds-aml/`（4.9 内核版本，仅用于其固件配置文件） |

## 1. DTS 现状

`~/meson-g12a-s905l3a-m401a.dts` 已经正确配置：

- `sd@ffe03000`：SDIO 控制器 `status="okay"`、`non-removable`、`mmc-pwrseq=<wifi-pwrseq>`、`bus-width=4`、`max-frequency=50MHz`、`keep-power-in-suspend`
- `wifi-pwrseq`：`compatible="mmc-pwrseq-simple"`、`reset-gpios` 指向 WiFi reset 引脚、`post-power-on-delay-ms=1000`
- `wifi@1`：`compatible="uwcnmodem,we5621ds"`（仅作标识，驱动不实际匹配此 compatible）

**DTS 不需要 `unisoc,uwe_bsp` 节点**——驱动通过 `platform_device_register()` 自注册（未启用 `CONFIG_WCN_PARSE_DTS` 路径），不依赖 DTS。

验证：上电后 dmesg 应看到 `mmc0: new high speed SDIO card at address 8800`。

## 2. 驱动选择策略

- `uwe5621ds-aml`：基于内核 4.9，到 6.18 跨度过大，**不用**。
- `uwe5622_driver`：基于内核 6.6/6.11，**主选**，使用 `CFG_AML_WIFI_DEVICE_UWE5621=y` 配置（不是 5622）。

理由：
1. 5621 模式启用 `CONFIG_WCN_DOWNLOAD_FIRMWARE_FROM_HEX`，固件嵌入 `.ko` 内部（来自 `unisocwcn/fw/wcnmodem.bin.hex`），无需 `request_firmware`。
2. AML 5621 路径**没有**启用 `CONFIG_WCN_PARSE_DTS`，走 `platform_device_register()` 自注册，无需修改 DTS。
3. 与硬件 `0x56630001` chipid 匹配，固件配置文件名匹配 `wifi_56630001_<N>ant.ini`。

## 3. 必需的打补丁列表

### 3.1 Amlogic BSP 私有头/函数 stub

主线/Armbian 内核没有 `<linux/amlogic/aml_gpio_consumer.h>` 和相关私有函数。

新建 `~/uwe5622_driver/unisocwcn/include/linux/amlogic/aml_gpio_consumer.h`：

```c
#ifndef _AML_GPIO_CONSUMER_STUB_H_
#define _AML_GPIO_CONSUMER_STUB_H_

#include <linux/mmc/host.h>

#ifndef GPIO_IRQ_HIGH
#define GPIO_IRQ_HIGH 0
#endif
#ifndef GPIO_IRQ_LOW
#define GPIO_IRQ_LOW  1
#endif

/* mmc_power_save_host / mmc_power_restore_host 在 5.7+ 被移除，
 * 这里用 no-op 替代——真正上电由 DTS 的 wifi-pwrseq 完成。 */
static inline void mmc_power_save_host(struct mmc_host *host) { (void)host; }
static inline void mmc_power_restore_host(struct mmc_host *host) { (void)host; }

#endif
```

新建 `~/uwe5622_driver/unisocwcn/platform/aml_stubs.c`：

```c
#include <linux/module.h>
#include <linux/kernel.h>

int wifi_irq_num(void)        { return -1; }
int wifi_irq_trigger_level(void) { return 0; /* GPIO_IRQ_HIGH */ }
void extern_wifi_set_enable(int is_on) { (void)is_on; }
void extern_bt_set_enable(int is_on)   { (void)is_on; }
void sdio_reinit(void) { }
void sdio_clk_always_on(int on) { (void)on; }
void sdio_set_max_reqsz(unsigned int size) { (void)size; }
```

`ccflags-y += -I$(src)/include/` 已存在于 `unisocwcn/Makefile`，所以 stub 头会被自动找到。

### 3.2 修改 `~/uwe5622_driver/unisocwcn/Makefile`

a) 把 `aml_stubs.o` 加入 obj 列表（约第 372 行）：

```makefile
$(MODULE_NAME)-y += wcn_bus.o \
            ...（原有项目保持不变）...
            platform/loopcheck.o \
            platform/aml_stubs.o
```

b) 在 `ifeq ($(CFG_AML_WIFI_DEVICE_UWE5621),y)` 块（约第 222 行）中，启用 SDIO inband 中断：

```makefile
ccflags-y += -DCONFIG_SDIO_INBAND_INT
```

这是关键。不启用 INBAND_INT 时，驱动默认走外置 GPIO IRQ 模式（`SDIOHAL_RX_EXTERNAL_IRQ`），但我们的 stub `wifi_irq_num()` 返回 -1，必然失败（dmesg 报 `sdiohal err:request irq err gpio is -1`）。
启用 INBAND_INT 后驱动用 SDIO 总线 CCCR 内中断，**无需外置 GPIO IRQ**。

### 3.3 内核 API 变化批量替换

6.11→6.18 间内核做了一些 timer / sched / mmc / power API 改名，必须修补：

```bash
cd ~/uwe5622_driver

# wakeup_source: 旧 create+add / remove+destroy 拆分被 register/unregister 取代
for f in unisocwcn/platform/wcn_txrx.c unisocwcn/sleep/sdio_int.c \
         unisocwcn/sdio/sdiohal_common.c tty-sdio/lpm.c; do
  sed -i 's/wakeup_source_create(\(.*\));/wakeup_source_register(NULL, \1);/g;
          /wakeup_source_add(/d;
          /wakeup_source_remove(/d;
          s/wakeup_source_destroy(\(.*\));/wakeup_source_unregister(\1);/g' "$f"
done

# timer API：del_timer_sync→timer_delete_sync, from_timer→timer_container_of, del_timer→timer_delete
find . -name '*.c' -o -name '*.h' | xargs sed -i \
  -e 's/\bdel_timer_sync\b/timer_delete_sync/g' \
  -e 's/\bfrom_timer\b/timer_container_of/g' \
  -e 's/\bdel_timer\b/timer_delete/g'

# sched_setscheduler 在新内核未导出给模块；改用 sched_set_fifo
for f in unisocwcn/sdio/sdiohal_rx.c unisocwcn/sdio/sdiohal_tx.c; do
  sed -i 's|sched_setscheduler(current, SCHED_FIFO, \&param);|sched_set_fifo(current);|g' "$f"
done

# cfg80211 ops：set_wiphy_params 在 6.13+ 加了 radio_idx 参数
sed -i 's|static int sprdwl_cfg80211_set_wiphy_params(struct wiphy \*wiphy, u32 changed)|static int sprdwl_cfg80211_set_wiphy_params(struct wiphy *wiphy, int radio_idx, u32 changed)|' \
  unisocwifi/cfg80211.c
```

## 4. 编译命令

务必使用 `gcc-15`（内核是它编出来的，gcc-13 编出来的模块加载时会报版本不匹配，且 gcc-13 不支持 `-fmin-function-alignment=4`）。

### 4.1 编译 unisocwcn（BSP 模块）

```bash
cd ~/uwe5622_driver/unisocwcn
make -C /lib/modules/$(uname -r)/build M=$PWD \
     CC=gcc-15 HOSTCC=gcc-15 \
     CFG_AML_WIFI_DEVICE_UWE5621=y modules
# 产物：uwe5621_bsp_sdio.ko
```

### 4.2 编译 unisocwifi（WLAN 模块）

需要把 unisocwcn 的 `Module.symvers` 作为外部符号源：

```bash
cd ~/uwe5622_driver/unisocwifi
rm -f Module.symvers
UNISOC_BSP_INCLUDE=$HOME/uwe5622_driver/unisocwcn/include \
KBUILD_EXTRA_SYMBOLS=$HOME/uwe5622_driver/unisocwcn/Module.symvers \
  make -C /lib/modules/$(uname -r)/build M=$PWD \
       CC=gcc-15 HOSTCC=gcc-15 modules
# 产物：sprdwl_ng.ko
```

## 5. 固件部署

`/lib/firmware/uwe5621ds/` 一般预先就有（来自 Armbian 原始镜像）。如果没有，从 `~/uwe5621ds-aml/` 取以下文件：

```bash
sudo mkdir -p /lib/firmware/uwe5621ds
sudo cp ~/uwe5621ds-aml/wifi_56630001_3ant.ini /lib/firmware/uwe5621ds/
sudo cp ~/uwe5621ds-aml/wifimac.txt /lib/firmware/uwe5621ds/

# 同时 sprdwl_ng 默认从 /lib/firmware 读 wifi_xxx.ini（WIFI_BOARD_CFG_PATH 未自定义时）
sudo cp ~/uwe5621ds-aml/wifi_56630001_3ant.ini /lib/firmware/
```

驱动会按 `wifi_<chipid>_<ant>ant.ini` 模板查找配置，例如 `wifi_56630001_3ant.ini`。

固件 `wcnmodem.bin` 已通过 `CONFIG_WCN_DOWNLOAD_FIRMWARE_FROM_HEX` 嵌入模块二进制，**不需要**再放 `wcnmodem.bin` 到 `/lib/firmware/`。

## 6. 模块加载

```bash
sudo insmod ~/uwe5622_driver/unisocwcn/uwe5621_bsp_sdio.ko
sudo insmod ~/uwe5622_driver/unisocwifi/sprdwl_ng.ko
dmesg | tail -100
ip link
```

## 7. 验证 checklist

通过的标志逐级出现：

1. `mmc0: new high speed SDIO card at address 8800` — DTS + pwrseq 工作正常（启动早期就有）
2. `WCN: marlin_probe ok!` — platform driver 注册成功
3. `WCN: start_marlin [MARLIN_WIFI]` 后跟 `sdiohal_probe: ..., clock=50000000` — SDIO 功能驱动绑定
4. `WCN: marlin_get_wcn_chipid: chipid: 0x56630001` — SDIO 通信正常，可读寄存器
5. `sprdwl_sync_version` 成功返回（dmesg 中应有版本号），而不是 `wait_for_completion_timeout` — 固件已下载并启动
6. `ip link` 出现 `wlan0`

## 8. 已知问题 / 调试笔记

- **`wait scan card time out` / `sprdwl_sync_version` 超时**：通常是 IRQ 模式选错（GPIO 模式 stub IRQ 返回 -1），按 §3.2 b) 启用 `CONFIG_SDIO_INBAND_INT` 后修复。
- **`sched_setscheduler undefined`**：见 §3.3 sched 部分。
- **`fmin-function-alignment=4 unrecognized`**：用 gcc-13 编译；必须 `CC=gcc-15`。
- **`aml_gpio_consumer.h: No such file`**：未应用 §3.1 stub。
- **rmmod 卡住**：sprdwl_ng probe 失败后驱动状态异常无法 rmmod，需 `sudo reboot`。
- **重启后 sshd 起来慢**：本次观察到 reboot 后 SSH 长时间拒绝连接。如果发生，物理断电重启即可。

## 9. 开机自动加载（验证通过后）

```bash
sudo mkdir -p /lib/modules/$(uname -r)/extra
sudo cp ~/uwe5622_driver/unisocwcn/uwe5621_bsp_sdio.ko \
        ~/uwe5622_driver/unisocwifi/sprdwl_ng.ko \
        /lib/modules/$(uname -r)/extra/
sudo depmod -a
echo -e "uwe5621_bsp_sdio\nsprdwl_ng" | sudo tee /etc/modules-load.d/uwe5621.conf
```

## 10. 一键重建脚本（参考）

把所有修改打包成可重放脚本：

```bash
#!/usr/bin/env bash
set -e
cd ~/uwe5622_driver

# 1) stub 头和函数
mkdir -p unisocwcn/include/linux/amlogic
cat > unisocwcn/include/linux/amlogic/aml_gpio_consumer.h <<'EOF'
#ifndef _AML_GPIO_CONSUMER_STUB_H_
#define _AML_GPIO_CONSUMER_STUB_H_
#include <linux/mmc/host.h>
#ifndef GPIO_IRQ_HIGH
#define GPIO_IRQ_HIGH 0
#endif
#ifndef GPIO_IRQ_LOW
#define GPIO_IRQ_LOW  1
#endif
static inline void mmc_power_save_host(struct mmc_host *host) { (void)host; }
static inline void mmc_power_restore_host(struct mmc_host *host) { (void)host; }
#endif
EOF

cat > unisocwcn/platform/aml_stubs.c <<'EOF'
#include <linux/module.h>
#include <linux/kernel.h>
int wifi_irq_num(void)        { return -1; }
int wifi_irq_trigger_level(void) { return 0; }
void extern_wifi_set_enable(int is_on) { (void)is_on; }
void extern_bt_set_enable(int is_on)   { (void)is_on; }
void sdio_reinit(void) { }
void sdio_clk_always_on(int on) { (void)on; }
void sdio_set_max_reqsz(unsigned int size) { (void)size; }
EOF

# 2) Makefile：加入 aml_stubs.o 和 INBAND_INT
grep -q 'platform/aml_stubs.o' unisocwcn/Makefile || \
  sed -i 's|platform/loopcheck.o|platform/loopcheck.o \\\n\t\t\tplatform/aml_stubs.o|' \
    unisocwcn/Makefile
sed -i '/ifeq (\$(CFG_AML_WIFI_DEVICE_UWE5621),y)/,/^endif/ \
  {s|# ccflags-y += -DCONFIG_SDIO_INBAND_INT|ccflags-y += -DCONFIG_SDIO_INBAND_INT|}' \
    unisocwcn/Makefile

# 3) 内核 API 替换
for f in unisocwcn/platform/wcn_txrx.c unisocwcn/sleep/sdio_int.c \
         unisocwcn/sdio/sdiohal_common.c tty-sdio/lpm.c; do
  sed -i 's/wakeup_source_create(\(.*\));/wakeup_source_register(NULL, \1);/g;
          /wakeup_source_add(/d;
          /wakeup_source_remove(/d;
          s/wakeup_source_destroy(\(.*\));/wakeup_source_unregister(\1);/g' "$f"
done
find . -name '*.c' -o -name '*.h' | xargs sed -i \
  -e 's/\bdel_timer_sync\b/timer_delete_sync/g' \
  -e 's/\bfrom_timer\b/timer_container_of/g' \
  -e 's/\bdel_timer\b/timer_delete/g'
for f in unisocwcn/sdio/sdiohal_rx.c unisocwcn/sdio/sdiohal_tx.c; do
  sed -i 's|sched_setscheduler(current, SCHED_FIFO, \&param);|sched_set_fifo(current);|g' "$f"
done
sed -i 's|static int sprdwl_cfg80211_set_wiphy_params(struct wiphy \*wiphy, u32 changed)|static int sprdwl_cfg80211_set_wiphy_params(struct wiphy *wiphy, int radio_idx, u32 changed)|' \
  unisocwifi/cfg80211.c

# 4) 编译
cd unisocwcn && rm -f *.o */*.o
make -C /lib/modules/$(uname -r)/build M=$PWD CC=gcc-15 HOSTCC=gcc-15 \
     CFG_AML_WIFI_DEVICE_UWE5621=y modules
cd ../unisocwifi && rm -f *.o Module.symvers
UNISOC_BSP_INCLUDE=$PWD/../unisocwcn/include \
KBUILD_EXTRA_SYMBOLS=$PWD/../unisocwcn/Module.symvers \
  make -C /lib/modules/$(uname -r)/build M=$PWD CC=gcc-15 HOSTCC=gcc-15 modules

echo "build done: $(ls -la ../unisocwcn/*.ko *.ko)"
```

## 11. 本次会话已完成与未完成

**已完成**：
- 工具链选择 (`gcc-15`)
- 9 项源码修改（§3 全部）
- 两个 `.ko` 模块均编译通过
- 固件 `.ini` 部署到 `/lib/firmware/`
- 第一次加载（默认 EXTERNAL_IRQ 模式）：SDIO 卡识别 → chipid 0x56630001 读出 → 但 IRQ 申请失败（stub `wifi_irq_num()=-1`）
- 第二次加载（启用 `CONFIG_SDIO_INBAND_INT`）：
  - sdiohal `irq type:data`（INBAND 工作了）
  - `marlin chip en pull up` → SDIO rescan → chipid 0x56630001
  - 固件下载完整：`marlin_firmware_write finish and successful`
  - CP2 启动 sync 完成：`check_cp_ready sync val:0xf0f0f0ff`
  - SDIO 配置发送：`sdio_config rx mode:[sdma]`、`blksize:[512]`、`irq:[inband]`
  - 但 `get_cp2_version` 超时：CP2 没回应 host 的命令
  - `sprdwl:sprdwl_cmd_init wakeup source register error.`（次要，是 sprdwl_dev=NULL）

**未完成**：
- 收到 CP2 response → `wlan0` 接口出现

## 12. 下一步诊断路径（CP2 启动后命令超时）

CP2 已经下载并运行（dmesg `then marlin download finished and run ok`），但 host 发送 `get_cp2_version` 后 3s 内没收到响应。两种可能：

### 12.1 INBAND IRQ 没真正传递

Amlogic meson-axg/gx-mmc 主线驱动需要 host 实现 `enable_sdio_irq` 回调。验证：

```bash
cat /sys/kernel/debug/mmc0/ios 2>/dev/null
cat /sys/class/mmc_host/mmc0/caps  # 应有 MMC_CAP_SDIO_IRQ (bit 8 = 0x100)
dmesg | grep -i 'sdio.irq\|enable_sdio_irq'
```

如果 host 不支持，强制 POLLING 模式：

```bash
sed -i '/ifeq (\$(CFG_AML_WIFI_DEVICE_UWE5621),y)/,/^endif/ {
  s|^ccflags-y += -DCONFIG_SDIO_INBAND_INT$|#ccflags-y += -DCONFIG_SDIO_INBAND_INT|
  s|^#ccflags-y += -DCONFIG_SDIO_INBAND_POLLING$|ccflags-y += -DCONFIG_SDIO_INBAND_POLLING|
}' ~/uwe5622_driver/unisocwcn/Makefile
# 重编 unisocwcn 模块，重启盒子，重新加载验证
```

POLLING 模式 host 主动周期查询 SDIO 寄存器，不依赖 IRQ。性能差但能验证逻辑通路。

> 本次会话已经把 Makefile 切到 POLLING 并重编了 `unisocwcn`，**待盒子重启后**按 §6 加载验证。

### 12.2 CP2 上 SDIO 控制器 caps 不匹配

dmesg 显示 `sdio_config rx mode:[sdma]`、`blksize:[512]`、`irq:[inband]`，但实际 host 控制器可能要 ADMA 才支持 SDIO IRQ。检查：

```bash
grep CAP /lib/modules/$(uname -r)/build/include/linux/mmc/host.h | grep -i sdio
```

如果 POLLING 也不通，再深入排查总线时钟、电压、SDIO function 1 enable 等。

### 12.3 sprdwl_dev NULL 的 wakeup_source warning

`sprdwl_cmd_init` 在 `sprdwl_dev` 赋值之前被调用（顺序错位），但这只是 warning，不阻塞主流程；解决可以把 `sprdwl_dev` 改成在 `module_init` 时用 `&pdev->dev`，但更彻底的修复是用 `wakeup_source_register(NULL, ...)`：

```bash
sed -i 's|wakeup_source_register(sprdwl_dev, "Wi-Fi_cmd_wakelock")|wakeup_source_register(NULL, "Wi-Fi_cmd_wakelock")|g' \
  ~/uwe5622_driver/unisocwifi/cmdevt.c
```

### 12.4 重启盒子的注意事项

本次观察到 `sudo reboot` 后 sshd 重新启动很慢（5-10 分钟）。如果一直拒绝连接：
- ping 通 → 内核活着，等 sshd 起来
- 长时间不通 → 物理断电重启

经常 `sudo reboot` 失败，物理拔插电源最可靠。

## 13. CONFIG_PM_SLEEP 关键修复（来自 openwrt-rk3566-leopad-10s 项目 513 patch）

Armbian 内核中 `CONFIG_SUSPEND is not set`，因此 `CONFIG_PM_SLEEP` 也未启用。在此情况下 `wakeup_source_register()` 是 inline 空函数，返回 `NULL`。原驱动 `sprdwl_cmd_init` 在 `wake_lock == NULL` 时返回 `-EINVAL`，**导致 cmd 子系统初始化失败但代码继续往下走**，造成后续 `wait_for_completion` 永远超时。

修复（参考 openwrt patch 513）：

```bash
# ~/uwe5622_driver/unisocwifi/cmdevt.c 第 266-269 行
# 原：
	if (!cmd->wake_lock) {
		wl_err("%s wakeup source register error.\n", __func__);
		return -EINVAL;
	}
# 改为：
	if (!cmd->wake_lock) {
#ifdef CONFIG_PM_SLEEP
		wl_err("%s wakeup source register error.\n", __func__);
		return -EINVAL;
#endif
	}
```

`__pm_stay_awake` / `__pm_relax` / `wakeup_source_unregister` 在没有 `CONFIG_PM_SLEEP` 时本身就是 inline noop，安全。

## 14. SDIO inband IRQ 确认通路（含调试 patch）

通过临时打 debug print 验证：

```c
// sdiohal_main.c sdiohal_runtime_get 中：
printk(KERN_ERR "DBG: sdio_claim_irq rc=%d\n", __rc);
// sdiohal_irq_handler_data 入口：
printk(KERN_ERR "DBG: sdiohal_irq_handler_data fired\n");
```

观察结果：
- `sdio_claim_irq rc=0`（成功注册）
- `sdiohal_irq_handler_data fired` 每 20-30ms 触发一次（CP2 在持续推送数据）

确认 host 端 SDIO inband IRQ 链路完整，问题不在 host 端 IRQ 路由。

## 15. 当前未通问题：CP2 不响应 cmd

固件下载成功、CP2 启动 sync 完成（`f0f0f0ff`）后：

```
sdio_config irq:[inband]
sdio_config:0x8c0f31 (enable config)
WCN_ERR: didn't get CP2 version           <-- 3s 超时
WCN_ERR: didn't get switch_cp2_log ack    <-- 3s 超时
sprdwl:[WIFI_CMD_SYNC_VERSION]timeout (mstime=42313)
```

可能原因（按可能性排序）：

1. **5621DS 模组的 26MHz 时钟模式问题**。dmesg 报 `clock mode: TCXO, outside clock`，但代码里 `marlin_avdd18_dcxo_enable(false)` 是被注释的；如果硬件实际用内部 DCXO，应保持注释；如果用外部 TCXO，需要取消注释（关掉内部 DCXO 防干扰）。M401A 的 5621DS 子板硬件原理图需要确认。
2. **驱动 cmd 通道路由问题**：`__sprdwl_cmd_getbuf cant't get vif, ctx_id: 0` 出现在 SYNC_VERSION 之前，cmd 通道路由可能未正确建立。但代码看 ctx_id=0、vif=NULL 时 mode 用 NONE 继续，理论上应可发命令。
3. **5621DS 模组使用了 5622 不兼容的 firmware command set**。当前嵌入的固件版本是 `Marlin3E_Integration_W20.18.4`，与 5621ds-aml/uwe5622_driver 两个仓库提供的完全相同（md5 一致）。

## 16. 下一步建议（受限于远程调试边界）

需要硬件 / 厂家文档支撑的工作：

- 用示波器看 5621DS 模组 26MHz 引脚是否有信号
- 在 `marlin_send_sdio_config_to_cp` 之后、`get_cp2_version` 之前抓 SDIO bus 看 CP2 是否有响应包发出
- 联系紫光展锐拿 5621DS 专用 driver（非 uwe5622_driver）和 datasheet
- 或者直接用社区已经跑通的镜像（armbian m401a 镜像 + 已 build-in 驱动）

## 17. 本次会话最终产物清单

文件位置 + 状态：

| 文件 | 路径 | 状态 |
| --- | --- | --- |
| BSP 模块 | `~/uwe5622_driver/unisocwcn/uwe5621_bsp_sdio.ko` | INBAND_INT + 全部 §3/§13 patches，编译通过、加载成功 |
| WLAN 模块 | `~/uwe5622_driver/unisocwifi/sprdwl_ng.ko` | 全部 §3/§13 patches，编译通过、加载成功 |
| 配置文件 | `/lib/firmware/wifi_56630001_3ant.ini` | 来自 5621ds-aml |
| 固件目录 | `/lib/firmware/uwe5621ds/` | 多份 wifi_xxx.ini + wcnmodem.bin（其实 ko 嵌入不需要） |
| 文档 | `~/wifi-driver-build.md`（待 scp） | 含本文所有内容 |

复现：参考 §10 的一键重建脚本，加上 §13 的 CONFIG_PM_SLEEP 修复（脚本里应加入这一步）。

## 18.1 RK 模式尝试结果（已验证不可行）

尝试切换到 `CFG_RK_WIFI_DEVICE_UWE5621` 模式（leopad-10s 项目用的也是这个）：

Makefile 改为：
```
ifeq ($(CONFIG_RK_WIFI_DEVICE_UWE5621),y)
ccflags-y += -DCONFIG_RK_BOARD
#ccflags-y += -DCONFIG_WCN_PARSE_DTS               # 注释，避免依赖 DTS
ccflags-y += -DCONFIG_WCN_DOWNLOAD_FIRMWARE_FROM_HEX
ccflags-y += -DCONFIG_SDIO_INBAND_INT
ccflags-y += -DCONFIG_WCN_POWER_UP_DOWN
ccflags-y += -DCONFIG_SDIO_BLKSIZE_512
```

结果：SDIO 卡 probe 成功，但 chipid 读出 `0x0`（应为 `0x56630001`），后续大量 `sdiohal_aon_writeb xmit_cnt:0 xmit_start:0,not have card` 错误。**RK 模式的 chip_en 时序与 Amlogic SoC 不兼容**。

结论：**当前最佳基线仍是 AML 5621 模式**。RK 模式如果要工作，需要：
- DTS 中增加完整的 `unisoc,uwe_bsp` 节点（参考 leopad-10s DTS）
- 启用 `CONFIG_WCN_PARSE_DTS`
- 用 `request_firmware()` 加载 `/lib/firmware/wcnmodem.bin`

这是个更大的工作量，不一定能解决 cmd 通信问题（因为 CP2 启动后 cmd 不响应可能跟 mode 无关）。

## 18.3 cmd 通道诊断笔记

`wl_intf.c` 中 SDIO channel mapping：

| 通道用途 | host channel ID |
| --- | --- |
| TX cmd（host→CP2） | `SDIO_TX_CMD_PORT = 8` |
| RX cmd（CP2→host） | `SDIO_RX_CMD_PORT = 22` |
| TX data | `SDIO_TX_DATA_PORT = 10` |
| RX data | `SDIO_RX_DATA_PORT = 24` |
| RX pkt log | `SDIO_RX_PKT_LOG_PORT` |

`sprdwl_intf_init` → `sprdwcn_bus_chn_init` 注册这些 channel 的回调，cmd RX 回调是 `intf_rx_handle`。

现象：CP2 启动后 SDIO inband IRQ 每 20-30ms 触发一次（说明 CP2 持续推数据），但 SYNC_VERSION 通过 channel 8 发出后，channel 22 上拿不到响应（`wait_for_completion_timeout` 3 秒超时）。

可能性：
1. CP2 把 cmd 响应发到了与 host 期望不同的 channel（5621DS firmware vs sdio_hif_ops 表不匹配）
2. CP2 IRQ 持续触发的内容是 log / data 通道，cmd 通道根本没被推过数据
3. `sprdwcn_bus_chn_init(22)` 静默失败，没注册成 RX handler

下一步建议的诊断（每次都要重启盒子，耗时大）：

```c
// 在 wl_intf.c intf_rx_handle 入口加 printk
int intf_rx_handle(int chn, struct mbuf_t *head, struct mbuf_t *tail, int num) {
    printk(KERN_ERR "DBG: intf_rx_handle chn=%d num=%d\n", chn, num);
    ...
}
// 在 sprdwl_intf_init 中 sprdwcn_bus_chn_init 后加：
printk(KERN_ERR "DBG: chn=%d init ret=%d\n", g_intf_ops.hif_ops[chn].channel, ret);
```

如果 dmesg 没看到 `intf_rx_handle chn=22`，确认 CP2 没把 cmd 响应通过 chn22 发出来。

## 18.4 长期方向

短期内可能跑通的两个路径：

**路径 A：从 openwrt-rk3566-leopad-10s 重建驱动**
- 把 501 patch 应用到一份干净的 6.6 kernel 源码（drivers/net/wireless/uwe5622/ 是空目录）
- 依次应用 502-513 patches，得到 leopad 项目用的最终 driver
- 把 `drivers/net/wireless/uwe5622/` 抽出来作为 standalone driver
- 重做 6.6 → 6.18 API 适配（参考本文 §3.3）
- 这是已知能跑通 5621DS 的代码，但还有 4 个 minor 内核版本的 gap 要桥

**路径 B：用 Armbian 社区已经支持 M401A 的 image**
- Armbian 论坛 M401A 板子如果有人移植成功，对应 image 里会有 build-in 驱动
- 直接刷镜像，省去自己编译适配的过程

## 18.5 DTS + stub GPIO 探索结果（已验证不解决问题）

参考 `g12a_u212_4g.dts` 给 m401a DTS 加了 amlogic 风格 wifi 节点：

```dts
wifi {
    compatible = "amlogic, aml_wifi";
    status = "okay";
    wl_reg_on_pin = <&periphs_gpio GPIOX_6 0>;   /* gpio 71, 已被 wifi-pwrseq */
    power_on_pin = <&periphs_gpio GPIOX_7 0>;    /* gpio 72 */
    interrupt_pin = <&periphs_gpio GPIOX_8 0>;   /* gpio 73 */
    interrupt-parent = <&gpio_intc>;
    interrupts = <IRQID_GPIOX_8 IRQ_TYPE_LEVEL_LOW>;  /* IRQID=85 */
    irq_trigger_type = "GPIO_IRQ_LOW";
};
```

并修改 `aml_stubs.c`：
- `extern_wifi_set_enable()` 用 `gpio_request(584)` / `gpio_direction_output()` 操作 GPIOX_7
- `wifi_irq_num()` 用 `irq_of_parse_and_map()` 通过 gpio_intc 申请 OOB IRQ

**验证结果**：

| 项 | 结果 | 说明 |
| --- | --- | --- |
| `gpio_request(72)` 用 chip-local 号 | -517 (-EPROBE_DEFER) | gpiochip0 base 是 512，正确写法 `512+72=584` |
| `gpio_request(584)` 全局号 | 0 (成功) | gpio framework 接受 |
| `extern_wifi_set_enable(1)` | dmesg `power_on_pin set 1` | driver 在 `marlin_chip_en` 时调用，但**对 CP2 行为无任何影响** → m401a 上 GPIOX_7 没接到 5621DS 的电源使能 |
| `gpio_to_irq(585)` | -6 (-ENXIO) | gpiochip0 没注册 IRQ-cap 给单独 line |
| `irq_of_parse_and_map(wifi_node)` via gpio_intc | 返回 0（失败） | `ffd0f080.interrupt-controller` 设备存在但 mainline `irq-meson-gpio.c` 没匹配它的 compatible，没注册 irqdomain |
| 切到 EXTERNAL_IRQ 模式 | sdiohal_probe 失败 -22 | 没有 IRQ → 全套失败 |

**结论**：
- 5621DS 的电源由 `wifi-pwrseq.reset-gpios = GPIOX_6` 管理，GPIOX_7 / GPIOX_8 在 m401a 硬件上**未连接**到 5621DS
- mainline kernel 的 `meson-gpio-intc` 在 6.18 上 g12a 没启用 irqdomain，OOB IRQ 路径不可用
- 必须保持 **INBAND IRQ** 模式（已工作）

回退措施已完成：Makefile 重新启用 `CONFIG_SDIO_INBAND_INT`，stub `wifi_irq_num()` 走不到也不影响（INBAND 模式不调用它）。

## 18.7 32K LPO 时钟修复（M401A_WIFI_MIGRATION 方案）

从安卓固件提取的 `meson1_0.dts` 显示 5621DS 需要 32.768 kHz 外部 LPO 时钟。M401A_WIFI_MIGRATION.md 提供了 mainline G12A 的实现方案：

1. **启用 `pwm@19000`**（PWM_EF）+ pinctrl pwm_e group → 输出到 GPIOX_16
2. **新增 `wifi32k { compatible = "pwm-clock"; clock-frequency = 0x8000; }`** 节点
3. **`wifi-pwrseq` 加 `clocks = <&wifi32k>; clock-names = "ext_clock";`**
4. **SDIO 控制器升级**: 加 `sd-uhs-sdr12/25/50/sdr104`、`cap-sdio-irq`、`max-frequency = 200MHz`

实施验证：

| 项 | 验证 | 结果 |
| --- | --- | --- |
| pwm-clock 注册 | `/sys/kernel/debug/clk/clk_summary` 显示 `wifi32k 32768 Hz, enabled=1` | ✓ |
| PWM 实际输出 | `/sys/kernel/debug/pwm` 显示 `pwm-0 (wifi32k): enabled, 15258/30517 ns` | ✓ 频率 32.769 kHz |
| ext_clock 接到 wifi-pwrseq | clk_summary `consumer wifi-pwrseq ext_clock` | ✓ |
| CP2 行为变化 | SDIO IRQ 计数从 ~30 Hz 降到 ~3 Hz | ✓ 说明 CP2 行为发生改变 |
| SYNC_VERSION | 仍 3 秒超时 | ✗ cmd 通信仍不通 |

**结论**：32K 时钟在内核侧正确产生（已用 debugfs 验证），但 5621DS 上 SYNC_VERSION 仍超时。两种可能：
1. **GPIOX_16 没物理连接到 5621DS 的 LPO_IN 引脚**——meson1_0 那边假设 GXL/m401a v1 用 GPIOX_16，但 G12A/m401a v2 可能走线不同（M401A_WIFI_MIGRATION.md §7 标记的待确认项）
2. **cmd 通信失败的根因不是 32K**——是 driver 与 5621DS fw 之间的协议层问题（之前 §15 的假设）

下一步需要硬件层面工具（示波器/逻辑分析仪 + 板子原理图）验证：
- GPIOX_16 输出端实际波形（确认 PWM 信号物理上是否到达）
- 5621DS 模组 LPO_IN 引脚是否真接 GPIOX_16

## 18.9 从安卓镜像深挖 vendor wifi（最终诊断）

把 m401a 安卓固件 system.PARTITION 解出来，提取 vendor wifi 完整组件做对照：

| 文件 | 大小 | 关键信息 |
| --- | --- | --- |
| `lib/uwe5621_bsp_sdio.ko` | 4.4 MB | vendor BSP，内嵌固件 W21.03.3 |
| `lib/uwe5621_wifi_sdio.ko` | 8.6 MB | vendor WiFi driver |
| `lib/uwe5621_bt_sdio.ko` | 925 KB | vendor BT driver |
| `etc/wifi/uwe5621/wifi_56630001_3ant.ini` | 6.8 KB | RF 校准 ini |
| `bin/unisoc_driver.sh` | 509 B | `insmod /system/lib/uwe5621_bsp_sdio.ko` |
| `boot/ramdisk/init.amlogic.wifibt.unisoc.rc` | - | 通过 property 触发 unisoc_driver.sh |

vendor ko 元数据：
- **vendor 内核**: `vermagic=3.14.29 SMP preempt mod_unload aarch64`
- **vendor 源码路径**: `/home/projects/ANDROID-S905L-EMMC-KITKAT-USER-DEVELOP-20190827/hardware/wifi/unisoc/drivers/uwe5621/`
- **fw 版本字串**: `Platform Version:MARLIN3E_20A_W21.03.3` (2021-01-13)
- **Project Version**: `uwe5623_marlin3E_ott`（注意是 5623！）

### 关键对比表

| 项 | 我们（Armbian 6.18） | vendor（KitKat + 3.14） |
| --- | --- | --- |
| 内核 | 6.18.29-ophub | 3.14.29 |
| Driver 源码 | leopad OpenWrt 移植版 (5622_driver) | vendor `uwe5621_marlin3E_ott` (2019) |
| 固件 | Marlin3E_W20.18.4 | **Marlin3E_W21.03.3** (新) |
| 固件 md5 | `35f143a6...` | **`8b7df595...`** |
| ini md5 | `1b0b3dba...` | **`0235314d...`** |
| WiFi GPIO | wifi-pwrseq=GPIOX_6 | (同) |
| 32K LPO | PWM_E → GPIOX_16 | wifi_32k_pins → GPIOX_16 (同) |

### 已部署的 vendor 资产 + 验证结果

**操作**：
1. ✅ vendor `wifi_56630001_3ant.ini` 覆盖到 `/lib/firmware/{,uwe5621ds/}` 
2. ✅ 从 vendor ko `.data` 段（偏移 `0x23368`，807936 字节）提取 `vendor_wcnmodem.bin`，部署到 `/lib/firmware/{,uwe5621ds/}wcnmodem.bin`
3. ✅ Driver Makefile 关闭 `CONFIG_WCN_DOWNLOAD_FIRMWARE_FROM_HEX`，让 driver 走 `request_firmware()` 真正加载 vendor 固件
4. ✅ ko 重编后 size 从 6.6MB → 4.9MB（嵌入固件已去除）

**dmesg 验证**：
```
WCN: marlin_request_firmware from /system/etc/firmware/wcnmodem.bin start!
WCN: marlin_firmware_parse_image imagepack is WCNE type
WCN: combin_img 3 marlin_firmware_write finish and successful  ← vendor 固件成功下载
WCN: clock mode: TCXO, outside clock
WCN: marlin_write_cali_data sync init_state:0x0 (×3, 之前 W20 要 ×5)
WCN: marlin_write_cali_data sync init_state:0xf0f0f0f1
WCN: marlin_send_sdio_config_to_cp sdio_config:0x8c0931 (enable config)
WCN: check_cp_ready sync val:0xf0f0f0ff   ← CP2 启动 OK
WCN: get_cp2_version entry!
WCN_ERR: didn't get CP2 version    ← cmd 仍超时
```

**结论**：W21.03.3 vendor 固件**实际加载并运行**（sync 阶段比 W20 更快），但 SYNC_VERSION 仍超时。**固件版本不是根因**。

### 最终诊断

cmd 通信失败的根因是 **driver-fw 协议层不兼容**：

- vendor: Linux 3.14 + 2019 编译的 driver + W21.03.3 fw → 协议自洽
- 我们: Linux 6.18 + 2023 移植版 5622_driver + W21.03.3 fw → driver 端 cmd packet 格式可能与 fw 期望的不一致

vendor `uwe5621_marlin3E_ott` driver 源码没有公开。5622_driver 是社区移植，从 5622 (uwe5622_OTT 商业项目) 衍生而来，**与 vendor 5621/5623 marlin3E_ott 项目的 cmd 协议可能在某些 fields layout 上有差异**。

### 下一步可行路径

| 路径 | 工作量 | 可行性 |
| --- | --- | --- |
| 反汇编 vendor ko 对比 `sprdwl_cmd_hdr` 结构 | 大 | 远程不可行 |
| 找紫光展锐 5621/5623_marlin3E_ott driver 源码 | 中 | 取决于能否拿到 |
| 把 m401a 刷回 vendor android | 小 | 放弃 mainline |
| 用 vendor 3.14 内核 + 原 ko | 中 | 兼容性最佳但失去 mainline 优势 |

## 18.11 OOB IRQ 路径在 mainline 6.18 上不可用（重新审视后纠正）

之前误判说"GPIOX_7/8 在 m401a 上没接"——**纠正**：同一块主板硬件接线必然一致，vendor android 能跑 wifi 证明 OOB IRQ 引脚是接着的。

从 vendor `meson1_0.dts` 翻译到 G12A：
- vendor `power_on_pin = <0x19 0x55 0x00>` = GXL gpio 85 = **GXL GPIOX_6** = G12A GPIOX_6 (pin 71) ← **与 wifi-pwrseq.reset-gpios 同一根线**
- vendor `interrupt_pin = <0x19 0x61 0x00>` = GXL gpio 97 = **GXL GPIOX_18** = G12A GPIOX_18 (pin 83)
- vendor `irq_trigger_type = "GPIO_IRQ_HIGH"`
- G12A IRQID_GPIOX_18 = 95 = 0x5f

按此重做 dts wifi 节点：
```dts
wifi {
    compatible = "amlogic,uwe5621-irq";
    interrupt-parent = <&gpio_intc>;
    interrupts = <0x5f 0x04>; /* IRQID_GPIOX_18=95, IRQ_TYPE_LEVEL_HIGH=4 */
};
```

driver 走 EXTERNAL_IRQ（关 INBAND_INT），stub 用 `irq_of_parse_and_map` 拿 IRQ。

**结果**：`aml_stubs: wifi_irq_num: irq=0 (via gpio_intc)` —— **`irq_of_parse_and_map` 仍返回 0**。

进一步诊断：
- `/sys/bus/platform/devices/ffd0f080.interrupt-controller/driver` → `meson_gpio_intc` ✓ 已绑定
- `echo 595 > /sys/class/gpio/export` 成功（GPIOX_18 全局编号 = chip_base 512 + 83）
- 但 `/sys/class/gpio/gpio595/edge` 文件**不存在** ← **关键证据**

`edge` sysfs 属性的存在意味着 gpio_chip 实现了 `.to_irq` 回调。文件不存在 = **mainline 6.18 `pinctrl-meson-g12a.c` 的 `gpio_chip` 没实现 `.to_irq`**。

GPIO 只能当普通 GPIO 用（input/output），**不能直接转 IRQ**。要让 GPIO 触发 IRQ 必须显式通过 `meson_gpio_intc` controller 走 of_irq 路径。但 `irq_of_parse_and_map` 在 module 上下文返回 0，可能是 channel allocation 失败或 driver 实际未注册 irqdomain（虽然 device-driver 已 bind）。

**真正结论**：
- 硬件 GPIOX_18 接到 5621DS HOST_WAKE 引脚（vendor android 上工作）
- mainline 6.18 + meson-g12a-pinctrl + meson_gpio_intc 这条链路**无法**给应用层（module/stub）分配 GPIO IRQ
- 所以 OOB IRQ 模式在 mainline 上**不可用**——必须用 INBAND IRQ
- INBAND IRQ 实际工作（dmesg sdiohal_irq_handler_data fired 频繁）但 SYNC_VERSION 仍超时——**根因转回 driver-fw cmd 协议层**

## 18.13 反编译 vendor ko 对比 cmd 协议（结论：协议层无差异）

反编译 vendor `uwe5621_wifi_sdio.ko`（Ghidra 输出），对比关键函数：

| 项 | vendor | 我们 5622_driver | 一致？ |
| --- | --- | --- | --- |
| **byte 0 公式** | `(rsp<<4) \| (ctx_id<<5) \| 0` | `(rsp<<4) \| (ctx_id<<5) \| SPRDWL_TYPE_CMD` | ✓（type=0 时等价） |
| **byte 1** | cmd_id | cmd_id | ✓ |
| **bytes 2-3** | plen | plen | ✓ |
| **bytes 4-7** | mstime（ktime_get/1e6） | mstime（同公式） | ✓ |
| **RX 分发** | `switch(*v16 & 7)` case 0=CMD | `switch(SPRDWL_HEAD_GET_TYPE(data))` case SPRDWL_TYPE_CMD=0 | ✓ |
| **rsp 匹配** | `mstime == g_cmd.mstime && cmd_id == g_cmd.cmd_id` | 同 | ✓ |
| **sanity check** | `ctx_id > 2 \|\| cmd_id > 84 \|\| plen > 2048` | 同 | ✓ |
| **SDIO channels** | TX_CMD=8, RX_CMD=22, ports=5 | 同 | ✓ |

**所有可反编译看到的 cmd 协议层都一致**。说明 cmd 不响应不是 packet 格式问题。

可能的（不可证实的）差异点（都在 BSP 层 `unisocwcn`）：

1. **mdbg / loopcheck channel 注册顺序**：vendor BSP 可能在某些 mdbg 通道上做 fw-driver 握手，5622_driver 移植版可能漏了
2. **vendor-specific SDIO config bits**：`sdio_config:0x8c0f31` 看似一样，但 W21.03.3 fw 可能期望某些位为不同值
3. **sprdwcn_bus 子系统 init 顺序**：vendor 可能在 marlin 之外另外初始化了某些 SoC 端口
4. **wakeup / power state 协商**：vendor 在 cmd 通信之前可能有 vendor-specific wake handshake，W21.03.3 fw 收不到 → 不进入 cmd loop

**总结**：cmd 协议反编译可见部分**完全一致**。剩余差异必在 BSP 层 vendor-specific 细节，需要 vendor `hardware/wifi/unisoc/drivers/uwe5621/` 源码才能找到。

## 18.15 反编译 vendor BSP（uwe5621_bsp_sdio.ko）— mdbg channel 全一致

继续深挖反编译 vendor **BSP** ko（不是 wifi ko）。

### 关键发现

**vendor mdbg channel 号通过 ELF symbol + .data 内容提取**：
- 符号 `mdbg_proc_ops` value `0xc4e38` size 320 字节 = 4 × 80 字节 mchn_ops_t
- 符号 `mdbg_ringc_ops` value `0xc4ce0` size 80 字节
- .data section file offset `0x23368`
- 提取位置：mdbg_proc_ops 在 ko 文件偏移 `0xe81a0`，mdbg_ringc_ops 在 `0xe8048`

| ops | vendor channel | 5622_driver 对应 enum |
| --- | --- | --- |
| `mdbg_proc_ops[0]` (AT_TX) | 0 | `WCN_AT_TX = 0` |
| `mdbg_proc_ops[1]` (LOOPCHECK_RX) | **12** | `WCN_LOOPCHECK_RX = 12` |
| `mdbg_proc_ops[2]` (AT_RX) | **13** | `WCN_AT_RX = 13` |
| `mdbg_proc_ops[3]` (ASSERT_RX) | **14** | `WCN_ASSERT_RX = 14` |
| `mdbg_ringc_ops` (RING_RX) | **15** | `WCN_RING_RX = 15` |

**完全一致**。`unisocwcn/platform/wcn_txrx.h` enum 与 vendor binary 提取的 channel 号匹配。

### BSP cmd 流程已映射

vendor `get_cp2_version` (C0FC.c)：
```c
strcpy(a, "at+spatgetcp2info\r\n");
mutex_lock(&atcmd_lock);
at_cmd_send(a, 0x14u);   // 20 字节，含 \r\n
wait_for_completion_timeout(&atcmd_completion, 300);  // 300 jiffies
```

vendor `at_cmd_send` (BD38.c)：
```c
_kmalloc(len + 5, ...)
memcpy(v5 + 4, buf, len)   // ← payload 前 4 字节预留
channel = mdbg_proc_ops[0].channel   // = WCN_AT_TX = 0
list_alloc(channel, ...) → push_list(channel, ...)
```

vendor `marlin_probe` 关键调用顺序（1678.c）：
1. `slp_mgr_init()`
2. `module_bus_init()` → `module_ops_register(&sdiohal_bus_ops)`
3. `register_rescan_cb(marlin_scan_finish)`
4. `preinit()`
5. `proc_fs_init()` → 循环调 `chn_init(&mdbg_proc_ops[i])` for i=0..3
6. `log_dev_init()` → `mdbg_ring_init()` → `chn_init(&mdbg_ringc_ops)`
7. `mdbg_atcmd_owner_init()`
8. `wcn_op_init()`
9. `loopcheck_init()`（只 init `atcmd_completion + atcmd_lock`）

### 仍未确认的潜在差异点

虽然 mdbg channel 号一致，但 **mchn_ops_t 内的 pop/push_link 函数指针** 在 vendor ko 中通过 `.rela.data` relocation 填充，反编译可见的是符号引用。需要：
1. 解析 vendor `.rela.data` 看 mdbg_proc_ops[i] 各字段的实际填充函数
2. 对比 vendor `mdbg_at_cmd_read` (A828.c) 与我们 `mdbg_at_cmd_read` 实现差异
3. 特别关注 vendor 中 `*(unsigned int *)head->buf >> 7` 的非标准 length 编码

### 详细接续工作清单

见同目录 `CONTINUATION.md`。下个会话从该文件 §4.1 开始。

## 18.16 SDIO 收发路径深度插桩（会话 3 决定性结论）

对 SDIO 收发路径做 KERN_ERR 深度插桩,得到迄今最确定的诊断。详见 `CONTINUATION.md §6.6`。

**核心证据**：
1. **TX 路径逐函数等价 vendor**：`sdiohal_channel_to_hwtype`(type=0,subtype=ch,SDIO_CHN_TX_NUM=12)、`sdiohal_send`(512块对齐)、`sdiohal_sdio_pt_write`(`sdio_writesb` 写 FIFO 0x20)、puh 填充 —— 全部与反编译的 vendor(10DEC/12648/DDBC.c)一致。
2. **RX trailer 机制工作**：插桩 rx_thread 看到启动时 `valid_len=128/1424`(从 buf[read_len-8] 读到有效 trailer),证明 SDMA 读+长度 trailer 机制正常,数据通路 OK。推翻了之前"读被拆分"的假设。
3. **vendor rx_thread(12AF8.c)逐行等价**我们的实现。
4. **fw 自报完整 ready**：`init_status == SYNC_ALL_FINISHED(0xF0F0F0FF)`,chip 检测正确(MARLIN3E)。
5. **手动 `echo > /proc/mdbg/at_cmd` 实验**：TX 成功送出(chn0),但 fw **零响应**,连 log 都停。

**锁定结论**：driver 软件 100% 正确;fw 完整启动、RX 通路活、启动时能发 log;**唯一故障:fw 收到 host 写入 packet FIFO(0x20)的 cmd 后完全不处理。**

**读写不对称是最大嫌疑**：
- RX `sdio_readsb(func1, 0x20)` 固定地址读 → **工作**(收到 log)
- TX `sdio_writesb(func1, 0x20)` 固定地址写 → fw 收不到

这指向 mainline **`meson-gx-mmc` 执行 CMD53 写 FIFO 的方式**与 amlogic vendor SDIO 驱动不同,导致 fw slave 的"host 写完成"DMA/中断未触发。属于 SDIO 主控驱动层问题,非 unisoc wifi driver 可修。

**真正解决需要**(超出 wifi driver 范畴)：
1. patch `meson-gx-mmc.c` 修正 SDIO 固定地址写/SDIO function 中断行为,或
2. 逻辑分析仪抓 CMD53 写 0x20 时的 DAT 线确认电气层,或
3. 改用 amlogic vendor 内核(3.14)+ vendor ko(已知工作),放弃 mainline

## 18.17 关于"模块是 uwe5621ds"的说明

用户特别指出模块型号是 **UWE5621DS**（不是 5622）。本次方案的选择：

- 驱动源码用的是 `uwe5622_driver` 仓库，**配置项是 `CFG_AML_WIFI_DEVICE_UWE5621=y`**（不是 5622）
- 这激活的代码路径是为 UWE5621 系列设计的：`BSP_CHIP_ID=uwe5621`、`MODULE_NAME=uwe5621_bsp_sdio`
- chipid `0x56630001` 是 5621DS 的标识，dmesg 已经正确读到
- 5621ds-aml 仓库和 5622_driver 仓库的固件 `wcnmodem.bin.hex` md5 完全一致——说明这套驱动框架是统一的，区别只在配置宏

CP2 不响应可能还是与 5621DS 硬件参数（时钟、PA 路径）有关，可参考 §15.1。

## 19. ✅ 最终解决（会话 6，2026-06-01）— WiFi 打通

经过本会话的进一步定位，**问题已彻底解决**：`wlan0` 正常注册，连续多次冷启动均能稳定扫描到 130~174 个 AP，无 cmd 超时、无 assert。

> ⚠️ 本节**更正了 §18.16 的结论**。当时把根因归到「mainline `meson-gx-mmc` 执行 CMD53 写 FIFO 不对称 / fw 收不到 cmd / 需逻辑分析仪」——**这个结论是错的**。真正的故障在 **RX（读）侧**，是纯软件可修的，根本不需要改 host 驱动或上电气层。

### 19.1 真正根因：SDMA trailer 不可靠 → 自适应读长被垃圾值污染

`sdiohal_rx_thread`（`unisocwcn/sdio/sdiohal_rx.c`）每读完一包，从读缓冲末尾 8 字节取展锐 SDMA trailer：
```c
rx_dtbs   = *(u32 *)(rx_buf + read_len - 4);   // 下次该读多少字节
valid_len = *(u32 *)(rx_buf + read_len - 8);   // 本包有效长度
...
sdiohal_rx_buf_parser(rx_buf, valid_len);       // 按 valid_len 派发
sdiohal_rx_adapt_set_dtbs(rx_dtbs);             // 用 dtbs 决定下次 read_len（0 → MAX_PAC_SIZE）
```

**mainline `meson-gx-mmc` 不会可靠地回写这 8 字节 trailer。** 而每次开机残留在 DMA 缓冲里的内容不同：

- **"好"启动**：trailer 读回 0 → 回退按包头计算 → 正常；
- **"坏"启动**：trailer 读回**非零垃圾** → ① 用垃圾 `valid_len` 派发 → 响应损坏/丢弃；② 用垃圾 `dtbs` 把下次自适应读长缩小 → **截断后续 cmd 响应**。

这就是之前"换一次启动卡在不同命令（SYNC / OPEN / SCAN / POWER_SAVE / get_cp2_version）、偶尔整轮成功"的真正来源——是**每启动随机**的 RX 帧化错误，不是 fw 不响应。

> 为什么 §18.16 误判：当时插桩看到启动时 trailer 给出 128/1424 等"有效值"，就以为 trailer 机制工作；又看到手动 at_cmd 后 fw"零响应"。实则是 trailer **时好时坏**，且手动实验那次正好命中坏帧化/响应被丢，于是错误地把锅甩给了 fw 和 host 写通路。

### 19.2 修复

核心改动 `unisocwcn/sdio/sdiohal_rx.c`：**彻底不信任 SDMA trailer**。

```c
/* 永不信任 trailer：始终用自描述包头重算 valid_len；
 * 始终把下次读长强制回 MAX_PAC_SIZE（rx_dtbs = 0），避免被垃圾 dtbs 截断；
 * 排空只靠 hdr-walk 是否还捞到包驱动。*/
valid_len = sdiohal_rx_calc_validlen(rx_buf, read_len);
rx_dtbs = 0;
if (valid_len > 0 && drain_cnt++ < MAX_CHAIN_NODE_NUM)
    force_drain = 1;
...
sdiohal_rx_buf_parser(rx_buf, valid_len);
...
sdiohal_rx_adapt_set_dtbs(rx_dtbs);   // 恒为 0 → 下次 read_len = MAX_PAC_SIZE
if (rx_dtbs > 0 || force_drain)
    goto read_again;                  // 排空 FIFO，响应不滞留到下一个（可能丢失的）IRQ
```

新增 helper `sdiohal_rx_calc_validlen()`：遍历自描述包头 `sdio_puh_t`（`include/wcn_bus.h`）累加各包长度，遇 EOF / 非法长度即停止。

配套改动 `unisocwcn/sleep/slp_mgr.c`：**永久禁用 CP2 睡眠**。mainline 缺少厂商用的 OOB `sdio_pub_int` GPIO 睡眠握手，让 CP 睡下去会导致 `host wakeup fw cmd failed`，后续命令全部失败。

三处稳健性叠加（hdr-walk 重算长度 + 读长恒为 MAX_PAC_SIZE + force_drain 排空 + 禁睡眠）共同消除了跨启动偶发性。

### 19.3 验证结果（连续 3 次冷启动）

| 启动 | wlan0 | 扫描 | cmd 超时 / assert |
| --- | --- | --- | --- |
| Boot 1 | ✅ | BSS=139 | 无（仅一次非致命 `didn't get CP2 version`） |
| Boot 2 | ✅ | BSS=130 | 完全无 |
| Boot 3 | ✅ | 连续 165 / 165 / 174 | 完全无 |

残留：开机偶发一次**非致命**的 `didn't get CP2 version` + `get_fw_info TLV check failed`（最早一两条命令在 fw/总线刚就绪窗口丢响应，驱动用默认 fw 信息继续，扫描正常）。可在 `sprdwl_get_cp2_version` 失败时加重试根除。尚未做实际关联+DHCP 的端到端联网验证。

### 19.4 编译与加载

```bash
cd ~/uwe5622_driver
make -C /lib/modules/$(uname -r)/build M=$PWD ARCH=arm64 \
    CONFIG_UWE5622=m CONFIG_SPRDWL_NG=m CONFIG_UNISOC_WIFI=m CC=gcc modules
# 只改了 BSP 层（sdiohal_rx.c / slp_mgr.c），sprdwl_ng.ko 无需重编
bash ~/loadwifi.sh   # modprobe cfg80211 + insmod uwe5621_bsp_sdio.ko + insmod sprdwl_ng.ko
```

### 19.5 开源 / Release 产物

驱动已整理为可开源仓库 + 预编译 Release 包：

- **源码**：`/Users/weijiangchen/tmp/uwe5622_driver_src/`（含 `README.md`、`m401a/` 设备树 dts+dtb、加载脚本；已清除明文密码/内网 IP）。机型更正为 **魔百盒 UNT431A（S905L3A）**，刷机教程见 README 顶部链接。
- **Release 包**：`/Users/weijiangchen/tmp/uwe5621ds-wifi-unt431a-s905l3a-6.18.29-ophub.tar.gz`，自包含：`uwe5621_bsp_sdio.ko` + `sprdwl_ng.ko` + `m401a_uwe5621ds_wifi.dtb` + `firmware/`（wcnmodem.bin + ini）+ `loadwifi.sh` + `INSTALL.md`（AI 可逐步执行的完整部署 runbook，带前置校验/设备树激活/持久化/故障排查）+ `SHA256SUMS.txt`。
- 模块 vermagic：`6.18.29-ophub SMP preempt mod_unload aarch64`（仅适配该验证镜像，换内核需重编）。

### 19.6 经验教训

1. **不要轻易把根因甩给"硬件/电气层/fw 端"**。§18.16 在软件层证据不足时过早下了"需逻辑分析仪 / 改 host 驱动 / 放弃 mainline"的结论，险些堵死纯软件修复路径。
2. **间歇性故障要找"每次启动会变的状态"**。本例是未初始化/残留 DMA 缓冲内容 → 完美解释"跨启动随机"。
3. **不可靠的硬件元数据要有软件兜底**。trailer 不可信时，用自描述包头自解析（hdr-walk）是稳健替代。
4. **调试 printk 的"意外延迟"会掩盖时序 bug**。移除调试输出后复现的失败，提示问题在帧化/排空而非纯延迟；试 `udelay` 替代无效也印证了这一点。

## 20. 会话 7（2026-06-01）— 连接失败定位 → 转向 OOB GPIO 中断（进行中）

> 背景：§19 的 RX trailer 兜底让"扫描"稳定，但**实际关联（connect）仍失败**。本会话从 connect 失败入手，最终把方向转到「用带外（OOB）GPIO 中断替代 inband data1 中断」——这正是厂商 amlogic 驱动的做法。**截至本节，OOB 中断尚未跑通，仍在调极性/引脚。**

### 20.1 connect 失败 → 去掉随机 MAC

- 现象：能扫描，连不上。NetworkManager 报 "association took too long / ssid-not-found"，`wpa_supplicant` 反复 `CTRL-EVENT-SCAN-FAILED ret=-5`。
- 对照本地反编译 vendor（`uwe5621_wifi_sdio`）：**connect 路径绝不下发随机 MAC**。我们的 `uwe5622_driver_src/unisocwifi/cfg80211.c` 在 `sprdwl_cfg80211_connect` 里主动调用 `wlan_cmd_set_rand_mac()`，相对已知能用驱动是真实偏差。
- 已改：删除该调用与未用变量 `int random_mac_flag;`（与 vendor 行为一致）。connect 期间的 fw assert 消失（进步），但**早期命令响应丢失/偶发 dump 复现**——根子还是 inband 中断在 meson SDIO 上不可靠。

### 20.2 关键转折：对照 `uwe5621ds-amlogic` 得出真正架构

amlogic 驱动（与我们同源码树，但为同 SoC 定制）`unisocwcn/Makefile` 的 UWE5621 段揭示了正确配置：

```
ccflags-y += -DCONFIG_AML_BOARD
ccflags-y += -DCONFIG_WCN_POWER_UP_DOWN
#ccflags-y += -DCONFIG_SDIO_TX_ADMA_MODE   ← ADMA 关
#ccflags-y += -DCONFIG_SDIO_RX_ADMA_MODE   ← ADMA 关
# ccflags-y += -DCONFIG_SDIO_INBAND_INT    ← inband 关
ccflags-y += -DCONFIG_CUSTOMIZE_SDIO_IRQ_TYPE=2   ← 告诉固件用 OOB GPIO 引脚
ccflags-y += -DCONFIG_SDIO_BLKSIZE_512
```

`wcn_boot.c` 里 `sdio_cfg.cfg.sdio_irq_type` 的取值（**发给固件**，告知它用哪条线通知 host 有 RX）：

```
bit[12:11] sdio_irq_type:
  00 = 专用 gpio1
  01 = inband data1 中断（= 我们之前一直在用，meson 上会漏）
  10 = 用 BT_WAKEUP_HOST(pubint) 引脚做 gpio 中断  ← amlogic UWE5621 用这个(=2)
  11 = 用 WL_WAKEUP_HOST(esmd3) 引脚做 gpio 中断
```

**结论**：amlogic 既不用 inband 也不用纯轮询，而是让固件在一条**专用 OOB GPIO 线**上拉中断，host 侧用 `SDIOHAL_RX_EXTERNAL_IRQ` + `request_irq()` 接这条线 → 这正是 meson 上「漏命令响应」的根治方向。

host 侧 `p_data->irq_type`（parse_dt 据编译宏决定）与固件 `sdio_irq_type`（发给 CP）是**两件事**，必须配套：INBAND/POLLING 宏全关时 host 默认 `EXTERNAL_IRQ`；此时 `sprdwcn_bus_get_irq_type()==0`，`wcn_boot` 才会用 `CONFIG_CUSTOMIZE_SDIO_IRQ_TYPE` 通知固件。

### 20.3 本会话已落实的改动（盒子 + 本地仓库 `uwe5622_driver`）

1. **`unisocwcn/Makefile`（AML_5621 段）去掉 `-DCONFIG_SDIO_INBAND_INT`**。
   之前盒子端这一段被误加了 INBAND_INT，导致 parse_dt 强制 `irq type:data`（inband），把整套 OOB GPIO 路径架空（dmesg `sdio_config irq:[inband]`）。去掉后 dmesg 变为 `irq type:gpio`。
2. **`unisocwcn/platform/aml_stubs.c` 提供 `wifi_irq_num()`**（替代 amlogic vendor 内核函数）。
   - 之前用 `of_find_node_by_name(NULL,"wifi")` → **会先命中 SDIO 子节点 `wifi@1`（无 interrupts）** → `irq_of_parse_and_map` 返回 0。
   - 已改为 `of_find_compatible_node(NULL,NULL,"amlogic,uwe5621-irq")` → 正确命中顶层 `/wifi` 节点 → **`irq=38` 映射成功**（`/proc/interrupts`：`meson-gpio-irqchip 83 Level sdiohal_irq`）。
3. **设备树 `/wifi` 节点引脚号更正**（`m401a_uwe5621ds_wifi.dts`，需 `dtc` 重编 dtb 部署到 `/boot/dtb/amlogic/`）。
   - 本盒 vendor dts（`meson1_0.dts`）`wifi` 节点：`interrupt_pin = <0x19 0x61 0x0>`（vendor 编号 97），`power_on_pin=<.. 0x55 ..>`（85），`irq_trigger_type="GPIO_IRQ_HIGH"`。
   - vendor g12a 编号 `GPIOX_0=79` → **97 = GPIOX_18**（power 85 = GPIOX_6 = wl_reg_on，自洽）。
   - **mainline 编号**（`meson-g12a-gpio.h`）`GPIOX_18 = 83`，gpio-intc hwirq 同号 → 节点应为 `interrupt-parent=<&gpio_intc>; interrupts=<0x53 type>`。
   - 原节点写成 `0x5f`(95，错号) 是先前的笔误，已更正为 `0x53`(83)。

### 20.4 OOB 引脚/极性实验矩阵（关键，尚未跑通）

固件已确认收到配置：dmesg `sdio_config sdio_irq:[pubint]`（type=2）。但 RX 仍不通：

| 配置 | host trigger | `/proc/interrupts` 计数 | SYNC_VERSION |
| --- | --- | --- | --- |
| `IRQ_TYPE=2` + DTS `LEVEL_HIGH`(4) + stub 返回 `GPIO_IRQ_HIGH`(0) | high | **暴涨**(~4300/s，中断风暴) | timeout |
| `IRQ_TYPE=2` + DTS `LEVEL_LOW`(8) + stub 返回 `GPIO_IRQ_LOW`(1) | low | **恒为 0**（从不触发） | timeout |

判读：GPIOX_18 这条线**常驻高电平**，固件从未拉动它 → 说明 `sdio_irq_type=2`（BT_WAKEUP_HOST/pubint 引脚）与我们监听的 `/wifi` 引脚（应为 WL_WAKEUP_HOST）**对不上**。

- `GPIO_IRQ_HIGH=0 / GPIO_IRQ_LOW=1`（`include/linux/amlogic/aml_gpio_consumer.h`）。
- `wcn_boot.c` 据 `wifi_irq_trigger_level()` 同时设固件 `sdio_irq_trigger_type`（low=0 / high=3）与 host `IRQF_TRIGGER_*`，两者会联动。

### 20.5 下一步（正在验证）

把固件改为 **`CONFIG_CUSTOMIZE_SDIO_IRQ_TYPE=3`**（WL_WAKEUP_HOST/esmd3 引脚 = `/wifi` 的 `interrupt_pin`=GPIOX_18，与 host 监听一致），DTS 保持 `LEVEL_LOW`、stub 返回 `GPIO_IRQ_LOW`。已重编 bsp、重启加载，**结果待确认**：
- 期望 `/proc/interrupts` 计数随 RX 适度增长（非风暴、非 0），且 `SYNC_VERSION` 不再超时、`wlan0` 注册。
- 若 type=3 + LOW 仍为 0，下一轮翻 host 极性为 HIGH（GPIOX_18 空闲高，固件 high 有效则需 LEVEL_HIGH）逐一排除。

### 20.6 关键文件位置（便于续作）

- BSP Makefile 段：`~/uwe5622_driver/unisocwcn/Makefile`（AML_5621：`#INBAND_INT`、`CUSTOMIZE_SDIO_IRQ_TYPE=2/3`）。
- IRQ/电源 stub：`~/uwe5622_driver/unisocwcn/platform/aml_stubs.c`（`wifi_irq_num` 按 compatible 查 `/wifi`，`wifi_irq_trigger_level` 控极性）。
- host 外部中断接管：`unisocwcn/sdio/sdiohal_main.c::sdiohal_host_irq_init`（`#ifdef CONFIG_AML_BOARD` 用 `wifi_irq_num()`）、`sdiohal_irq_handler`（mask→`sdiohal_rx_up()`→读完 `sdiohal_enable_rx_irq` 重开）。
- 设备树：本地 `m401a_uwe5621ds_wifi.dts` 的 `/wifi`（compatible `amlogic,uwe5621-irq`）；盒子 `dtc -I dts -O dtb` 后部署 `/boot/dtb/amlogic/m401a_uwe5621ds_wifi.dtb`（`/boot/uEnv.txt` 的 `FDT=` 指向它）。
- 参考标准：`uwe5621ds-amlogic/`（同源码定制版）、本盒 vendor `meson1_0.dts`、同模组参考 `g12a_u212_4g.dts`。

### 20.7 经验教训（本会话）

1. **host RX 模式 与 固件中断引脚是两套配置，必须配套**。只改一个（如开 IRQ_TYPE=2 但 host 仍 INBAND）等于没改。
2. **`of_find_node_by_name(NULL,"wifi")` 有坑**——会先命中 SDIO 子节点 `wifi@1`。OOB 节点要用唯一 `compatible` 查找。
3. **vendor dts 的 gpio 编号是 vendor 内核编号**（本盒 `GPIOX_0=79`），换算到 mainline（`GPIOX_0=65`）才能填进 `gpio_intc` 的 hwirq。
4. **中断风暴 vs 计数恒 0 是极性/引脚错配的两个典型征兆**：风暴=监听线空闲即处于触发态；恒 0=固件根本没在这条线上发信号（很可能引脚选错，pubint vs esmd3）。

## 21. 会话 8（2026-06-01 续）— OOB 判死 → 回 INBAND + 兜底 → 定位「CP 闲置后失联」

> 结论先行：**OOB GPIO 路线在本板判定走死**（固件根本不驱动 GPIOX_18）；转回 INBAND + §19 兜底 + 轮询 + 加长超时 + 关闭 host 主动 assert 后，**驱动能完整跑通 bring-up（校准/`chip_model 0x2355`/`wlan0` 注册）并稳定存活不再 carddump**；但**最终卡在固件侧**：CP 在 bring-up 后约 30s 闲置即变为**永久不响应**（之后每条 SCAN/SET_IE 都整 8s 超时），与「OOB 唤醒 GPIO 在本板不可用 → CP 一旦休眠无法唤回」完全吻合。

### 21.1 OOB GPIO 路线判死（补完 §20.5 矩阵）

把固件改 `CONFIG_CUSTOMIZE_SDIO_IRQ_TYPE=3`（WL_WAKEUP_HOST/esmd3）后实测：

| 配置 | host trigger | `/proc/interrupts` | 结果 |
| --- | --- | --- | --- |
| `IRQ_TYPE=2`(pubint) + HIGH | high | 暴涨（风暴） | SYNC timeout |
| `IRQ_TYPE=2`(pubint) + LOW | low | 恒 0 | SYNC timeout |
| `IRQ_TYPE=3`(esmd3) + LOW | low | **恒 0**（`irq=38`/hwirq83 映射成功但从不触发） | SYNC timeout |

- vendor 引脚换算复核无误：以 mainline `reset-gpios=pin71=GPIOX_6=wl_reg_on` 反推 vendor `GPIOX_0=79` → `power_on=85→GPIOX_6`、`interrupt_pin=97→GPIOX_18`，自洽。
- 判读：GPIOX_18 **空闲常高**，无论固件 type=2/3、host 高/低极性，**固件都不曾拉动这条线**。
- 与 `slp_mgr.c` 既有注释一致：**本 mainline meson-g12a 移植上 OOB `sdio_pub_int`(bt/wl_wake_host) 并未接通/不可用**。→ OOB 路线在本板不可行，放弃。

### 21.2 回退到 INBAND + 逐项兜底（均已落地：本地 `uwe5622_driver_src` 与盒子 `~/uwe5622_driver`）

1. **Makefile 回 INBAND**：AML_5621 段恢复 `-DCONFIG_SDIO_INBAND_INT`，注掉 `CUSTOMIZE_SDIO_IRQ_TYPE`。dmesg 回到 `irq type:data`。
2. **`sdiohal_rx_down()` 改限时轮询兜底**（`unisocwcn/sdio/sdiohal_common.c`）：
   `wait_for_completion_interruptible(...)` → `..._timeout(&rx_completed, msecs_to_jiffies(100))`。
   meson-gx-mmc 的 DAT1 inband 中断会漏，漏了 rx 线程会永久阻塞 → 限时 100ms 兜底，超时也照常推进做一次投机读，漏的 IRQ 在一个轮询周期内自恢复（空读无害：`calc_validlen` 返回 0 不分发）。**此项让 SYNC 通过率显著提升。**
3. **软件中断 ack（`sdiohal_inband_int_ack()`，已加入但默认未在轮询里启用）**：在 `sdiohal_main.c` 复刻 handler 的 `sdio_f0_readb(FUNC_0, SDIO_CCCR_INTx)`。实测把轮询缩到 20ms 并每次 ack 反而**更早触发 CP dump**，遂回退为「100ms 纯限时轮询、不 ack」。该函数保留备用，header 已声明。
4. **命令超时 3s→8s**（`unisocwifi/cmdevt.h::CMD_WAIT_TIMEOUT`）：
   实测 `get_cp2_version` 的响应在**超时后 4ms** 才到（CP 启动期会卡几秒再批量回包），3s 太紧。放宽到 8s 让「迟到但确实会到」的响应被接收。
5. **关闭 host 主动 assert/carddump**（`unisocwifi/Makefile` 去掉 `-DATCMD_ASSERT`）：
   命令超时本会触发 `sprdwl_atcmd_assert → mdbg_assert_interface → carddump`，**一次超时即把芯片打死**。而响应匹配是 `mstime+cmd_id` 双重校验（`cmdevt.c::sprdwl_rx_rsp_process` 行 2596），**迟到的旧响应绝不会错配到新命令**，故把超时改为「非致命」是安全的。去掉后**芯片不再被 host 打死**。

> wifi 模块单独重编需带：`UNISOC_BSP_INCLUDE=.../unisocwcn/include` 和 `KBUILD_EXTRA_SYMBOLS=.../unisocwcn/Module.symvers`，否则 `wcn_bus.h` 找不到 / modpost 报 `get_wcn_bus_ops` 等 undefined。

### 21.3 关键发现：dump 是「果」不是「因」——CP 自己闲置后失联

逐条对照 dmesg 时间线（关闭 assert 后这一关系才看清）：

- `60.9s` bring-up 完成：`marlin ... run ok`、`chip_model:0x2355` 读取成功（**此刻 CP 正常**）。
- 之后约 30s **空闲无命令下发**。
- `90.3s` 第一条 `WIFI_CMD_SCAN` 即失败；此后每条 SCAN/SET_IE **整整 8s 超时**（=新的 `CMD_WAIT_TIMEOUT`），`sprdwl_cfg80211_scan failed (-1)` 不断重复。
- 关闭 assert 后**没有 carddump**，但 CP 同样从 ~90s 起**永久不回包**。

→ 之前几轮看到的 `carddump`/`pt_read line 294 dump happened` 是 **host 因超时主动打 dump 的结果**；真正的根因是 **CP 在 bring-up 后闲置约 30s 即进入不可恢复状态**。这与「CP 休眠后需 OOB `pub_int` 唤醒、而该 GPIO 在本板不可用」高度一致：CP 一旦自行进入低功耗/休眠，host 无法把它唤回，表现为之后所有命令超时。

注：`slp_mgr_drv_sleep()` 已是 `if (0 && ...)`（host 侧从不主动允许休眠），但 CP 仍失联 → 怀疑是**固件侧自身的 idle 行为**，或闲置后 inband 中断链路彻底失活。

### 21.4 现状与下一步候选（未完成）

- ✅ connect 路径去随机 MAC、§19 trailer 兜底、100ms RX 轮询、8s 超时、关 host assert —— 这些让驱动**能完整 bring-up 且不再被 host 打死**。
- ❌ 仍**无法稳定扫描/连接**：CP 在 bring-up 后约 30s 闲置即失联，所有命令 8s 超时。
- 下一步候选（按优先级）：
  1. **bring-up 后立即、连续扫描**（不留 30s 闲置）验证「趁 CP 未休眠就用」是否能扫到/连上 → 可快速证伪/证实「闲置休眠」假设。
  2. 若属闲置休眠：找**固件级关闭省电**的途径（bring-up 后立刻下发 disable-PS 命令；或 marlin 配置不进 sleep；或周期 keepalive 命令在 idle 阈值前保活）。
  3. 复核 `power_notify`/`marlin` 在 idle 是否仍把 CP 切到低功耗（即便 `slp_mgr` 已禁），定位真正的休眠下发点。

### 21.5 本会话改动文件清单（本地仓库 = 待同步 release）

- `uwe5622_driver_src/unisocwifi/cfg80211.c`：connect 去随机 MAC（已在 §20 记录）。
- `uwe5622_driver_src/unisocwifi/cmdevt.h`：`CMD_WAIT_TIMEOUT` 3000→8000（带注释说明 CP 迟到回包）。
- `uwe5622_driver_src/unisocwifi/Makefile`：注掉 `-DATCMD_ASSERT`（超时非致命，附 mstime+cmd_id 安全性说明）。
- `uwe5622_driver_src/unisocwcn/sdio/sdiohal_common.c`：`sdiohal_rx_down()` 限时轮询兜底（100ms）。
- `uwe5622_driver_src/unisocwcn/sdio/sdiohal_main.c`：新增 `sdiohal_inband_int_ack()`（备用）。
- `uwe5622_driver_src/unisocwcn/sdio/sdiohal.h`：声明 `sdiohal_inband_int_ack`。
- BSP Makefile（盒子 `~/uwe5622_driver/unisocwcn/Makefile`）：回 INBAND。

### 21.6 经验教训（本会话）

1. **关掉「兜底/自毁」机制才能看清真因**。`-DATCMD_ASSERT` 的 carddump 把"果"提前到很像"因"；关掉后才暴露「CP 自己 ~30s 闲置失联」这一真正根因。
2. **响应有 `mstime+cmd_id` 双校验时，命令超时可安全降级为非致命**——不会因迟到响应错配而损坏后续命令。
3. **OOB 与 INBAND 是非此即彼的硬件能力**：本板 OOB 唤醒 GPIO 不通，反过来意味着「CP 休眠不可唤回」，这既堵死了 OOB-RX，也是 INBAND 下「闲置失联」的同一个硬件根因。
4. **单独重编子模块要带 BSP 头路径与符号表**（`UNISOC_BSP_INCLUDE` / `KBUILD_EXTRA_SYMBOLS`），否则跨模块符号 undefined。

## 22. 会话 9（2026-06-01 续）— 反编译对照定位「闲置失联」真正下发点：`FW_PWR_DOWN_ACK`

> 从反编译 vendor `uwe5621_wifi_sdio` 继续诊断 §21.3 的「CP bring-up 后约 30s 闲置即永久失联」。**找到了 §21 漏掉的关键路径**：让 CP 进入不可唤回深睡的，不是 BSP 层 `slp_mgr`（§21 已禁），而是 **WiFi 驱动层固件主动发起的 `WIFI_EVENT_FW_PWR_DOWN` 事件 + host 的 `sprdwl_fw_power_down_ack` 回复 `value=1`（同意断电）**。

### 22.1 排除：BSP slp_mgr 已彻底禁睡，不是它

`unisocwcn/sleep/slp_mgr.c`：§21 的 `if (0 && ...)` 使 `cp2_state` 恒为 `STAY_AWAKING`；`slp_mgr_wakeup()` 因此每次直接 `return 0`（不做 `ap_wakeup_cp`）。即 **host 侧从不让 CP 睡、也从不尝试唤醒**。但 CP 仍 30s 失联 → 必有一条**独立于 BSP slp_mgr** 的睡眠下发路径。

### 22.2 定位：WiFi 层 fw-initiated power-down（与 BSP slp_mgr 完全独立）

`unisocwifi/cmdevt.c` 事件分发 `WIFI_EVENT_FW_PWR_DOWN(0xf8 区段)` → `sprdwl_event_fw_power_down` → 排 `SPRDWL_WORK_FW_PWR_DOWN` work → `sprdwl_fw_power_down_ack()`：

```c
// 无待发 TX（idle）时：
p->value = 1;            // ← 告诉固件「同意你断电深睡」
intf->fw_power_down = 1;
...
sprdwl_cmd_send_recv(... WIFI_CMD_POWER_SAVE ...);   // 把 ACK 发给 CP
if (intf->fw_power_down == 1) {
    sprdwcn_bus_allow_sleep(WIFI);   // 再放行 BSP 侧
    sprdwl_unboost();
}
```

固件收到 `value=1` 后**自行深睡**。本板 OOB `sdio_pub_int` 唤醒 GPIO 不可用（§20.4/§21.1 已判死）→ **CP 一旦深睡再也唤不回**。

之后任何 cmd 的下场（`unisocwifi/tx_msg.c::sprdwl_tx_work_queue`）：
- **CMD（SCAN/SET_IE 等）**：第 1313/1369 行**无条件** `sprdwl_tx_cmd()` 直接发——即便 CP 已断电也照发 → 无响应 → 8s（`CMD_WAIT_TIMEOUT`）超时。**完全吻合 §21.3 的「90s 起每条 SCAN 整 8s 超时」**。
- 只有 **DATA** 路径（1373 行）才在 `fw_power_down==1` 时调 `sprdwcn_bus_sleep_wakeup(WIFI)` + `host_wakeup_fw`，而前者因 slp_mgr 被禁是 no-op，后者命令本身也超时 → 唤不回。

### 22.3 反编译对照：vendor 逻辑与我们逐字节一致（证明 value 语义 + 证实 vendor 靠 OOB 唤回）

vendor `uwe5621_wifi_sdio` 的 `sprdwl_fw_power_down_ack`（`decompile/F120.c`）：

```c
*data = 6;                       // sub_type = SPRDWL_FW_PWR_DOWN_ACK
if (v9 <= 0 && 两条 list 空) {    // idle：无待发 tx
    data[1] = 1;                 // value = 1（同意断电）→ 与我们一致
    hw_priv[1462] = 1;           // fw_power_down = 1
}
...
sprdwl_cmd_send_recv(...);
if (hw_priv[1462] == 1) {
    (*(bus_ops+264))(8);         // sprdwcn_bus_allow_sleep(WIFI=8)
    sprdwl_unboost();
}
```

- **协议语义确认**：`value=1`=允许固件断电，`value=0`=拒绝（保持唤醒）。
- vendor 之所以敢回 `value=1`，是因为它在**真实 amlogic 内核 + OOB GPIO** 环境下能把 CP 唤回；mainline 本板这条唤回路不通 → 我们绝不能回 `value=1`。

### 22.4 修复：WiFi 层永不允许固件断电（对齐 §21 的 BSP slp_mgr 修法）

`unisocwifi/cmdevt.c::sprdwl_fw_power_down_ack`，把「idle 时回 value=1」改成**无条件拒绝**：

```c
/* MAINLINE FIX: never let the firmware power down. OOB sdio_pub_int wake
 * GPIO not functional on this meson-g12a port; once the CP deep-sleeps it
 * never returns -> ~30s after bring-up every SCAN/SET_IE times out. */
p->value = 0;            // 始终拒绝断电
intf->fw_power_down = 0;
intf->fw_awake = 1;
```

随后 `if (intf->fw_power_down == 1) { sprdwcn_bus_allow_sleep(WIFI); ... }` 因恒为 0 不再执行 → CP 永远不深睡。这是 §21 BSP slp_mgr 「never sleep」在 **WiFi 层的对应补丁**（§21 只补了 BSP 层，漏了这条 WiFi 层 fw-initiated 路径）。

> 同时清理：删除唯一 `goto err` 后不可达的 `err:` 标签块（避免 `-Werror` 未用标签告警）。

### 22.5 编译 / 加载 / 验证（待盒子上确认）

只改了 WiFi 模块（`cmdevt.c`），BSP 无需重编：

```bash
cd ~/uwe5622_driver/unisocwifi
rm -f Module.symvers
UNISOC_BSP_INCLUDE=$HOME/uwe5622_driver/unisocwcn/include \
KBUILD_EXTRA_SYMBOLS=$HOME/uwe5622_driver/unisocwcn/Module.symvers \
  make -C /lib/modules/$(uname -r)/build M=$PWD CC=gcc-15 HOSTCC=gcc-15 modules
bash ~/loadwifi.sh
```

**预期验证点**：
- dmesg 出现 `sprdwl_fw_power_down_ack, value=0, fw_pwr_down=0, fw_awake=1`（确认走了拒绝分支）。
- bring-up 后**静置 >30s（关键：复现 §21 的闲置窗口）**，再 `iw dev wlan0 scan` → 不再 8s 超时、能稳定扫到 AP。
- 进一步做关联 + DHCP 端到端验证。

> 若静置后仍失联，则 fw 深睡可能并非只由 `FW_PWR_DOWN_ACK` 触发（还有 fw 自带 idle doze）；下一步候选：bring-up 后立即下发 `sprdwl_power_save(SPRDWL_SET_PS_STATE, disable)` 显式关固件省电，或周期 keepalive。但本补丁是**必要前提**——只要还回 `value=1`，CP 必被允许深睡。

### 22.6 本会话改动文件清单

- `uwe5622_driver_src/unisocwifi/cmdevt.c`：`sprdwl_fw_power_down_ack` 无条件拒绝固件断电（`value=0`），删除不可达 `err:` 标签。

## 23. 会话 10（2026-06-01 续）— 回退到 git commit 基线 + 实测排除 DTS 是回归点

> 用户提示「`uwe5622_driver_src` 的 commit 是能扫描的版本，可 git diff 对比/回退」「dts 也被改了，关键点可能是 dts」。本会话据此把代码与设备树都回退到 commit 基线，在盒子上逐项实测——**结论：commit 基线在当前盒子上同样扫不到，且 DTS（OOB 节点极性、SDIO 速率）对结果无影响，DTS 不是回归点。真正阻塞仍是 CP 固件在 bring-up 后约 15~30s 进入不可恢复状态（与 §21.3 / §22 一致）。**

### 23.1 git 现状

- 仓库仅 1 个 commit `60e92e3 init`（= §19 trailer 兜底 + slp_mgr 禁睡 + OOB `/wifi` 节点，**已含 §19 修复**）。
- 工作区相对 commit 改了 8 个文件（= §20/§21/§22 的改动）。本会话先 `git stash` 回到 commit，验证完再 `git stash pop` 恢复。

### 23.2 实验 A：sdiohal_rx_down 回退到阻塞式 → 证实 §21 的 100ms 投机轮询是 -110 洪流来源

把 §21 的 `wait_for_completion_interruptible_timeout(100ms)+投机读` 退回 commit 的纯阻塞 `wait_for_completion_interruptible`：
- `pt_read fail ret:-110` 计数 **1795 → 1**（投机空读正是 -110 洪流的来源，且非致命改动已防 carddump）。
- 但**扫描仍 0 BSS**、仍有 cmd 超时 → CP 变哑**不是**投机轮询引起（阻塞式下 -110 仅 1 条仍变哑）。

### 23.3 实验 B：整体回退到 commit（代码+模块）→ 基线同样扫不到

`git stash` 后把 8 个文件全部部署到盒子，重编 BSP + WiFi 两模块（`uwe5621_bsp_sdio.ko` / `sprdwl_ng.ko`），重启冷启动加载：
- `wlan0` 注册（DORMANT），bring-up 正常（`chip_model:0x2355` 读取成功）。
- `get_cp2_version` 在 **+3.04s** 才回（commit 的 3s `CMD_WAIT_TIMEOUT` 差 46ms 错过 → `didn't get CP2 version`；这正是 §21.4 / §22 记录的「CP 迟到回包」）。
- **scan #1 / #2 均 0 BSS**；随后 connect 的 `WIFI_CMD_SET_IE` 超时 → commit 的 `-DATCMD_ASSERT` 触发 `sprdwl_atcmd_assert → mdbg_assert_interface` → **CP carddump（5 次）**。
- 即 commit 基线**比 §21/§22 更差**（§21 去掉 ATCMD_ASSERT 后至少不被 host 打死）。

### 23.4 实验 C：对比三方 DTB，定位 DTS 真实差异（唯一差异是 OOB 节点极性）

解编对比 §19 release 包内 DTB（已知能扫）、盒子当前 DTB、repo DTS：

| 来源 | `sd@ffe03000` SDIO | `/wifi` OOB 节点 |
| --- | --- | --- |
| release `m401a_uwe5621ds_wifi.dtb`（§19 已知能扫） | sdr12/25/50/**sdr104** + `max-frequency=200MHz` | `interrupts=<0x5f 0x04>` = GPIOX_18 **LEVEL_HIGH** |
| 盒子当前 `/boot/.../*.dtb`（17:17 改过） | **完全相同** | `interrupts=<0x53 0x08>` = **LEVEL_LOW**（§20.4 LOW 实验残留） |
| repo commit DTS | **完全相同** | `0x5f 0x04`（= release，HIGH） |

- **三方 SDIO 节点字节级相同**（sdr104 + 200MHz），唯一差异是 OOB `/wifi` 节点中断极性 HIGH vs LOW。
- 把盒子 DTB 恢复成 release 的 HIGH 版并冷启动：`/proc/interrupts` **无 `sdiohal` 项**（OOB IRQ 根本没被申请，`gpio_num:0`/`irq:[inband]`）→ **OOB 节点惰性，极性 HIGH/LOW 对结果无影响**。
- HIGH DTB + bring-up 后**立即连续扫描**（不留闲置，验证 §21.4 候选1）：scan 仍 0 BSS；CP 在 ~105s `swd_dump_arm_reg`/`marlin_hold_cpu` **固件 dump 崩溃**。

### 23.5 实验 D：把 SDIO 降速到 50MHz high-speed（去掉所有 UHS）→ 仍崩、仍扫不到

参考同模组 vendor `g12a_u212_4g.dts`：其 WiFi SDIO **也用 sdr104+200MHz**（`caps2/caps` 含 `MMC_CAP_UHS_SDR104`，`f_max=200MHz`）——区别在 vendor 用 amlogic `aml_sdio` 驱动（正确做 SDR104 tuning），mainline 用 `meson-gx-mmc`。为排除 mainline SDR104 tuning 不稳，做保守变体：`sd@ffe03000` 去掉 `sd-uhs-sdr12/25/50/sdr104`、`max-frequency` 改 50MHz：
- dmesg 确认生效：`new high speed SDIO card`、`clock=50000000`。
- scan #1/#2/#3 仍 **0 BSS**；CP 仍 **dump（5 次）**。

→ **SDIO 速率（200MHz/sdr104 ↔ 50MHz/HS）对 CP 崩溃与扫描结果均无影响，排除速度因素。**

### 23.6 本会话结论

1. **DTS 不是回归点**：OOB `/wifi` 节点惰性（极性无关），SDIO 速率无关；§19 能扫时本就是 sdr104/200MHz。
2. **commit 基线在当前盒子也扫不到**，说明回归**不在**本地源码的 §20/§21/§22 改动里（这些反而比 commit 更稳，去掉了 host 主动 carddump）。
3. **真正阻塞**：CP 固件 bring-up 后约 15~30s 进入不可恢复状态（dump 是 host `ATCMD_ASSERT` 超时主动打的「果」），与 §21.3 / §22 定位的「fw-initiated power-down + 本板 OOB 唤回不可用」一致。
4. **新增疑点**：即便 CP 短暂存活、命令不超时的窗口内，`iw scan` 也返回 0 BSS（而 §19 曾扫到多 AP）。需查 scan 结果事件（`scan_done` + BSS 上报）的 RX 上报路径是否在当前环境下丢失，或环境（固件 `wcnmodem.bin` / 内核）较 §19 已漂移。

### 23.7 下一步候选（按优先级）

1. **必做前提**：部署 §22 的 `FW_PWR_DOWN_ACK` 拒绝断电补丁（`value=0`），再叠加 bring-up 后**立即下发显式关固件省电** `sprdwl_power_save(SPRDWL_SET_PS_STATE, disable)`，看能否止住 CP 失联。
2. 若 CP 能稳定存活后扫描仍 0：插桩 `WIFI_EVENT_SCAN_DONE` / BSS frame 上报路径，确认 scan 结果事件是否到达 host（RX 大包/多包路径）。
3. 比对 §19 当时与现在的 `wcnmodem.bin` 与内核版本，排除环境漂移。

### 23.8 盒子当前状态（便于续作）

- 盒子 `~/uwe5622_driver` 源码 = **commit 基线**（本会话部署），运行的 .ko = commit 版（含 ATCMD_ASSERT，会 host dump）。
- 盒子 `/boot/dtb/amlogic/m401a_uwe5621ds_wifi.dtb` = **降速 50MHz 实验版**（`/tmp/slow.dtb`）；原 17:17 版备份在同目录 `*.box1717bak`；release HIGH 版在 `/tmp/rel_dtb.dtb`。
- 本地 `uwe5622_driver_src` 工作区 = 已 `git stash pop` 恢复 §20/§21/§22 全部改动（领先 commit 的版本）。

## 24. 会话 11（2026-06-01 续）— 用 release 完整包恢复部署，确认「首扫能扫到」+ 定位「首扫后 CP 失联」

> 用户提供工作区 `release/`（记录在案的**完整已知能扫产物**：`uwe5621_bsp_sdio.ko` + `sprdwl_ng.ko` + `m401a_uwe5621ds_wifi.dtb`(HIGH) + `firmware/wcnmodem.bin` + ini + `INSTALL.md` + `SHA256SUMS.txt`），要求用它恢复部署测试。**结论：release 确实能扫（冷启动首扫 BSS=36）；扫描方法是之前漏的关键变量；真正未解的是「首扫成功后 CP 进入收命令不回包的休眠态」，且不由 FW_PWR_DOWN、host assert、固件 ini 省电开关任一控制。**

### 24.1 关键校验：固件未漂移、release 二进制与我的 commit 编译产物不同

- 盒子 `/lib/firmware/uwe5622/wcnmodem.bin` md5 = release 一致（`8b7df595…`）→ **固件不是回归变量**。
- release `.ko` 与「我从 `init` commit 重编的 `.ko`」**体积/内容均不同**（bsp 4894992 vs 4895904）→ **commit 源码 ≠ release 二进制对应的源码状态**；release 才是真正记录在案的已知能扫物。
- INSTALL.md 还原出**正确扫描方法**：`ip link set wlan0 up` → `iw dev wlan0 scan trigger` → `sleep` → `iw dev wlan0 scan dump`。之前 §23 用阻塞式 `iw dev wlan0 scan` 测得 BSS=0 **有测量假象成分**。

### 24.2 三组实测（均冷启动 + trigger/dump 方法 + 立即首扫 + 静置 40s 再扫）

| 配置 | scan#1 | scan#2/#3(闲置后) | carddump | 说明 |
| --- | --- | --- | --- | --- |
| **release**（§19 .ko + HIGH DTB + 原 ini PS=2） | **BSS=36** | 0 | **7（CP 被打死）** | release 含 `ATCMD_ASSERT`：connect 的 `WIFI_CMD_SET_IE` 超时 → `sprdwl_atcmd_assert → mdbg_assert_interface` → CP carddump |
| **工作区 §20/21/22**（HIGH DTB + ini PS=2） | **BSS=30** | 0 | **0** ✓ | §21 去 `ATCMD_ASSERT` 生效：不再 host dump；但首扫后 CP 仍失联，scan cmd 8s 超时 |
| **工作区 + ini `power_save_switch=0`** | **BSS=36** | 0 | 0 | 固件 ini 省电开关改 0 **无效**：CP 首扫后照样失联 |

### 24.3 「首扫后失联」的确切性质（决定性诊断）

静置后触发 scan，同时看命令流与中断计数：
- `WIFI_CMD_SCAN[11] rsp timeout`——**SCAN 命令本身超时，CP 不回包**（TX 照常发出无错）。
- `/proc/interrupts` 的 `ffe03000.sd` inband IRQ：`37102 → 37118`，扫描窗口内**仍 +16** → **不是漏中断**（SDIO 中断线在动）。
- 0 carddump、0 `swd_dump`、`sprdwl_fw_power_down_ack` 日志**从未出现** → **不是** host assert、**不是** §22 的 FW_PWR_DOWN 路径。

→ CP 在**首扫成功后**自行进入「接收命令但永不回包」的状态。本路径**独立于** §22 的 fw-initiated power-down（该事件这次根本没发），也不受固件 ini `power_save_switch` 控制。即存在**第三条、纯固件内部的 idle/scan-after 状态机**，host 侧目前无已知开关可关。

### 24.4 阶段性结论

1. ✅ **release 能扫**（冷启动首扫 BSS=36）；硬件 + 固件 + release 二进制 + HIGH DTB 这套是好的。
2. ✅ **工作区 §20/21/22 严格优于 release**：同样首扫能扫到（BSS=30），且 §21 去 `ATCMD_ASSERT` 后**不再被 host 打死**（0 carddump）。
3. ✅ **扫描方法**必须用 `scan trigger`+`scan dump`（+`ip link set wlan0 up`），阻塞式 `iw scan` 会误报 0。
4. ❌ **唯一未解**：CP 每次冷启动**只能扫一次**，首扫后进入收命令不回包的休眠态；非 host assert、非 FW_PWR_DOWN、非固件 ini 省电开关。

### 24.5 下一步候选（按优先级）

1. **插桩对比 scan#1(成功) vs scan#2(失败) 的 RX 派发**：scan#2 时 IRQ 仍 +16，需确认这些中断带回的数据是「scan 响应被误判/丢弃」（驱动可修，回到 §19 calc_validlen 同类问题）还是「纯心跳无 payload」（CP 侧）。开 `sdiohal` RX 详细日志 + 打印每包 `type/len/cmd_id`。
2. **反编译 vendor 驱动**：看 vendor 在**每次 scan 之后**是否有固定的「复位/重新 arm RX」或「保活」命令序列（我们可能漏发），使其能连续多次扫描。
3. **bring-up 后周期 keepalive**：若属固件 idle doze，定时（<首扫后失联阈值）下发轻量命令保活，作为兜底。

### 24.6 盒子当前状态（便于续作）

- 运行 .ko = **工作区 §20/21/22 版**（`~/uwe5622_driver` 下，scans 一次 + 0 carddump）。
- DTB = **release HIGH 版**（`/boot/dtb/amlogic/m401a_uwe5621ds_wifi.dtb`，md5 `f50abacd…`）。
- 固件 = release `wcnmodem.bin`（未漂移）；ini = **`power_save_switch=0` 实验版**（无效，可改回 release 的 `=2`）。
- 完整已知能扫 release 在盒子 `~/release_known/`（`sha256sum -c` 全 OK），本地 `release/`。

## 25. 会话 12（2026-06-01 续）— 反编译确认「无漏发命令」+ 重大突破：真凶是 connect 不是 scan

> 用户要求查反编译 vendor `uwe5621_wifi_sdio` 是否有「每次 scan 后固定的 re-arm RX / 保活序列」是我们漏发的。**结论分两层：(1) 反编译带符号，vendor 驱动 == 我们源码，逐函数一致，代码层面无漏发命令；(2) 顺藤摸瓜做了「停掉 NetworkManager 后连续扫描」实验，证明扫描本身完全稳定可连续，之前所有「只能扫一次 / CP 闲置失联」的现象，真凶是 NetworkManager 自动连接的 `WIFI_CMD_SET_IE` 把 CP 挂死。**

### 25.1 反编译是带符号的 → vendor 与我们同源同版本

`uwe5621_wifi_sdio/decompile/*.c` 每个文件头有 `func-name:` 注释（IDA 符号化）。逐一核对：
- `sprdwl_scan_done`(6F0C.c)、`sprdwl_sched_scan_done`(70B4.c)、`sprdwl_tx_work_queue`(1F4AC.c)、`sprdwl_fw_power_down_ack`(F120.c) 等与我们 `unisocwifi/*` **逐行一致**。
- `sprdwl_scan_done` **扫描完成后不发任何命令**（只 `cfg80211_scan_done` 上报 + 清 `scan_request`）→ **不存在「scan 后 re-arm/保活」序列**。

### 25.2 反编译还原 vendor 的 CP 唤醒机制（SDIO 命令，非 GPIO）

`sprdwl_tx_work_queue`(1F4AC.c) 仅在 `fw_power_down==1` 且有数据待发时走唤醒分支：
```c
bus_ops[272](WIFI=8);            // sprdwcn_bus_sleep_wakeup
intf->fw_power_down = 0;
sprdwl_work_host_wakeup_fw(vif); // → work id=17
```
`sprdwl_cmd_host_wakeup_fw`(D8E8.c)：
```c
_sprdwl_cmd_getbuf(..., cmd=5 /*WIFI_CMD_POWER_SAVE*/, 0xD0);
data[0]=7; /*SPRDWL_HOST_WAKEUP_FW*/ data[1]=0;
sprdwl_cmd_send_recv(..., 3000ms, &rbuf);  // 等 CP 回 rbuf==1
```
- 唤醒是 **`WIFI_CMD_POWER_SAVE` sub_type=7 的 SDIO 命令**，不是 GPIO。
- **此路径只在 `fw_power_down==1` 触发**；§22 把 `fw_power_down` 强制恒 0，等于关掉它。但下面证明本场景压根用不到它。

### 25.3 决定性实验：停掉 NetworkManager 后连续扫描全部成功

`systemctl stop NetworkManager`（去除自动连接干扰）后用工作区模块 + release DTB/固件，冷启动连续扫描：

| 扫描 | 结果 |
| --- | --- |
| scan#1 | BSS=33 |
| scan#2 | BSS=46 |
| scan#3 | BSS=48 |
| scan#4 | BSS=50 |
| scan#5（静置 45s 后） | BSS=35 |

**0 carddump、0 SET_IE 超时、0 SCAN 超时。**

→ **扫描本身完全稳定、可连续、静置后照样扫；CP 根本不会自己 idle doze。**

### 25.4 真凶定位：connect 的 `WIFI_CMD_SET_IE` 挂死 CP

开着 NM 时 dmesg：
```
[62.1s] WIFI_CMD_SET_IE[25] rsp timeout → sprdwl_cfg80211_connect failed
        之后 SET_IE 反复超时(num=18,19,21,22,23,26,...)，所有后续命令(含 SCAN)全超时
```
- NM 在首扫拿到结果后**自动连接保存的网络** → `sprdwl_cfg80211_connect` 第一条命令 `sprdwl_set_ie(SPRDWL_IE_ASSOC_REQ, sme->ie, sme->ie_len)`(cfg80211.c:1841) → **SET_IE 超时 → CP 挂死** → 之后 scan#2 等全部超时。
- 之前 §21.3「CP bring-up 后约 30s 闲置失联」、§22「fw idle doze / FW_PWR_DOWN」**全是误判**：那 30s 里其实是 NM 在反复尝试 connect，SET_IE 把 CP 打死。`fw_power_down_ack` 日志从未出现也印证了——CP 从没走过 FW_PWR_DOWN 协议。

### 25.5 修正认知

1. ✅ **扫描子系统完全可用**（连续、稳定、抗闲置）。§19 trailer 兜底 + §21 去 ATCMD_ASSERT 已足够稳。
2. ❌ **唯一真 bug = connect 路径**：`WIFI_CMD_SET_IE(ASSOC_REQ)` CP 无响应而超时（这是 §20 一直没解决的 connect 失败，本质所在）。
3. ⚠️ scan 路径通常不带额外 IE（普通 `iw scan` 无 `ie_len`），所以 **ASSOC_REQ 很可能是 `WIFI_CMD_SET_IE` 第一次被真正下发**——需查 SET_IE 命令本身（payload 组装/长度/对齐）为何 CP 不回。

### 25.6 下一步（聚焦 connect/SET_IE）

1. **插桩 `sprdwl_set_ie`**：打印 type/len/前若干字节，对比 scan 偶发的 PROBE_REQ SET_IE（若有）与 connect 的 ASSOC_REQ SET_IE，看是否大 payload/特定 IE 触发。
2. **反编译对照 `sprdwl_set_ie`(cmdevt.c:1210) 与 vendor**：确认 payload 组装一致（同源应一致，重点看 length/fragment 边界）。
3. **临时可用方案**：scan-only 用途可直接 `nmcli dev set wlan0 managed no` / 停 NM，扫描即完全可用。
4. 若 SET_IE 是大包 TX 问题，关联到 §19 的 SDIO 大包/CMD53 路径（RX 已兜底，TX 侧可能仍有量级问题）。

### 25.7 盒子当前状态

- 运行工作区 §20/21/22 模块 + release HIGH DTB + release 固件 + ini PS=0。
- **NetworkManager 已 `systemctl stop`（未 disable，重启会自启）**；停掉后扫描连续稳定。
- 复现「能连续扫」：重启 → `sudo systemctl stop NetworkManager` → `bash ~/loadwifi.sh` → `ip link set wlan0 up` → `iw dev wlan0 scan trigger; sleep5; iw dev wlan0 scan dump`。

## 26. 会话 13（2026-06-01 续）— 固化 scan-only + 提交并 push + connect 初步对照

### 26.1 scan-only 已固化（开机自启，已重启验证）

盒子上落地（脚本 `~/setup_scanonly.sh`，本地 `/Users/weijiangchen/tmp/setup_scanonly.sh`）：
1. **wlan0 设为 NM unmanaged**：`/etc/NetworkManager/conf.d/zz-99-unmanaged-wlan0.conf`
   （`unmanaged-devices=interface-name:wlan0;interface-name:p2p-dev-wlan0`）。NM 不再自动连接 → connect 的 SET_IE 不会被触发 → CP 不被打死，扫描持续可用。
2. **驱动装入模块树**：`/lib/modules/$(uname -r)/extra/{uwe5621_bsp_sdio,sprdwl_ng}.ko` + `depmod -a`。
3. **systemd 自启**：`uwe5621-wifi.service`（`After=multi-user.target NetworkManager.service` + `ExecStartPre=/bin/sleep 20`）→ `/usr/local/sbin/uwe5621-loadwifi.sh`。
4. **加载脚本带重试**：早期 boot 加载会 `wait scan card time out / chip power on fail`（SDIO/pwrseq 未就绪竞态），脚本最多重试 5 次（失败则 rmmod 重来）。`logger -t uwe5621` 记录。

**重启验证**：service active、wlan0 自动 UP（attempt 1）、零干预连续扫描 BSS#1=31/#2=41、0 carddump、0 超时。✅

### 26.2 源码已提交并 push

- commit `5e747c5`（origin `github.com:NullYing/UWE5621DS-WIFI-Driver-For-Armbian.git` master）：8 个文件（§20/21/22 全部改动），commit message 详述每项+已知 connect 未通。
- release 已本地备份：`/Users/weijiangchen/tmp/release.bak_20260601_221929` + `*.tar.gz.bak_*`（`sha256sum -c` 全 OK）。

### 26.3 connect 初步对照：SET_IE payload 组装与 vendor 一致（排除对齐 bug）

- 我们 `sprdwl_set_ie`(cmdevt.c:1210) vs vendor `sprdwl_set_ie`(B428.c)：**逐行一致**。
- `struct sprdwl_cmd_set_ie` 是 `__packed`（`type@0, __le16 len@1, data@3`，头 3 字节）= vendor 的 `_sprdwl_cmd_getbuf(priv, len+3, ...)`。**无对齐错位 bug**。
- 失败序列（开 NM 时）：命令 1~17 正常，**第 18 条 = connect 的 `SET_IE(ASSOC_REQ)` 无响应超时**，之后全挂。
- 触发条件分析：普通 `iw scan` 不带 IE，所以 `SET_IE(ASSOC_REQ)` 很可能是 `WIFI_CMD_SET_IE` **第一次真正下发**；wpa_supplicant 只有在扫描结果里命中目标 AP 后才发 assoc IE → **必须用真实 AP 才能触发该路径**，dummy/不存在 SSID 无法复现。

### 26.4 connect 下一步（需真实 AP）

1. 插桩 `sprdwl_set_ie`（打印 type/len/前 16 字节）+ connect 关键点；编 `sprdwl_ng` 单模块部署。
2. 用 `wpa_supplicant`（或临时 `nmcli dev set wlan0 managed yes` + `nmcli dev wifi connect`）连真实 WPA AP，抓 dmesg 看 SET_IE(ASSOC_REQ) 的 len/内容、CP 是否对该包无响应。
3. 对照点：若是大 payload → 关联 §19 的 SDIO 大包 TX；若特定 IE（RSN/WPS）触发 → 查固件对该 IE 的处理；并对照 vendor `sprdwl_cmd_connect`(47FC.c) 的后续序列（SET_IE 后还发 WIFI_CMD_CONNECT 等）。
4. **需要用户提供测试 AP 的 SSID/密码**（或允许临时让 NM 接管 wlan0 连其家里的网）。

## 27. 会话 14（2026-06-01 续）— 用真实 AP（SSID `310` / `lk310310`）攻 connect，定位到「关联成功但四次握手 EAPOL 不流动」

用户提供测试 AP：`SSID 310 / lk310310`（2.4G），`310_5G / lk310310`（5G）。全程用 `wpa_supplicant -B -i wlan0 -c /tmp/wpa310.conf -D nl80211` + 插桩 `sprdwl_ng` 调试。

### 27.1 推翻 §25/§26 的「SET_IE(ASSOC_REQ) 打死 CP」结论 —— 真凶是 SET_IE(PROBE_REQ)

插桩 `sprdwl_set_ie` 打印 `ctx/type/len/前24字节`，实测崩 CP 的是**扫描阶段**的 `SET_IE(PROBE_REQ)`，不是 connect 的 ASSOC_REQ：
```
SET_IE DBG: ctx=0 type=1(PROBE_REQ) len=150 data=7f 0b 00 00 0a 02 01 40 40 00 00 01 20 dd 69 00 50 f2 04 ...
[WIFI_CMD_SET_IE]timeout → 之后 SCAN/CLOSE 全超时 → mdbg_assert → carddump
```
- `type=1`=`SPRDWL_IE_PROBE_REQ`（cfg80211.c:1476 扫描路径）。payload = 扩展能力 IE(0x7f) + **WPS IE(dd 69, OUI 00:50:f2:04)** + P2P，共 150B。
- 普通 `iw scan` 不带 IE → 不发 SET_IE → 所以裸扫描一直没事；**wpa_supplicant/NM 的 probe-request 带 WPS/P2P IE → 发 SET_IE(PROBE_REQ) → 固件崩**。这才是「只能扫一次 / 开 NM 就挂」的真因。
- 二分确认是**内容/固件**问题，不是大包 TX 通病：bring-up 的 `DOWNLOAD_INI`（分段大包）能成功；`SET_IE` 与 vendor `B428.c` 逐字节一致、`__packed` 无错位。`iw scan ies <hex>` 在 nl80211 层就 -105，无法用于二分（未到驱动）。

### 27.2 Workaround：跳过 PROBE_REQ 的 SET_IE（cfg80211.c:1476）

```c
/* 这块固件 push WPS/P2P probe-req IE 会挂 CP；STA 连接不需要，跳过 */
wl_info("skip SET_IE(PROBE_REQ) len=%zu (CP crash workaround)\n", request->ie_len);
#if 0  ... sprdwl_set_ie(SPRDWL_IE_PROBE_REQ, ...) ... #endif
```
效果：**扫描带 IE 不再崩 CP，wpa 能完成扫描并发起关联**（之前永远卡在 SCANNING）。

### 27.3 关联成功，但 4-way 握手超时（核心发现）

跳过 PROBE_REQ 后，wpa `-d` 全流程：
```
Trying to associate with 50:88:11:aa:08:f3 (SSID='310' freq=2437)
State: ASSOCIATING -> ASSOCIATED
WPA: Association event - clear replay counter
wlan0: Authentication with 50:88:11:aa:08:f3 timed out.   ← 等不到 AP 的 EAPOL-Key msg1/4
Request to deauthenticate ... reason=3 (DEAUTH_LEAVING)
```
驱动侧插桩（fresh boot 单次，时序）：
```
[t+0.000] CONNECT DBG: ssid='310' ch=6 wpa_ver=2 pairwise=132(0x84=CCMP|VALID) group=132 akm=130(0x82=WPA-PSK) auth=0 psk_len=0
[t+0.059] CONNECT DBG: sprdwl_connect ret=0
[t+0.105] EVENT_CONNECT DBG: fw conn_info.status=0        ← 固件105ms就报关联成功（纯802.11 assoc，未做4-way）
[t+0.105] REPORT CONN: sm_state=5(CONNECTING) status=0 bssid=50:88:11:aa:08:f3 req_ie_len=97 resp_ie_len=211
          → cfg80211_connect_result(SUCCESS) 被调用，wpa 进入 ASSOCIATED
[t+~10s ] (4-way 等待窗口：SDIO 上 TX/RX 全静默) → wpa auth timeout → 发 disconnect
[t+~15s ] [WIFI_CMD_DISCONNECT]timeout → 固件不回 disconnect → CP 最终挂死
```

### 27.4 铁证：关联后零数据帧

- 关联后连续探测 `/sys/class/net/wlan0/statistics/{rx,tx}_packets` **恒为 0**（8 次采样全 0）。
- `iw dev wlan0 station dump` **空**（无 peer 表项）；`iw link` 显示 Connected（仅 802.11 关联，未 authorized）。
- 抓全量 dmesg dispatch：关联事件后 **10s 内 SDIO 上无任何 RX/TX dispatch**，直到 wpa 超时后重新扫描才出现 chn=22。
- SDIO 端口：`SDIO_RX_CMD_PORT=22`（cmd/event，刷屏可见）、`SDIO_RX_DATA_PORT=24`（数据/EAPOL）。**chn=24 从未出现** → 固件从不上送数据帧。
- **结论：802.11 关联 OK，但 AP 的 EAPOL-Key msg1/4 从未到达主机**（固件没在数据通道 chn24 上送）。主机做四次握手（`psk_len=0`，host-4-way 模型）但拿不到第一帧 → 超时。

### 27.5 已排除项

- **PSK 模型**：`psk_len=0` 是正常的（vendor `47FC.c` connect 逐行一致，WPA-PSK 时 con.psk 也来自空的 sme->key）→ host 做 4-way，vendor 同款，非 bug。
- **RSN IE**：实测 wpa 这次 `sme->ie_len==0`（无 ASSOC_REQ IE 下发），RSN 由 con.pairwise/group/key_mgmt 传给固件、固件自建 assoc-req RSN；AP 接受了关联(assoc-resp success, resp_ie_len=211) → RSN 协商 OK。恢复 ASSOC_REQ 的 SET_IE 无影响。
- **host 省电**：`iw get power_save` = off；toggle on/off 无效；rx/tx 仍 0。
- **命令通道**：4-way 窗口内 `iw link` 持续响应、无 cmd 超时 → 固件 CPU 活着；是 **RF/数据 RX 侧**没收/没上送 EAPOL。
- **CP 不是逻辑崩**：DISCONNECT 超时是「固件不回 disconnect」的次生现象（assoc 半成品状态下 disconnect 无响应）。

### 27.6 剩余根因假设（按概率）

1. **固件关联后 RF 进入 doze/省电**（与扫描期同款慢性睡眠病），AP 缓冲的 EAPOL 取不回 → chn24 永远空。我们的 slp_mgr/`fw_power_down_ack` 改动只覆盖 host 命令面，未必压住 assoc 后的 RF 省电。
2. **数据 RX 数据面未启用**：缺少 vendor Android userspace 在 connect 后下发的某条命令（如使能数据通道/credit/`NOTIFY_IP_ACQUIRED`），导致固件不把数据推给 host。
3. **固件/环境漂移**：当前 `wcnmodem.bin`/ini 与该平台数据面不完全匹配。

### 27.7 connect 下一步建议

1. 抓 vendor `47FC.c` connect **之后**的完整命令序列（callees）/ `sprdwl_event_connect` 后驱动是否还发数据面使能命令，对照我们是否漏发。
2. 深挖 SDIO **数据 RX(chn24)** 与 TX(chn10) credit 流控：connect 后固件是否授信、host 是否 arm chn24 接收。
3. 试压住 assoc 后 RF 省电：用 `WIFI_CMD_POWER_SAVE` 各 subtype（`SET_PS_STATE/SCREEN_ON_OFF`）显式置「常醒」，或 ini `power_save_switch` 与 vendor 逐项对齐后实测 chn24 是否出现 EAPOL。
4. 抓包佐证 AP 是否真的发 msg1（另一台机器开 monitor 抓 310 的 4-way），区分「AP 没发」vs「固件没上送」。

### 27.8 插桩/实验改动（工作区，未提交）

- `cfg80211.c`: `SET_IE(PROBE_REQ)` 跳过(`#if 0`) + `skip` 日志；connect 路径 `CONNECT DBG`(wl_err) + `ASSOC_REQ DBG`(wl_err)。
- `cmdevt.c`: `sprdwl_set_ie` 加 `SET_IE DBG`(wl_err)；`sprdwl_event_connect` 加 `EVENT_CONNECT DBG`；`sprdwl_report_connection` 加 `REPORT CONN DBG`。
- `sdiohal_common.c`: 去掉两条 `DBG: TX/RX dispatch` 的 `KERN_ERR` 刷屏（**注意：BSP 在此 6.18 头文件下编译报 `CARD_DETECT_WAIT_MS/PACKET_SIZE` 未定义**，故此改动**尚未编进部署的 BSP**，盒子上仍有 dispatch 刷屏；调试靠 `dmesg -w | grep` 实时抓，免环形缓冲淘汰）。
- 盒子部署的 `sprdwl_ng.ko` = 含 PROBE_REQ 跳过 + 各插桩；BSP 仍为旧（含刷屏）。**结论：scan-only 仍可用，connect 仍未通（卡在 4-way）。**

---

## 28. connect 攻关（Session 15）：vendor 后续序列对照 + 压 RF 省电实验

### 28.1 反编译对照：connect 之后 vendor 不发任何「数据面使能」命令
- `sprdwl_event_connect`(DFC4.c)：只解析事件 payload（status/bssid/channel/signal/req_ie/resp_ie/bea_ie）→ 调 `sprdwl_report_connection`，**之后无任何额外命令**。
- `sprdwl_report_connection`(773C.c)：成功路径(LABEL_53) 仅做 `netif_carrier_on()` + `_netif_schedule(tx->qdisc)`（唤醒 TX 队列），再解析 WMM、置 `SPRDWL_CONNECTED`。**没有任何 credit/数据通道使能/NOTIFY_IP 命令。**
- **我们的 `sprdwl_report_connection`(cfg80211.c:2457) 已有等价逻辑**：`if(!netif_carrier_ok) { netif_carrier_on; netif_wake_queue; }`。
- ⇒ **「connect 后漏发数据面使能命令」假设排除**：vendor 与我们一样，数据面仅靠 `netif_carrier_on`，我们没漏发。
- 旁证：vendor `report_connection` 要求 `req_ie_len`/`resp_ie_len` 均非零否则判失败；我们实测 req=97/resp=211 均非零，符合成功路径。

### 28.2 WIFI_CMD_POWER_SAVE subtype 梳理（cmdevt.h:360）
`SCREEN_ON_OFF=1 / SET_FCC_CHANNEL=2 / SET_TX_POWER=3 / SET_PS_STATE=4 / SUSPEND_RESUME=5 / FW_PWR_DOWN_ACK=6 / HOST_WAKEUP_FW=7`。
「常醒」= `SET_PS_STATE(4) value=0`（关 802.11 PS）。vendor `sprdwl_power_save`(B088.c) 仅由 ioctl/`set_power_mgmt`/suspend 调用，**无 init 默认下发**；固件 PS 默认来自 ini `power_save_switch`（已=0）。

### 28.3 实验：强制常醒（已撤回）
两处改：(1) `set_power_mgmt` 强制下发 value=0；(2) connect 成功后立即注入 `SET_PS_STATE=0`。重启 ×2 实测连 `310`：
- `CONNECT DBG: ret=0` + `forcing SET_PS_STATE=0` 都打出，但**固件不再报 EVENT_CONNECT/REPORT CONN**（关联未完成），chn=24 无、rx/tx=0，~15s 后 `WIFI_CMD_DISCONNECT timeout`。
- 对比：不注入时之前能拿到 `EVENT_CONNECT`(105ms, 关联成功)。
- `set_power_mgmt DBG` **从未打印** → wpa_supplicant 根本没走 set_power_mgmt 路径。

### 28.4 结论：PS 不是 4-way 失败的根因
1. connect 后立刻注入 PS 命令会**打断正在进行的关联**（有害，反而连 assoc 都不成）。
2. wpa 不调用 `set_power_mgmt`，该路径强改无效。
3. 固件 ini `power_save_switch` 本就=0；且**此前关联成功的运行里 chn=24 同样静默、rx=0**——PS off 也救不了 EAPOL。
⇒ 已**全部撤回** PS 实验改动，盒子重新部署干净基线（关联可成功的版本）。

### 28.5 收敛后的根因判断
assoc 成功（AP 回 assoc-resp，resp_ie_len=211）⇒ AP 认为 STA 已关联 ⇒ AP **会发 EAPOL msg1**；但 host 侧 chn=24（数据 RX）**从不触发**、rx_packets=0 ⇒ **固件收到/未收到 EAPOL 但没经 chn=24 上送 host**。这是**固件数据 RX 侧问题**，非 host 命令面、非 PS、非数据面 API 漏发。

### 28.6 下一步（唯一高价值实验）
**另一台机器开 monitor 抓 `310` 信道的 4-way**，区分：
- AP 发了 msg1 但 host chn24 没收 → 固件数据 RX/credit 问题（需对照 vendor TX/RX credit 初始化或换 fw/ini）。
- AP 压根没发 msg1 → assoc 在 AP 侧未真正完成（4-way 前置状态问题）。

## 29. 会话 16（2026-06-02）— 改用官方 `uwe5621ds-amlogic` 驱动：扫描打通，connect 仍卡同一处（确认与驱动源无关）

> 用户要求尝试 `uwe5621ds-amlogic`（`github.com:liangxiwei/uwe5621ds-amlogic`，**专为 amlogic armbian 定制的同源驱动树**，区别于一直在用的 leopad 移植版 `uwe5622_driver`）。结论：移植到 6.18 后**扫描完全打通**（与 leopad 版同等稳定），但 **connect 卡在完全相同的位置**（802.11 关联后 AP 的 EAPOL 不经 chn=24 上送）。两份**不同来源**的驱动失败方式逐字一致 → **再次铁证：connect 失败是固件数据面问题，与驱动源码无关。**

### 29.1 这份驱动 vs leopad 版的关键差异
- 配置项是 `CONFIG_ARMBIAN_WIFI_DEVICE_UWE5621`（顶层 Makefile `obj-$(CONFIG_ARMBIAN_WIFI_DEVICE_UWE5621) += unisocwcn/`），wifi 模块仍是 `sprdwl_ng`，BSP 仍是 `uwe5621_bsp_sdio`。
- 比 leopad 版**移植更完善**：已内置 §13 的 `CONFIG_PM_SLEEP` wake_lock 修复、`sched_set_fifo`、5.4+ 的 `wakeup_source_register(dev,name)`。
- **默认不带 `-DRND_MAC_SUPPORT`**（天然避开 §20 的随机 MAC 坑）。
- 固件走 `DOWNLOAD_FIRMWARE_FROM_HEX`（`unisocwcn/fw/wcnmodem.bin.hex` 内嵌进 .ko），无需 request_firmware。

### 29.2 6.18 编译适配（在 leopad §3.3 基础上额外需要）
源码同步到盒子 `~/uwe5621ds-amlogic/`，应用：
1. **wakeup_source**（BSP 层 `wcn_txrx.c/sdio_int.c/sdiohal_common.c`、`tty-sdio/lpm.c`）：`wakeup_source_create`+`_add` → `wakeup_source_register(NULL, ...)`；`_destroy` → `_unregister`。
2. **timer API**：`del_timer_sync`→`timer_delete_sync`、`from_timer`→`timer_container_of`、`del_timer`→`timer_delete`。
3. **`set_wiphy_params`**：6.13+ 加 `int radio_idx` 参数。
4. **`get_tx_power`**（本树新增，leopad 版无此问题）：6.13+ 签名变为 `(wiphy, wdev, int radio_idx, unsigned int link_id, int *dbm)`。
5. **`MODULE_IMPORT_NS`**：6.13+ 改为接收**字符串字面量** `MODULE_IMPORT_NS("VFS_internal_...")`（`wl_core.c`/`wcn_usb.c`/`lpm.c`）。

编译命令（BSP 段 key 是 `CONFIG_ARMBIAN_WIFI_DEVICE_UWE5621=y`）：
```bash
cd ~/uwe5621ds-amlogic/unisocwcn
make -C /lib/modules/$(uname -r)/build M=$PWD CONFIG_ARMBIAN_WIFI_DEVICE_UWE5621=y CC=gcc-15 HOSTCC=gcc-15 modules
cd ~/uwe5621ds-amlogic/unisocwifi; rm -f Module.symvers
UNISOC_BSP_INCLUDE=$HOME/uwe5621ds-amlogic/unisocwcn/include \
UNISOC_WIFI_CUS_CONFIG="/lib/firmware/uwe5621ds/" \
UNISOC_WIFI_MAC_FILE="/lib/firmware/uwe5621ds/wifimac.txt" \
KBUILD_EXTRA_SYMBOLS=$HOME/uwe5621ds-amlogic/unisocwcn/Module.symvers \
make -C /lib/modules/$(uname -r)/build M=$PWD CONFIG_ARMBIAN_WIFI_DEVICE_UWE5621=y CC=gcc-15 HOSTCC=gcc-15 modules
```

### 29.3 ★决定性根因：ARMBIAN 配置缺 BOARD 宏 → `sdio_config` 发成 0x0（CP 永不回数据）
首次加载：bring-up 全过（chipid `0x56630001`、固件下载成功、CP sync 到 `0xf0f0f0ff`），但 **`get_cp2_version`/`SYNC_VERSION` 超时**。即使把 §19 的 RX trailer 兜底 + §19.2 的 slp_mgr 禁睡眠都移植过来仍超时。

插桩 RX 路径发现：inband IRQ 每 **1s** 触发一次（心跳），每次读回**恒定空帧** `3f 46 83 ff ff ff ff ff`（解码 `eof=1` → 解析器立即 break），**RX 解析器从未拿到任何真实包**。

追到 `wcn_boot.c::marlin_send_sdio_config_to_cp_vendor()`：构建真实 SDIO 配置（`sdio_config_en=1` + inband irq 类型 + blksize）的整段代码被
```c
#if (defined(CONFIG_HISI_BOARD) || defined(CONFIG_AML_BOARD) || \
     defined(CONFIG_RK_BOARD)   || defined(CONFIG_AW_BOARD))
   ... 真实配置 ...
#else
   sdio_cfg.val = 0;   /* ← ARMBIAN 落到这里 */
#endif
```
包住。**ARMBIAN 配置段没定义任何 BOARD 宏** → `sdio_config` 发成 `0x0 (disable config)` → CP 没被告知用什么方式通知 host 有 RX → **CP 永不上送命令/数据响应**。这正是 leopad 版当年靠 `CONFIG_AML_BOARD` 才让 `sdio_config` 等于 `0x8c0f31` 的同一处。

**修复**（`unisocwcn/Makefile` ARMBIAN 段，约 357 行加一行）：
```makefile
ifeq ($(CONFIG_ARMBIAN_WIFI_DEVICE_UWE5621),y)
ccflags-y += -DCONFIG_AML_BOARD      # ← 关键：否则 sdio_config=0x0
ccflags-y += -DCONFIG_WCN_POWER_UP_DOWN
ccflags-y += -DCONFIG_WCN_DOWNLOAD_FIRMWARE_FROM_HEX
ccflags-y += -DCONFIG_SDIO_BLKSIZE_512
ccflags-y += -DCONFIG_BT_WAKE_HOST_EN
ccflags-y += -DCONFIG_SDIO_INBAND_INT
```
`CONFIG_AML_BOARD` 会引入 amlogic 厂商内核私有函数（`wifi_irq_num/wifi_irq_trigger_level/sdio_reinit/sdio_clk_always_on/sdio_set_max_reqsz/extern_wifi_set_enable/extern_bt_set_enable`）和 `<linux/amlogic/aml_gpio_consumer.h>` —— 全部用 no-op stub 替代（mainline 上电由 DTS wifi-pwrseq 完成、RX 走 inband，不需要它们）：
- 新建 `unisocwcn/platform/aml_stubs.c`（7 个 no-op，`wifi_irq_num` 返回 -1）。
- 新建 `unisocwcn/include/linux/amlogic/aml_gpio_consumer.h`（定义 `GPIO_IRQ_HIGH/LOW` + `mmc_power_save/restore_host` no-op）。
- `Makefile` obj 列表加 `platform/aml_stubs.o`。

加 `CONFIG_AML_BOARD` 后：`sdio_config:0x8c0f31 (enable config)`、`irq:[inband]`，**`wlan0` 注册、扫描连续稳定（BSS 30/39/43）、0 carddump、0 timeout**。

### 29.4 移植过来的 host 侧必备补丁（与 leopad 同源问题）
- **§19 RX trailer 兜底**（`sdiohal_rx.c`）：新增 `sdiohal_rx_calc_validlen()`（hdr-walk 重算长度）+ `force_drain` 排空 + `rx_dtbs=0` 恒定读长。meson-gx-mmc 不可靠 trailer 在本树同样存在。
- **§19.2 slp_mgr 禁睡眠**（`slp_mgr.c`）：`if (0 && slp_mgr.active_module == 0)`。
- **关闭 `-DATCMD_ASSERT`**（`unisocwifi/Makefile`）：cmd 超时不再 host 主动 carddump（§21）。
- **§27.2 跳过 `SET_IE(PROBE_REQ)`**（`cfg80211.c` 扫描路径 `#if 0`）：wpa/NM 的 probe-req WPS/P2P IE 会崩 CP，STA 连接不需要。

### 29.5 connect 实测：与 leopad 逐字节相同的失败
`wpa_supplicant -i wlan0 -c wpa310.conf -D nl80211` 连 `310/lk310310`：
```
Trying to associate with 50:88:11:aa:08:f3 (SSID='310' freq=2437)
Authentication with 50:88:11:aa:08:f3 timed out.
CTRL-EVENT-DISCONNECTED reason=3 locally_generated=1
```
驱动侧：**只收到 chn=22（cmd/event），从未出现 chn=24（数据/EAPOL）**，`rx_packets=0`，随后 `WIFI_CMD_DISCONNECT` 超时 → CP carddump。**与 §27.4 完全一致。**

### 29.6 本会话结论
1. ✅ **官方 `uwe5621ds-amlogic` 驱动已在 6.18 打通扫描**，并定位+修复了它自身的 ARMBIAN 配置缺陷（缺 BOARD 宏 → sdio_config=0x0），这是本树独有、leopad 版没有的坑。
2. ❌ **connect 仍卡在 4-way（EAPOL 不经 chn=24 上送）**，与 leopad 版**完全相同**。
3. ⇒ **换驱动源不能解决 connect**。两份独立来源（leopad 社区移植 + liangxiwei 官方 amlogic 定制）在同一固件 + 同一 meson SDIO 主控下，connect 失败点逐字一致，**根因确定在固件数据面 / meson-gx-mmc 数据 RX 通路**，非 wifi driver 可改。§28.6 的 monitor 抓包仍是唯一高价值下一步。

### 29.7 产物与盒子状态
- 盒子源码：`~/uwe5621ds-amlogic/`（含全部上述补丁，clean 构建已去除调试插桩）。
- 模块：`~/uwe5621ds-amlogic/unisocwcn/uwe5621_bsp_sdio.ko`（6.5MB，含内嵌固件）+ `~/uwe5621ds-amlogic/unisocwifi/sprdwl_ng.ko`。
- 加载脚本：`~/loadwifi_aml.sh`（cfg80211 + 这两个 .ko）。
- 本地源码：`/Users/weijiangchen/tmp/uwe5621ds-amlogic/`（补丁已同步：Makefile/aml_stubs.c/aml_gpio_consumer.h/sdiohal_rx.c/slp_mgr.c 为本地直接编辑；cfg80211.c 的 set_wiphy_params/get_txpower/timer/PROBE_REQ 改动仅在盒子上，本地 cfg80211.c 仅含 get_txpower）。
- DTB/固件沿用 §19/§24 的 release 版（HIGH DTB + `wcnmodem.bin` 未漂移），扫描无关 OOB 节点。

