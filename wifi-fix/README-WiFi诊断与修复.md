# WiFi "密码错误" 真相与修复记录

日期：2026-09-19
现象：飞牛界面里连 WiFi 一直报**密码错误**，2.4G 和 5G 都一样。

---

## 一、结论先行：**根本不是密码问题**

wpa_supplicant 的真实报错是：

```
wlan0: Trying to associate with 22:6b:7d:52:f8:22 (SSID='1F' freq=2412 MHz)
wlan0: Association request to the driver failed          ← ★ 驱动层拒绝
wlan0: CTRL-EVENT-SSID-TEMP-DISABLED auth_failures=1 reason=CONN_FAILED
```

用 `iw` 直接尝试可以看到真正的错误码：

```
$ iw dev wlan0 connect 1F
command failed: Operation already in progress (-114)     ← ★ -EALREADY
```

**`-EALREADY` 来自 cfg80211 内核层**：`struct wireless_dev` 里的连接状态标志
（`connecting` / `connected`，6.12 里的实现）**还挂着没释放**。
wpa_supplicant 把这个错误翻译成 "Association request to the driver failed"，
飞牛界面再翻译成"密码错误"——**密码从来没人校验过**。

---

## 二、根因：连接结果事件丢失 → cfg80211 永远在等

cfg80211 的约定是：`cfg80211_connect()` 发起连接后，驱动**必须恰好上报一次**结果
（`cfg80211_connect_result()` 或 `cfg80211_disconnected()`），否则标志不会被清除，
之后**所有** connect 都返回 `-EALREADY`。

而 `sprdwl_ng`（UWE5621DS 驱动）的 `sprdwl_report_connection()` 有 **3 条静默丢弃路径**：

| 位置 | 条件 | 后果 |
|---|---|---|
| `cfg80211.c:2295` | `sm_state` 不是 CONNECTING/CONNECTED | **直接 return，不上报** |
| `cfg80211.c:2464` `err:` | 只在 `sm_state == CONNECTING` 才上报 | 其他状态**不上报** |
| `sprdwl_report_disconnection` | 只在 CONNECTING/CONNECTED/DISCONNECTING 上报 | 其他状态**不上报** |

而且 **固件对失败的关联尝试可能根本不回事件**，驱动也没有超时机制
→ `wdev->connecting` 悬空 → 网卡此后**永久不可用，只能重启**。

### 触发条件：切换 WiFi 网络

正常日志（开机首次连接）：
```
wpa_supplicant: Trying to associate with ... SSID='1F' freq=2412
wpa_supplicant: Associated with 22:6b:7d:52:f8:22        ← ✅ 成功
```

一旦切换网络（尤其在飞牛界面点连 5G）就坏掉。飞牛的行为：
```
[19 11:54:19.468] [nm] add-activate connection '1F-5G-wlan0' success    ← 建临时连接去试
[19 11:54:34.793] [nm] delete connection '1F-5G-wlan0' success          ← 15 秒后删掉
```
这个"试连→失败→删除"的过程会中断正在进行的连接，留下未上报的连接，
之后 2.4G 和 5G **全都**报"密码错误"。

---

## 三、已实施的修复

### 补丁 1：`sprdwl_ng` 保证上报连接结果（已应用）

`unisocwifi/main.c` + `cfg80211.c`，引入 `fnnas_conn_pending` 标志：

1. `sprdwl_cfg80211_connect()` 发出连接命令成功后置位
2. 所有可能丢弃事件的地方，**只要还有未上报的连接就先报失败**
3. 成功/失败上报后清位

详见 `sprdwl-devaddr-fix.patch` 同目录的 `sprdwl-connleak-fix.patch`。

### 补丁 2（已应用）：`dev_addr` 直写修复

见 `README-WiFi修复.md`（内核 6.11+ `dev_addr` 变 const 指针，
驱动仍用 `memcpy` 直写 → `dev_addr_check()` 失败 → `__dev_open` 失败）。

---

## 四、已排除的假设（都验证过）

| 假设 | 验证结果 |
|---|---|
| **5G 被法规域禁止** | ❌ 排除。`iw phy phy0 info` 显示 `* 5220 MHz [44] (20.0 dBm)`，**可主动使用**（`iw reg get` 里的 PASSIVE-SCAN 是 global 域，phy0 域没有该标记） |
| **`country 00` 导致** | ❌ 排除。设了 `cfg80211 ieee80211_regdom=CN` 后 phy0 信道依旧可用，5G 仍然因 `-EALREADY` 失败 |
| **eMMC / U 盘供电** | ❌ 排除（本次问题无关） |
| **模块重载可免重启恢复** | ❌ **不可行**。`rmmod uwe5621_bsp_sdio` 必段错误：<br>`sdiohal:sdiohal_exit entry → sdiohal:[sdiohal_remove]enter → Oops`<br>崩溃在 `sdiohal_remove+0xac`，`driver_unregister` → `sdiohal_remove` 回调。<br>试过在 exit 末尾（NULL 解引用）和开头（仍在 remove 里崩）都不行。<br>上游驱动卸载路径本身有缺陷，**放弃**。 |

> 因此：**WiFi 卡死后的恢复手段 = 重启。**
> （`rmmod sprdwl_ng` 本身是安全的 rc=0，但 `uwe5621_bsp_sdio` 不能动。）

---

## 五、实际使用建议

| 场景 | 建议 |
|---|---|
| **日常使用** | 用 **2.4G**（`1F`）。开机自动连接，实测 **104 Mbit/s**，外网 38ms |
| **不要做** | 在飞牛界面里点"连接"切换 WiFi 网络（尤其 5G）。这是把驱动搞坏的**唯一**触发点 |
| **如果坏了** | 重启盒子即可恢复（约 1 分钟） |
| **5G** | 技术上信道可用，但驱动在"切换网络"时状态机会卡死，**暂不建议作为主力** |
| **网络需求** | 盒子网口(eth0)只有 100Mb，2.4G 的 104Mbit 已经跑满网口带宽，够用 |

---

## 六、当前生效的模块指纹

```
sprdwl_ng.ko           112948314ff2f3072812d9414f1a5496   （dev_addr + conn-leak 双补丁）
uwe5621_bsp_sdio.ko    81c6e12e6c001acbdca52b741d1c51a3   （重编译，已回退 sdiohal 补丁）
```

其他相关配置：

```
/etc/NetworkManager/conf.d/99-wifi-mac.conf   wifi.scan-rand-mac-address=no
/etc/modprobe.d/cfg80211-regdom.conf          options cfg80211 ieee80211_regdom=CN
/etc/modules-load.d/uwe5621.conf              cfg80211 / uwe5621_bsp_sdio / sprdwl_ng
/etc/modprobe.d/uwe5621.conf                  softdep sprdwl_ng pre: ...
/etc/iproute2/rt_tables                       route_wlan0(10) / route_eth0(11)
```

---

## 七、复现验证结果（关机后冷启动）

```
wlan0:connected:1F   192.168.3.83/24   carrier=1
  SSID 1F  freq 2412  signal -46 dBm
  tx bitrate 104.0 MBit/s VHT-MCS 5 VHT-NSS 2
ping -I wlan0 223.5.5.5 → 0% 丢包, 38ms
ip rule: 10: from 192.168.3.83 lookup route_wlan0
dmesg: dev_addr_check=0  Oops=0
```
