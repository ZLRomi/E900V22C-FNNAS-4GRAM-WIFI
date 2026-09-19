#!/bin/bash
#================================================================================================
# E900V22C (S905L3A) FnNAS 完整镜像构建脚本：在官方 ophub/fnnas s905l3a 镜像上仅注入生产版 dtb
#
# 产物:
#   $OUTDIR/fnnas_amlogic_s905l3a_e900v22c-4g-wifi_k<VER>_<DATE>.img.gz  完整可刷镜像
#   $OUTDIR/meson-g12a-s905l3a-e900v22c.dtb                             生产版 dtb(单独附)
#
# 用法:
#   sudo bash scripts/build_full_img.sh [官方img.gz URL] [生产dtb路径] [输出目录]
#   (需要 root 以使用 losetup 挂载 img)
#================================================================================================

set -euo pipefail

# ---------- 参数 / 默认值 ----------
BASE_URL="${1:?缺少官方 img.gz 下载地址}"
# 例: https://github.com/ophub/fnnas/releases/download/fnnas_amlogic_1252/fnnas_amlogic_s905l3a_k6.18.18_2026.06.25.img.gz
DTB_SRC="${2:-dtb-variants/v8b-uwe5621ds-3.97g-ddr52.dtb}"
OUTDIR="${3:-out/full}"

WORK="$(mktemp -d build_full.XXXXXX)"
trap 'cd /; sudo umount -f "$WORK/boot" 2>/dev/null || true; sudo losetup -d "$LOOP" 2>/dev/null || true; rm -rf "$WORK"' EXIT

# ---------- 0. 校验生产 dtb 指纹 ----------
echo "[1/6] 校验生产 dtb (md5 应为 719a1ef85279e71a3b9964e0c123ca0a)"
md5sum "${DTB_SRC}"
DTB_BASENAME="meson-g12a-s905l3a-e900v22c.dtb"

# ---------- 1. 下载官方镜像 ----------
echo "[2/6] 下载官方镜像"
sudo apt-get install -y -qq gzip util-linux dosfstools >/dev/null 2>&1 || true
curl -fL --retry 3 -o "${WORK}/base.img.gz" "${BASE_URL}"
gunzip -f "${WORK}/base.img.gz"                                     # -> base.img

# ---------- 2. 挂载 BOOT 分区 ----------
echo "[3/6] 挂载 BOOT 分区"
sudo losetup -fP "${WORK}/base.img"
LOOP="$(losetup -j "${WORK}/base.img" | cut -d: -f1 | head -n1)"
mkdir -p "${WORK}/boot"
sudo mount -o rw "${LOOP}p1" "${WORK}/boot"

# ---------- 3. 替换 dtb (生产版覆盖同名) ----------
echo "[4/6] 替换 dtb"
DTB_DIR="${WORK}/boot/dtb/amlogic"
[[ -d "${DTB_DIR}" ]] || { echo "未找到 dtb/amlogic 目录!"; exit 1; }
sudo cp "${DTB_SRC}" "${DTB_DIR}/${DTB_BASENAME}"
echo "已覆盖: ${DTB_DIR}/${DTB_BASENAME}"

# 校验 uEnv.txt 的 FDT 指向同名文件(无需改)
if [[ -f "${WORK}/boot/uEnv.txt" ]]; then
  grep -E '^FDT=' "${WORK}/boot/uEnv.txt" || echo "(无 FDT 行, 用默认 dtb)"
fi

# ---------- 4. 卸载并重新打包 ----------
echo "[5/6] 卸载并重新打包"
sudo umount -f "${WORK}/boot"
sudo losetup -d "${LOOP}" && LOOP=""

DATE="$(date +%Y.%m.%d)"
KVER="$(basename "${BASE_URL}" | sed -E 's/.*_k([0-9.]+)_[0-9.]+.*/\1/')"
IMG_NAME="fnnas_amlogic_s905l3a_e900v22c-4g-wifi_k${KVER}_${DATE}"
mkdir -p "${OUTDIR}"
gzip -c9 "${WORK}/base.img" > "${OUTDIR}/${IMG_NAME}.img.gz"
cp "${DTB_SRC}" "${OUTDIR}/${DTB_BASENAME}"

# ---------- 5. 校验 ----------
echo "[6/6] 校验产物"
gzip -t "${OUTDIR}/${IMG_NAME}.img.gz" && echo "gzip OK"
ls -lh "${OUTDIR}"
echo
echo "完成: ${OUTDIR}/${IMG_NAME}.img.gz"
echo "建议发布说明中标注: 基于官方 ${BASE_URL##*/} 注入生产版 dtb (md5 719a1ef85279e71a3b9964e0c123ca0a)"