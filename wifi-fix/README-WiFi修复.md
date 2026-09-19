# WiFi 深度修复：dev_addr 直写 bug

修复时间：2026-09-19
模块：sprdwl_ng.ko（UWE5621DS WiFi 网络驱动）

---

## 一、现象

`wlan0` 永久 DOWN，`carrier=0`，`ip link set wlan0 up` 无效。
NetworkManager 报 `The Wi-Fi network could not be found`。
内核日志刷屏：

```
wlan0: start set random mac: 0e:1b:0c:e3:63:03
wlan0: Current addr:  0e 1b 0c e3 63 03 ...
wlan0: Expected addr: a0 67 20 18 b5 3f ...
netdevice: wlan0: Incorrect netdev->dev_addr
WARNING: at net/core/dev_addr_lists.c:519 dev_addr_check+0xac/0x13c
         __dev_open+0x40/0x204
         do_setlink / rtnl_newlink
```

## 二、根因

内核 **6.11 起** `struct net_device` 的地址管理改版：

```c
/* include/linux/netdevice.h (6.12) */
const unsigned char  *dev_addr;              /* ← const 指针 */
u8                   dev_addr_shadow[MAX_ADDR_LEN];
/* 注释：Copy of @dev_addr to catch direct writes. */
```

`dev_addr` 指向 rbtree 节点，`dev_addr_shadow` 是内核影子副本。
**所有修改必须走 `dev_addr_set()` / `eth_hw_addr_set()`**，它会同时更新两者：

```c
void dev_addr_mod(...)          /* net/core/dev_addr_lists.c */
{
	dev_addr_check(dev);
	ha = container_of(dev->dev_addr, struct netdev_hw_addr, addr[0]);
	rb_erase(&ha->node, &dev->dev_addrs.tree);
	memcpy(&ha->addr[offset], addr, len);          /* rbtree */
	memcpy(&dev->dev_addr_shadow[offset], addr, len); /* shadow */
	WARN_ON(__hw_addr_insert(...));
}
```

而驱动 `unisocwifi/main.c` 的 `sprdwl_set_mac()`（`.ndo_set_mac_address`）里：

```c
memcpy(dev->dev_addr, sa->sa_data, ETH_ALEN);   /* 947 行 —— 直写 const 指针 */
memcpy(dev->dev_addr, vif->mac, ETH_ALEN);      /* 952 行 —— 且 vif->mac 从未被赋值(恒为全0) */
```

直写只改了 rbtree，**shadow 不同步** → `memcmp` 失配。
内核 `dev_addr_check()`：

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

触发链：**NM 用一次 netlink `do_setlink` 同时设 MAC + 起网卡**
→ `sprdwl_set_mac` 直写导致失配
→ `__dev_open` 调 `dev_addr_check` 失败
→ **网卡永远打不开**。

> 编译器其实早就警告了：
> `warning: passing argument 3 of 'sprdwl_set_mac_addr' discards 'const' qualifier`
> `expected 'u8 *' but argument is of type 'const unsigned char *'`

## 三、修复

`unisocwifi/main.c` `sprdwl_set_mac()`：

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

（第二处同时修掉了 `vif->mac` 恒为全 0 的隐患，改用固件上报的真实 MAC `priv->mac_addr`。）

## 四、验证结果

```
wlan0  UP  192.168.3.83/24   carrier=1  operstate=up
  MAC=a0:67:20:18:b5:3f
  SSID 1F  2412MHz  -44dBm  tx 78.0 MBit/s VHT-MCS 4 VHT-NSS 2
  ping 223.5.5.5 → 0% 丢包 38ms

dev_addr_check 错误: 0      (修复前每次联网必报)
Incorrect netdev  : 0
scan_done error   : 0
marlin_probe ok   : 1
download finished : 1

飞牛策略路由:
  10: from 192.168.3.83  lookup route_wlan0
  11: from 10.10.10.109 lookup route_eth0
```

## 五、配套的 NetworkManager 加固

`/etc/NetworkManager/conf.d/99-wifi-mac.conf`：

```ini
[device]
wifi.scan-rand-mac-address=no
```

禁止 NM 扫描时随机化 MAC，减少不必要的 MAC 变更（与驱动补丁形成双重保险）。

## 六、重新编译方法

```bash
cd /root/uwedrv/unisocwifi
UNISOC_BSP_INCLUDE=/root/uwedrv/unisocwcn/include \
KBUILD_EXTRA_SYMBOLS=/root/uwedrv/unisocwcn/Module.symvers \
make -C /lib/modules/$(uname -r)/build M=$PWD ARCH=arm64 \
     CONFIG_UWE5622=m CONFIG_SPRDWL_NG=m modules
cp sprdwl_ng.ko /lib/modules/$(uname -r)/extra/
depmod -a
reboot
```
