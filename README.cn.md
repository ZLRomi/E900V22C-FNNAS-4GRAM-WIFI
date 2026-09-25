# E900V22C（S905L3A）4G 运存 + WiFi 魔改版

[English Instructions](README.md)

> 本仓库专门给创维 **E900V22C（S905L3A，4G 运存）** 跑飞牛 FnNAS 用的魔改构建。
> 基于 `ophub/fnnas`，只构建 `e900v22c` 单机型。每次 Release 只有 **2 个附件**。

## 一、这个包解决什么问题

官方镜像在 E900V22C 上有三个坑，本包全修：

### 1. 4G 运存只认到 2G

官方 dtb 的 `memory` 节点写死 2G。本包改成 **`0xf5700000`（3927MiB 可用）**，
顶部 169MiB 留给 BL31/TEE 安全区。

> 警告：不要改成 `0xfe000000`（3.97G）。看着多 143MiB，但内核 256MiB 的 CMA
> 会压到安全区上，拷贝大文件时核心进程猝死。`free -m` 显示约 3.7Gi 即正常。

### 2. eMMC 写不动

这颗 eMMC 在 HS200 高速模式下写通道不稳定。本包把 eMMC 降到
**DDR52 50MHz 时钟**（读约 81MB/s，写约 70MB/s，跑满百兆网口无压力）。

### 3. 板载 WiFi 没有驱动

紫光展锐 UWE5621DS，含驱动双模块 + dtb 四处补齐：

* `sprdwl_ng.ko` / `uwe5621_bsp_sdio.ko`（`vermagic = 6.12.41-trim`，
  已打 `dev_addr` + `conn-leak` 双补丁）
* dtb：`wifi@1` 节点、`cap-sdio-irq`、`pwm_ef` 32k 时钟、`sdio-pwrseq` 时序
* `99-wifi-mac.conf`（禁扫描随机 MAC）、`99-uwe5621ds.conf`（开机自动加载）

## 二、Release 里有什么

| 文件 | 用途 |
|---|---|
| `fnnas_amlogic_e900v22c_k6.12.41_<日期>.r<构建号>.img.xz` | 系统镜像（约 1.6GB），balenaEtcher 整盘写入 U 盘 / TF 卡（≥16GB）。`r` 后数字为构建号，保证每次发布文件名唯一，解压即得同名 `.img`，无需改名 |
| `meson-g12a-s905l3a-e900v22c.dtb` | 4G dtb，可单独替换到已装系统的 `/boot/dtb/amlogic/` |

## 三、装机步骤

1. balenaEtcher 写入 U 盘 / TF 卡，插盒开机（网线、WiFi 都会自动连）。
2. 浏览器打开 `http://<盒子IP>:5666`，创建管理员账号（首次需等几分钟扩容+初始化）。
3. （可选）摆脱 U 盘：`sudo fnnas-install -y` 全自动装到 eMMC。
   生产版脚本特性：无机型选择、沿用当前运行 dtb、仅允许 `0xf5700000` 边界安装、
   自带内存保护与拷贝校验。

## 四、在线更新规则

| 通道 | 结论 |
|---|---|
| 飞牛 Web 的 trim 应用更新 | 可以点（只换 `/usr/trim`，4G/WiFi 不受影响） |
| `apt update && apt upgrade` | 可以跑（官方源里没有本内核） |
| `fnnas-update` 内核更新 | 默认被拦截（追新内核会覆盖 4G dtb 并使驱动失效）。同版本重装用 `fnnas-update -k 6.12.41`；强行加 `--force-kernel-ota`，后果自负，事后重刷本包 |

## 五、已知限制

* 优先用 2.4G WiFi（5G 受法规域限制）；配好自动连接后不要反复手动断开重连（驱动缺陷，需重启恢复）。
* 内核锁定 `6.12.41-trim`，换内核即断 WiFi。
* 上报板型 `/etc/device_info/boot_board` 为 `e900v22c`。

## 六、构建

Actions → Build Image and 4G DTB → Run workflow（或推 `e900v22c-*` tag），
单机型约 11 分钟。CI 自带阻断式门禁：`/bin|/sbin|/lib` 必须为软链、
动态加载器存在、双驱动存在且 `vermagic` 匹配、镜像内 dtb 边界为 `0xf5700000`、
OTA 防护文件在位，否则直接失败不发布。

## 七、致谢

* [ophub/fnnas](https://github.com/ophub/fnnas) —— FnNAS Amlogic 适配与构建链
* [7Ji/ampart](https://github.com/7Ji/ampart) —— eMMC 分区工具
* [KryptonLee/uwe5621ds-aml](https://github.com/KryptonLee/uwe5621ds-aml) —— UWE5621DS 驱动原始来源
