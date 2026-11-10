"""字体辅助脚本：从 .ttc/.otc 集合里抽出单个 face，或把字体压成 woff2。

由 main.py 用系统 python3 调用，避免依赖 Decky 内嵌解释器里是否有 fontTools。

用法：
    python3 face_extract.py extract <集合文件> <face 序号> <输出文件>
    python3 face_extract.py woff2   <输入字体>        <输出文件>
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

    fail(f"未知模式 {mode}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
