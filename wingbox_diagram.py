"""The blast radius of a pull request as a Mermaid flowchart.

GitHub renders a ```mermaid block in a pull request comment and in the job summary, so the picture
needs no uploaded image and nothing written to the repository. The changed files stand in one box;
every module that depends on them and is not changed stands in the box of its folder; an arrow goes
from a module to what it uses. A direct dependent has a solid border and arrow, a farther one a
dashed border and arrow.

The diagram stays readable: at most NODE_CAP boxes and EDGE_CAP arrows. Over a cap, the modules of
a folder become one box with their count (the farther ones first, then the direct ones, then the
changed files, then everything by top folder); what still does not fit is left out and counted. Names never reach Mermaid as
syntax: node identifiers are generated and every label is escaped. The same modules give the same
text byte for byte, in whatever order they come. Pure functions only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from wingbox_facts import folder_of

CHANGED, DIRECT, FAR = "changed", "direct", "far"
# GitHub fits a diagram to the width of the comment: a wider one is scaled down, labels included.
# Drawn with Mermaid 11, 14 to 23 boxes take about two thirds of that width; a tangled diagram of 40
# boxes and 52 arrows takes all of it at full label size and each arrow can still be followed. More
# boxes would shrink the text, more arrows would hide each other. Mermaid itself refuses a diagram
# over 500 arrows or 50,000 characters, far above these caps.
NODE_CAP = 40
EDGE_CAP = 60
LABEL_LIMIT = 48
# Boxes closer together than Mermaid's defaults: the picture is a list read top to bottom.
_LAYOUT = '%%{init: {"flowchart": {"nodeSpacing": 12, "rankSpacing": 60, "padding": 8}}}%%'
_KIND_ORDER = {DIRECT: 0, FAR: 1}
_PLAIN = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 _./-")


@dataclass(frozen=True)
class Module:
    name: str                    # a module ID; for a changed file its path
    kind: str                    # CHANGED, DIRECT or FAR
    uses: tuple[str, ...] = ()   # the names of the changed files or modules it uses
    test: bool = False


@dataclass(frozen=True)
class Diagram:
    text: str             # the Mermaid source, without the code fence; empty when there is nothing to draw
    modules: int          # dependents drawn, one by one or inside a counted box
    hidden_modules: int   # dependents not drawn
    hidden_changed: int   # changed files with dependents that are not drawn
    hidden_edges: int     # arrows not drawn
    collapsed: bool       # some box stands for several modules or files


@dataclass(frozen=True)
class _Unit:
    """One box: a module, a changed file, or the modules of a folder under one count."""
    kind: str
    folder: str
    members: tuple[str, ...]
    label: str
    counted: bool
    test: bool = False


def escape(label: str) -> str:
    """A label that Mermaid reads as text only: letters, digits, space and `_ . / -` stay, a line break or
    another control character becomes a space, every other character its entity code (`#34;` for a quote)."""
    return "".join(
        char if char in _PLAIN else " " if ord(char) < 32 or ord(char) == 127 else f"#{ord(char)};" for char in label
    )


def shorten(label: str, limit: int = LABEL_LIMIT) -> str:
    """A label of at most limit characters: the end of a long one, after three dots."""
    return label if len(label) <= limit else "..." + label[len(label) - (limit - 3):]


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def _relative(name: str, folder: str) -> str:
    return name[len(folder):] if name.startswith(folder) and len(name) > len(folder) else name


# Collapse levels, tried in turn until the diagram fits its caps. Per level: the folder depth the farther
# modules are counted under (0: drawn one by one), the same for the direct ones and for the changed files.
_LEVELS = ((0, 0, 0), (2, 0, 0), (1, 0, 0), (1, 2, 0), (1, 2, 2), (1, 1, 1))


def _units(changed: Sequence[str], dependents: Sequence[Module], level: tuple[int, int, int]) -> tuple[list[_Unit], list[_Unit]]:
    """The boxes of one collapse level. Tests are always counted by folder. A folder with one module keeps
    the module."""
    far_depth, direct_depth, changed_depth = level
    changed_units: list[_Unit] = []
    by_folder: dict[str, list[str]] = {}
    for path in changed:
        by_folder.setdefault(folder_of(path, changed_depth) if changed_depth else path, []).append(path)
    for folder, paths in sorted(by_folder.items()):
        one = len(paths) == 1
        changed_units.append(_Unit(CHANGED, "", tuple(paths), paths[0] if one else f"{_plural(len(paths), 'file')} in {folder}", not one))
    groups: dict[tuple[str, str, bool], list[str]] = {}
    together: dict[tuple[str, str, bool], bool] = {}
    for module in dependents:
        depth = far_depth if module.kind == FAR else direct_depth
        key = (folder_of(module.name, depth or 2), module.kind, module.test)
        groups.setdefault(key, []).append(module.name)
        together[key] = module.test or depth > 0
    units: list[_Unit] = []
    for (folder, kind, test), names in groups.items():
        if together[(folder, kind, test)] and len(names) > 1:
            units.append(_Unit(kind, folder, tuple(sorted(names)), _plural(len(names), "test module" if test else "module"), True, test))
        else:
            units.extend(_Unit(kind, folder, (name,), _relative(name, folder), False, test) for name in names)
    return changed_units, units


def _edges(units: Sequence[_Unit], uses: dict[str, tuple[str, ...]]) -> list[tuple[int, int]]:
    """The arrows between boxes, as (index of the box that uses, index of the box used), each pair once."""
    box = {member: index for index, unit in enumerate(units) for member in unit.members}
    found: set[tuple[int, int]] = set()
    for index, unit in enumerate(units):
        for member in unit.members:
            for target in uses.get(member, ()):
                if target in box and box[target] != index:
                    found.add((index, box[target]))
    return sorted(found)


def _connected(count: int, edges: Iterable[tuple[int, int]]) -> list[bool]:
    linked = [False] * count
    for source, target in edges:
        linked[source] = linked[target] = True
    return linked


def _cut(changed: list[_Unit], units: list[_Unit], uses: dict[str, tuple[str, ...]], node_cap: int, edge_cap: int
         ) -> tuple[list[_Unit], list[_Unit], list[tuple[int, int]]]:
    """The boxes and arrows that fit the caps: the changed boxes most modules use; then the direct boxes before
    the farther ones, the largest first, each only when something it points at is drawn; then the arrows of
    the direct boxes before those of the farther ones; a box left without an arrow is not drawn."""
    users: dict[str, int] = {}
    for targets in uses.values():
        for target in targets:
            users[target] = users.get(target, 0) + 1
    ranked_changed = sorted(changed, key=lambda unit: (-sum(users.get(member, 0) for member in unit.members), unit.members))
    kept_changed = ranked_changed[:max(1, node_cap // 3)]
    ranked = sorted(units, key=lambda unit: (_KIND_ORDER[unit.kind], -len(unit.members), unit.test, unit.folder, unit.members))
    drawn = {member for unit in kept_changed for member in unit.members}
    kept: list[_Unit] = []
    waiting = ranked
    while waiting and len(kept) < node_cap - len(kept_changed):
        later: list[_Unit] = []
        for unit in waiting:
            if len(kept) < node_cap - len(kept_changed) and any(target in drawn for member in unit.members for target in uses.get(member, ())):
                kept.append(unit)
                drawn.update(unit.members)
            else:
                later.append(unit)
        if len(later) == len(waiting):
            break
        waiting = later
    while True:
        boxes = _ordered(kept_changed, kept)
        edges = sorted(sorted(_edges(boxes, uses), key=lambda edge: (boxes[edge[0]].kind == FAR, edge))[:edge_cap])
        linked = _connected(len(boxes), edges)
        if all(linked):
            return [unit for unit in boxes if unit.kind == CHANGED], [unit for unit in boxes if unit.kind != CHANGED], edges
        kept_changed = [unit for unit, keep in zip(boxes, linked) if keep and unit.kind == CHANGED]
        kept = [unit for unit, keep in zip(boxes, linked) if keep and unit.kind != CHANGED]


def _ordered(changed: Sequence[_Unit], units: Sequence[_Unit]) -> list[_Unit]:
    """The boxes in the order of the text: the changed ones, then the others by folder."""
    return sorted(changed, key=lambda unit: unit.members) + sorted(
        units, key=lambda unit: (unit.folder, _KIND_ORDER[unit.kind], unit.counted, unit.test, unit.members))


def _text(boxes: Sequence[_Unit], edges: Sequence[tuple[int, int]]) -> str:
    changed_count = sum(1 for unit in boxes if unit.kind == CHANGED)
    ids = [f"c{index}" if index < changed_count else f"m{index - changed_count}" for index in range(len(boxes))]
    lines = [_LAYOUT, "flowchart RL", '  subgraph pr["Changed files"]']
    for index in range(changed_count):
        label = escape(shorten(boxes[index].label))
        lines.append(f'    {ids[index]}[["{label}"]]' if boxes[index].counted else f'    {ids[index]}["{label}"]')
    lines.append("  end")
    folders: list[str] = []
    for index in range(changed_count, len(boxes)):
        unit = boxes[index]
        if not folders or folders[-1] != unit.folder:
            if folders:
                lines.append("  end")
            folders.append(unit.folder)
            lines.append(f'  subgraph g{len(folders) - 1}["{escape(shorten(unit.folder))}"]')
        label = escape(shorten(unit.label))
        lines.append(f'    {ids[index]}[["{label}"]]' if unit.counted else f'    {ids[index]}("{label}")')
    if folders:
        lines.append("  end")
    # An arrow to a changed file before an arrow to another module, for each box in turn.
    for source, target in sorted(edges, key=lambda edge: (edge[0], edge[1] >= changed_count, edge[1])):
        lines.append(f"  {ids[source]} {'-.->' if boxes[source].kind == FAR else '-->'} {ids[target]}")
    lines += ["  classDef changed stroke-width:3px", "  classDef far stroke-dasharray:5 4",
              "  class " + ",".join(ids[:changed_count]) + " changed"]
    far = [ids[index] for index in range(changed_count, len(boxes)) if boxes[index].kind == FAR]
    if far:
        lines.append("  class " + ",".join(far) + " far")
    return "\n".join(lines)


def blast_radius(modules: Iterable[Module], node_cap: int = NODE_CAP, edge_cap: int = EDGE_CAP) -> Diagram:
    """The diagram of the changed files and the modules that depend on them, within the caps."""
    by_name: dict[str, Module] = {}
    for module in sorted(modules, key=lambda module: (module.name, module.kind, module.test, tuple(sorted(module.uses)))):
        by_name.setdefault(module.name, module)
    dependents = [module for module in by_name.values() if module.kind != CHANGED]
    uses = {module.name: tuple(sorted({use for use in module.uses if use in by_name and use != module.name})) for module in dependents}
    used = {target for targets in uses.values() for target in targets}
    changed = [module.name for module in by_name.values() if module.kind == CHANGED and module.name in used]
    if not dependents or not changed:
        return Diagram("", 0, len(dependents), 0, 0, False)
    changed_units: list[_Unit] = []
    units: list[_Unit] = []
    edges: list[tuple[int, int]] = []
    total_edges = 0
    for level in _LEVELS:
        changed_units, units = _units(changed, dependents, level)
        edges = _edges(_ordered(changed_units, units), uses)
        total_edges = len(edges)
        if len(changed_units) + len(units) <= node_cap and total_edges <= edge_cap:
            break
    else:
        changed_units, units, edges = _cut(changed_units, units, uses, node_cap, edge_cap)
    boxes = _ordered(changed_units, units)
    drawn = sum(len(unit.members) for unit in units)
    drawn_changed = sum(len(unit.members) for unit in changed_units)
    if not edges:
        return Diagram("", 0, len(dependents), len(changed), total_edges, False)
    return Diagram(_text(boxes, edges), drawn, len(dependents) - drawn, len(changed) - drawn_changed,
                   total_edges - len(edges), any(unit.counted for unit in boxes))
