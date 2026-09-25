#!/bin/bash
# ============================================================================
# E900V22C (S905L3A) 专用：内核 OTA 拦截器
#
# 为什么需要它：
#   `fnnas-update` 默认追 `ophub/fnnas kernel_fnnas` 的同系列最新内核。
#   一旦升到非 6.12.41（例如 6.12.x-latest / 6.18.y）：
#     1. /boot/dtb/amlogic 下的 4G+WiFi dtb 会被内核自带的 stock 2G dtb 覆盖
#        （内存掉回 2G，wifi@1/cap-sdio-irq/pwm 时钟全丢）；
#     2. /usr/lib/modules/<新版本>/extra 下没有双 ko，且 vermagic≠6.12.41-trim，
#        insmod 直接拒绝，板载 WiFi 永久失效。
#   而飞牛 Web 的 trim 应用 OTA（liveupdate，只换 /usr/trim）不受影响，放行。
#
# 行为：
#   - 显式 `-k 6.12.41...`（同版本重装修复）：放行；
#   - 其它（含无参默认追新）：拒绝并指路，除非带 `--force-kernel-ota`；
#   - `--force-kernel-ota`：放行但警告（dtb/驱动后果自负）。
# 安装：rc.local 首次启动时把本文件装到 /usr/sbin/fnnas-update，
#       原文件备份为 /usr/sbin/fnnas-update.real（幂等，只装一次）。
# ============================================================================
REAL_BIN="/usr/sbin/fnnas-update.real"
PIN_PREFIX="6.12.41"

FORCE=0
ARGS=()
KVAL=""
WANT_KVAL=0
for a in "$@"; do
    if [ "$a" = "--force-kernel-ota" ]; then
        FORCE=1
        continue
    fi
    if [ "$WANT_KVAL" = "1" ]; then
        KVAL="$a"
        WANT_KVAL=0
        ARGS+=("$a")
        continue
    fi
    case "$a" in
        -k|--Kernel)
            WANT_KVAL=1
            ARGS+=("$a")
            ;;
        -k*)
            KVAL="${a#-k}"
            ARGS+=("$a")
            ;;
        --Kernel=*)
            KVAL="${a#--Kernel=}"
            ARGS+=("$a")
            ;;
        *)
            ARGS+=("$a")
            ;;
    esac
done

allow_pinned() {
    case "$KVAL" in
        "$PIN_PREFIX"*) return 0 ;;
        *) return 1 ;;
    esac
}

if [ "$FORCE" = "1" ]; then
    echo "[e900v22c-guard] --force-kernel-ota:放行（警告：非 $PIN_PREFIX 内核会丢 4G dtb + WiFi，需重刷本仓库镜像修复）" >&2
    exec "$REAL_BIN" "${ARGS[@]}"
fi

if [ -n "$KVAL" ] && allow_pinned; then
    exec "$REAL_BIN" "${ARGS[@]}"
fi

cat >&2 <<EOF
[e900v22c-guard] 已拦截本次内核更新，保护 4G 运存 + 板载 WiFi。
  原因：本机锁定内核 $PIN_PREFIX-trim（WiFi vermagic + 0xf5700000 dtb），
  追新内核会覆盖 dtb 并使双 ko 失效。
  允许：仅同版本重装，例如 fnnas-update -k $PIN_PREFIX
  强行：加 --force-kernel-ota（后果自负，事后需重刷 e900v22c 镜像）。
  注意：飞牛 Web 的 trim 应用更新不受影响，可正常点。
EOF
exit 1
