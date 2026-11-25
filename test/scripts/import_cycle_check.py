"""import 图循环检测（架构债修复 P0 验证工具）。

用法：
    env PYTHONPATH= E:/anaconda3/python.exe test/scripts/import_cycle_check.py

收集 plugins/StoryTeller/src 下所有模块的「包内」import 依赖（相对导入与 src.* 绝对导入），
构建有向图，用 Tarjan SCC 找出强连通分量 >1 或自环的循环，打印环路径。

- 顶层图：仅模块级 import（缩进 0）——import 时真实发生的硬环。
- 全量图：顶层 + 函数/类内惰性 import——架构上的互相依赖（软环）。

退出码：有环 1，无环 0。
"""
import re
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent.parent / "plugins" / "StoryTeller" / "src"
PACKAGE_ROOT = "src"

_TOP_RE = re.compile(r"^(?:from\s+(\.+[\w.]*)\s+import|import\s+(\.+[\w.]+))")
_LAZY_RE = re.compile(r"^\s+from\s+(\.+[\w.]*)\s+import|\s+import\s+(\.+[\w.]+)")


def _module_of(path: Path) -> str:
    rel = path.resolve().relative_to(SRC_ROOT.resolve())
    parts = list(rel.parts)
    if parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join([PACKAGE_ROOT] + parts)


def _resolve_relative(base_pkg: str, rel: str) -> str:
    if not rel.startswith("."):
        return None
    dots = 0
    i = 0
    while i < len(rel) and rel[i] == ".":
        dots += 1
        i += 1
    body = rel[i:]
    base_parts = base_pkg.split(".") if base_pkg else []
    drop = dots - 1
    parts = base_parts[: len(base_parts) - drop]
    if body:
        parts += body.split(".")
    return ".".join(parts)


def _targets_from_lines(lines, base_pkg: str) -> tuple[set, set]:
    top: set = set()
    lazy: set = set()
    for line in lines:
        m = _TOP_RE.match(line)
        indented = line.startswith((" ", "\t"))
        if not m:
            if indented:
                m = re.match(r"^\s*from\s+(\.+[\w.]*)\s+import", line) or re.match(
                    r"^\s*import\s+(\.+[\w.]+)", line
                )
            else:
                continue
            if not m:
                continue
        rel = next((g for g in m.groups() if g), "")
        if not rel.startswith("."):
            continue
        target = _resolve_relative(base_pkg, rel)
        if target and target.startswith(PACKAGE_ROOT + "."):
            (lazy if indented else top).add(target)
    return top, lazy


def _deps(path: Path, mod: str) -> tuple[set, set]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set(), set()
    base_pkg = mod if path.name == "__init__.py" else (
        mod.rsplit(".", 1)[0] if "." in mod else mod
    )
    top, lazy = _targets_from_lines(text.splitlines(), base_pkg)
    # 绝对导入（src.* 或 plugins.StoryTeller.src.*）按缩进区分
    for line in text.splitlines():
        indented = line.startswith((" ", "\t"))
        mm = re.match(r"^\s*from\s+([\w.]+)\s+import", line)
        if not mm:
            mm = re.match(r"^\s*import\s+([\w.]+)", line)
        if not mm:
            continue
        name = mm.group(1)
        target = None
        if name.startswith(PACKAGE_ROOT + "."):
            target = name
        elif name.startswith("plugins.StoryTeller.src."):
            target = name.split(".", 3)[-1]
        if target and target.startswith(PACKAGE_ROOT + "."):
            (lazy if indented else top).add(target)
    return top, lazy


def _scc(graph: dict) -> list:
    index, lowlink, on_stack, stack, comps, counter = {}, {}, set(), [], [], [0]

    def strongconnect(v):
        index[v] = lowlink[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in graph.get(v, ()):
            if w not in graph:
                continue
            if w not in index:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], index[w])
        if lowlink[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.append(w)
                if w == v:
                    break
            comps.append(comp)

    for v in graph:
        if v not in index:
            strongconnect(v)
    return comps


def _cycle_path(graph: dict, comp: list) -> list:
    nodes = set(comp)
    for start in sorted(nodes):
        path = [start]
        seen = {start}
        stack = [(start, iter(sorted(graph.get(start, ()))))]
        while stack:
            node, it = stack[-1]
            advanced = False
            for nxt in it:
                if nxt in seen:
                    if nxt == start and len(path) > 1:
                        return path + [nxt]
                    continue
                if nxt in nodes:
                    seen.add(nxt)
                    path.append(nxt)
                    stack.append((nxt, iter(sorted(graph.get(nxt, ())))))
                    advanced = True
                    break
            if not advanced:
                stack.pop()
                seen.discard(path.pop())
    return []


def analyze(graph: dict, label: str) -> list:
    comps = _scc(graph)
    cycles = []
    for comp in comps:
        if len(comp) > 1:
            cycles.append(comp)
        elif len(comp) == 1 and comp[0] in graph.get(comp[0], ()):
            cycles.append(comp)
    if not cycles:
        print(f"[OK] {label}：无环（{len(graph)} 个模块）")
        return []
    print(f"[FAIL] {label}：发现 {len(cycles)} 个强连通分量")
    for comp in sorted(cycles, key=lambda c: sorted(c)):
        path = _cycle_path(graph, comp)
        if path:
            print("  环路径：" + " → ".join(path))
        else:
            print("  SCC成员：" + ", ".join(sorted(comp)))
    return cycles


def _check_target_edges(graph: dict) -> int:
    """架构债 P0-批次B 三项循环的目标断言（含惰性边在内必须不成立）。"""
    checks = [
        # ① services 不得 import plugins/adventure（boss_framework 依赖倒置已切断）
        ("src.services.boss_framework", "src.plugins.adventure"),
        # ② guest/gm_room 不得再 import adventure（插件层互导已切断）
        ("src.plugins.guest", "src.plugins.adventure"),
        ("src.plugins.gm_room", "src.plugins.adventure"),
        # ② npc 本来就不 import adventure（单向，保持）
        # ③ stats_service 不得反向依赖 ending_engine
        ("src.services.stats_service", "src.services.ending_engine"),
        # ④ flow_engine 不得反向依赖 plugins/mainline（services→plugins 倒置目标边，§2#2；待回调反转切断）
        ("src.services.flow_engine", "src.plugins.mainline"),
    ]
    fail = 0
    for src, dst in checks:
        bad = dst in graph.get(src, ())
        mark = "FAIL" if bad else "OK"
        if bad:
            fail += 1
        print(f"[{mark}] 目标边已切断断言：{src} -x-> {dst}")
    return fail


def main() -> int:
    files = [p for p in SRC_ROOT.rglob("*.py") if "__pycache__" not in p.parts]
    top_graph, all_graph = {}, {}
    for p in files:
        mod = _module_of(p)
        top, lazy = _deps(p, mod)
        top_graph[mod] = top
        all_graph[mod] = top | lazy
    fail = 0
    fail += bool(analyze(top_graph, "顶层 import 图（硬环）"))
    fail += bool(analyze(all_graph, "全量 import 图（含惰性，软环）"))
    fail += _check_target_edges(all_graph)
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
