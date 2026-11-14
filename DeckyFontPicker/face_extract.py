"""字体辅助脚本：从 .ttc/.otc 集合里抽出单个 face、把字体压成 woff2，
或按剔除码位区间生成剔字子集（方案 D：西文槽去中日韩/PUA 字形）。

由 main.py 用系统 python3 调用，避免依赖 Decky 内嵌解释器里是否有 fontTools。

用法：
    python3 face_extract.py extract <集合文件> <face 序号> <输出文件>
    python3 face_extract.py woff2   <输入字体>        <输出文件>
    python3 face_extract.py subset  <输入字体> <face 序号> <输出文件> <剔除区间串>

subset 模式的退出约定（main.py 据此判定）：
    退出码 0 + 产出文件   -> 已生成剔字子集
    退出码 0 + 无产物     -> cmap 与剔除区间无交集，无需剔字（绝大多数西文字体）
    退出码 1              -> 失败（字体损坏 / 区间串不合法 / 剔完无保留码位等）
"""

import os
import sys


def fail(message, code=1):
    sys.stderr.write(str(message) + "\n")
    sys.exit(code)


def main(argv):
    if len(argv) < 4:
        fail("参数不足")
    mode = argv[1]

    try:
        from fontTools.ttLib import TTCollection, TTFont
    except Exception as exc:
        fail(f"fontTools 不可用: {exc}")

    if mode == "extract":
        src, index, out = argv[2], int(argv[3]), argv[4]
        if not os.path.isfile(src):
            fail(f"找不到字体文件 {src}")
        collection = TTCollection(src, lazy=True)
        if index < 0 or index >= len(collection.fonts):
            fail(f"{os.path.basename(src)} 只有 {len(collection.fonts)} 个 face，取不到 #{index}")
        font = collection.fonts[index]
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        tmp = out + ".part"
        font.save(tmp)
        # 保存后立刻回读一遍，确保文件完整、表结构可用
        TTFont(tmp, lazy=True).close()
        os.replace(tmp, out)
        return 0

    if mode == "woff2":
        src, out = argv[2], argv[3]
        if not os.path.isfile(src):
            fail(f"找不到字体文件 {src}")
        font = TTFont(src)
        font.flavor = "woff2"
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        tmp = out + ".part"
        font.save(tmp)
        font.close()
        os.replace(tmp, out)
        return 0

    if mode == "subset":
        # 方案 D 剔字：读 face 的 cmap，与剔除区间求交；交集非空才生成
        # 「cmap − 掩码」子集。掩码常量在 main.py（分基线版 / 含 CJK 标点版），
        # 这里只收区间串（"2E80-2FDF,4E00-9FFF,…" 十六进制），保持通用。
        if len(argv) < 6:
            fail("subset 模式参数不足：subset <输入字体> <face 序号> <输出文件> <剔除区间串>")
        src, index, out, ranges_arg = argv[2], int(argv[3]), argv[4], argv[5]
        if not os.path.isfile(src):
            fail(f"找不到字体文件 {src}")
        mask = []
        for token in ranges_arg.split(","):
            token = token.strip()
            if not token:
                continue
            lo, sep, hi = token.partition("-")
            try:
                start = int(lo, 16)
                end = int(hi, 16) if sep else start
            except ValueError:
                fail(f"剔除区间串不合法: {token}")
            if end < start:
                fail(f"剔除区间串不合法（终点小于起点）: {token}")
            mask.append((start, end))
        if not mask:
            fail("剔除区间串为空")

        from fontTools import subset as ft_subset

        if src.lower().endswith((".ttc", ".otc")):
            collection = TTCollection(src, lazy=True)
            if index < 0 or index >= len(collection.fonts):
                fail(f"{os.path.basename(src)} 只有 {len(collection.fonts)} 个 face，取不到 #{index}")
            font = collection.fonts[index]
        else:
            font = TTFont(src, lazy=True)

        cmap = font.getBestCmap()
        kept = [cp for cp in cmap if not any(lo <= cp <= hi for lo, hi in mask)]
        if len(kept) == len(cmap):
            # 交集为空：不产出文件，调用方原样使用原始 face
            print("subset: cmap 与剔除区间无交集，跳过（不产出文件）")
            return 0
        if not kept:
            fail("剔除后没有保留任何码位（该字体全部落在掩码内），拒绝产出空字体")

        options = ft_subset.Options()
        # 与实现前实测基线同参：保留全部 layout feature（kern 等）与全部 name 记录。
        # 注意子集后字形名会按 AGL 重生成，消费方一律按码位对齐，不要依赖字形名。
        options.layout_features = ["*"]
        options.name_IDs = ["*"]
        subsetter = ft_subset.Subsetter(options=options)
        subsetter.populate(unicodes=kept)
        subsetter.subset(font)
        # 输出一律为裸 sfnt（输入若是 woff/woff2，读入时 fontTools 会记下 flavor，
        # 不清掉会把 woff2 字节存进 .ttf 文件名里）
        font.flavor = None

        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        tmp = out + ".part"
        font.save(tmp)
        try:
            # 保存后立刻回读一遍，确保文件完整、表结构可用
            TTFont(tmp, lazy=True).close()
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        os.replace(tmp, out)
        return 0

    fail(f"未知模式 {mode}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
