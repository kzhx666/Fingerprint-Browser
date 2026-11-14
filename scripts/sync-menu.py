# -*- coding: utf-8 -*-
"""交互式同步菜单（给 同步数据.bat 调用；中文全在这里，避免 cmd 编码问题）"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("kzsync", os.path.join(HERE, "sync-data.py"))
kz = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kz)


def line(s=""):
    print(s)


def main():
    line("=" * 52)
    line("  多开实例数据同步（书签 / 历史 / 密码 / 插件）")
    line("=" * 52)
    line()

    insts = kz.list_instances()
    if not insts:
        line("还没有任何实例。先用 启动管理器.bat 建一个并启动一次。")
        return
    line("可用实例：")
    for n in insts:
        p = kz.profile_dir(n)
        marks = []
        for label, f in (("书签", "Bookmarks"), ("历史", "History"),
                         ("密码", "Login Data"), ("插件", "Extensions")):
            if os.path.exists(os.path.join(p, f)):
                marks.append(label)
        line("  %-18s %s" % (n, ("有: " + "/".join(marks)) if marks else "（还没启动过）"))
    line()
    line("提示：同步时两个浏览器都要关掉；目标实例必须先启动过一次。")
    line()

    src = input("源实例名（复制上面的名字）: ").strip()
    if not src:
        line("取消。")
        return
    if src not in insts:
        line("没有这个实例: %s" % src)
        return

    dst = input("目标实例（多个用英文逗号分隔）: ").strip()
    if not dst:
        line("取消。")
        return

    line()
    line("同步什么？直接回车 = 全部")
    line("  1) 全部")
    line("  2) 只要书签")
    line("  3) 书签 + 历史")
    line("  4) 书签 + 历史 + 密码")
    line("  5) 自定义（输入英文逗号分隔的类型）")
    choice = input("选择 [1]: ").strip() or "1"
    mapping = {
        "1": "all",
        "2": "bookmarks",
        "3": "bookmarks,history",
        "4": "bookmarks,history,passwords",
    }
    what = mapping.get(choice)
    if what is None:
        what = input("输入类型（bookmarks,history,passwords,extensions）: ").strip()
        if not what:
            line("取消。")
            return

    line()
    argv = ["--from", src, "--to", dst, "--what", what]
    sys.argv = ["sync-data.py"] + argv
    spec2 = importlib.util.spec_from_file_location(
        "kzsync_main", os.path.join(HERE, "sync-data.py"))
    m2 = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(m2)
    m2.main()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        line("\n已取消。")
