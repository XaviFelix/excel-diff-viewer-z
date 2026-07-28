import argparse
import difflib
import os
import sys

import pandas as pd
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


# ---------------------------------------------------------------- key input

def get_key():
    if os.name == "nt":
        import msvcrt

        ch = msvcrt.getch()
        if ch in (b"\x00", b"\xe0"):
            ch2 = msvcrt.getch()
            return {b"K": "LEFT", b"M": "RIGHT", b"H": "UP", b"P": "DOWN"}.get(ch2, "")
        if ch == b"\x03":
            raise KeyboardInterrupt
        return ch.decode("utf-8", errors="ignore")

    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            ch2 = sys.stdin.read(2)
            return {"[D": "LEFT", "[C": "RIGHT", "[A": "UP", "[B": "DOWN"}.get(ch2, "")
        if ch == "\x03":
            raise KeyboardInterrupt
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


# ------------------------------------------------------------- normalization

def norm(value):
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def row_signature(row, cols):
    return tuple(norm(row[c]) for c in cols)


def row_similarity(a, b):
    if not a:
        return 0.0
    return sum(1 for x, y in zip(a, b) if x == y) / len(a)


def cell_value(df, idx, col):
    if df is None or idx is None or col not in df.columns:
        return None
    return df.iloc[idx][col]


def format_cell(value):
    if value is None or pd.isna(value):
        return ""
    return str(value)


# ---------------------------------------------------------------- diff core

class SheetDiff:
    def __init__(self, status, col_order=None, added_cols=None, removed_cols=None,
                 entries=None, counts=None):
        self.status = status  # only_old | only_new | unchanged | diff
        self.col_order = col_order or []
        self.added_cols = added_cols or []
        self.removed_cols = removed_cols or []
        self.entries = entries or []
        self.counts = counts or {"changed": 0, "added": 0, "removed": 0, "unchanged": 0}


def match_replace_block(old_range, new_range, old_sig, new_sig):
    """Within a block difflib couldn't align wholesale, greedily pair rows
    by similarity so modified rows read as 'changed' rather than a
    coincidental delete+insert."""
    scored = []
    for i in old_range:
        for j in new_range:
            s = row_similarity(old_sig[i], new_sig[j])
            if s > 0:
                scored.append((s, i, j))
    scored.sort(key=lambda t: -t[0])

    matched_old, matched_new, changed_pairs = set(), set(), []
    for s, i, j in scored:
        if i in matched_old or j in matched_new:
            continue
        matched_old.add(i)
        matched_new.add(j)
        changed_pairs.append((i, j))

    removed = [i for i in old_range if i not in matched_old]
    added = [j for j in new_range if j not in matched_new]
    return changed_pairs, removed, added


def diff_sheet(df_old, df_new):
    old_cols = list(df_old.columns)
    new_cols = list(df_new.columns)
    common_cols = [c for c in old_cols if c in new_cols]
    added_cols = [c for c in new_cols if c not in old_cols]
    removed_cols = [c for c in old_cols if c not in new_cols]
    col_order = old_cols + added_cols

    if df_old.equals(df_new):
        return SheetDiff("unchanged", col_order, added_cols, removed_cols)

    if not common_cols:
        entries = [("removed", i) for i in range(len(df_old))]
        entries += [("added", j) for j in range(len(df_new))]
        counts = {"changed": 0, "added": len(df_new), "removed": len(df_old), "unchanged": 0}
        return SheetDiff("diff", col_order, added_cols, removed_cols, entries, counts)

    old_sig = [row_signature(row, common_cols) for _, row in df_old.iterrows()]
    new_sig = [row_signature(row, common_cols) for _, row in df_new.iterrows()]

    matcher = difflib.SequenceMatcher(None, old_sig, new_sig, autojunk=False)
    entries = []
    counts = {"changed": 0, "added": 0, "removed": 0, "unchanged": 0}

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                entries.append(("unchanged", i1 + k, j1 + k))
            counts["unchanged"] += i2 - i1
        elif tag == "delete":
            entries += [("removed", i) for i in range(i1, i2)]
            counts["removed"] += i2 - i1
        elif tag == "insert":
            entries += [("added", j) for j in range(j1, j2)]
            counts["added"] += j2 - j1
        elif tag == "replace":
            changed_pairs, removed, added = match_replace_block(
                range(i1, i2), range(j1, j2), old_sig, new_sig
            )
            changed_pairs.sort(key=lambda p: p[0])
            entries += [("changed", i, j) for i, j in changed_pairs]
            entries += [("removed", i) for i in sorted(removed)]
            entries += [("added", j) for j in sorted(added)]
            counts["changed"] += len(changed_pairs)
            counts["removed"] += len(removed)
            counts["added"] += len(added)

    return SheetDiff("diff", col_order, added_cols, removed_cols, entries, counts)


def merged_sheet_order(old_names, new_names):
    """Interleave sheet names the same way rows are aligned, so a
    newly-inserted sheet lands where it actually sits in the workbook
    instead of always trailing at the end."""
    matcher = difflib.SequenceMatcher(None, old_names, new_names, autojunk=False)
    order = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("equal", "delete"):
            order.extend(old_names[i1:i2])
        elif tag == "insert":
            order.extend(new_names[j1:j2])
        elif tag == "replace":
            order.extend(old_names[i1:i2])
            order.extend(new_names[j1:j2])
    return order


# ------------------------------------------------------------------ render

def render_tabs(sheet_names, diffs, current):
    parts = []
    for i, name in enumerate(sheet_names):
        sd = diffs[name]
        if sd.status == "only_old":
            badge = "[red]only old[/red]"
        elif sd.status == "only_new":
            badge = "[green]only new[/green]"
        elif sd.status == "unchanged":
            badge = "[dim]=[/dim]"
        else:
            c = sd.counts
            badge = f"[green]+{c['added']}[/green]/[red]-{c['removed']}[/red]/[yellow]~{c['changed']}[/yellow]"
        label = f"[reverse bold] {name} [/reverse bold]" if i == current else f" {name} "
        parts.append(f"{label}({badge})")
    console.print("  ".join(parts))


def render_sheet(sd, df_old, df_new):
    if sd.status == "only_old":
        console.print(Panel("[red]Sheet only exists in Oldest[/red]", border_style="red"))
        return
    if sd.status == "only_new":
        console.print(Panel("[green]Sheet only exists in Newest[/green]", border_style="green"))
        return

    if sd.added_cols or sd.removed_cols:
        notes = []
        if sd.added_cols:
            notes.append(f"[green]+ columns:[/green] {', '.join(str(c) for c in sd.added_cols)}")
        if sd.removed_cols:
            notes.append(f"[red]- columns:[/red] {', '.join(str(c) for c in sd.removed_cols)}")
        console.print(Panel("\n".join(notes), title="Column changes", border_style="yellow"))

    if sd.status == "unchanged":
        console.print("[dim]No differences[/dim]")
        return

    table = Table(show_header=True, header_style="bold white")
    table.add_column("Row", style="dim", no_wrap=True)
    table.add_column("", style="dim", no_wrap=True)
    for col in sd.col_order:
        table.add_column(str(col))

    blank_row = [""] * len(sd.col_order)
    skipped = 0

    def flush_skip():
        nonlocal skipped
        if skipped:
            table.add_row(f"[dim]… {skipped} unchanged row(s) …[/dim]", "", *blank_row)
            skipped = 0

    for entry in sd.entries:
        kind = entry[0]
        if kind == "unchanged":
            skipped += 1
            continue

        flush_skip()

        if kind == "changed":
            _, oi, ni = entry
            old_cells, new_cells = [str(oi + 2), "Old"], ["", "New"]
            for col in sd.col_order:
                ov = format_cell(cell_value(df_old, oi, col))
                nv = format_cell(cell_value(df_new, ni, col))
                if ov != nv:
                    old_cells.append(f"[red]{ov}[/red]" if ov else "")
                    new_cells.append(f"[green]{nv}[/green]" if nv else "")
                else:
                    old_cells.append(ov)
                    new_cells.append(nv)
            table.add_row(*old_cells)
            table.add_row(*new_cells)
            table.add_section()
        elif kind == "added":
            _, ni = entry
            cells = [str(ni + 2), "New"]
            for col in sd.col_order:
                nv = format_cell(cell_value(df_new, ni, col))
                cells.append(f"[green]{nv}[/green]" if nv else "")
            table.add_row(*cells)
            table.add_section()
        elif kind == "removed":
            _, oi = entry
            cells = [str(oi + 2), "Old"]
            for col in sd.col_order:
                ov = format_cell(cell_value(df_old, oi, col))
                cells.append(f"[red]{ov}[/red]" if ov else "")
            table.add_row(*cells)
            table.add_section()

    flush_skip()
    console.print(table)
    c = sd.counts
    console.print(
        f"[green]+{c['added']} added[/green]   "
        f"[red]-{c['removed']} removed[/red]   "
        f"[yellow]~{c['changed']} changed[/yellow]   "
        f"[dim]={c['unchanged']} unchanged[/dim]"
    )


# --------------------------------------------------------------------- main

def build_diffs(oldest, newest):
    sheet_names = merged_sheet_order(list(oldest.keys()), list(newest.keys()))
    diffs, dfs = {}, {}
    for name in sheet_names:
        if name not in oldest:
            diffs[name] = SheetDiff("only_new")
            dfs[name] = (None, newest[name])
        elif name not in newest:
            diffs[name] = SheetDiff("only_old")
            dfs[name] = (oldest[name], None)
        else:
            diffs[name] = diff_sheet(oldest[name], newest[name])
            dfs[name] = (oldest[name], newest[name])
    return sheet_names, diffs, dfs


def run_viewer(sheet_names, diffs, dfs):
    idx = 0
    dirty = True
    while True:
        if dirty:
            console.clear()
            render_tabs(sheet_names, diffs, idx)
            console.rule()
            df_old, df_new = dfs[sheet_names[idx]]
            render_sheet(diffs[sheet_names[idx]], df_old, df_new)
            console.print("\n[dim]h/l or ←/→: switch sheet   q: quit[/dim]")
            dirty = False

        try:
            key = get_key()
        except KeyboardInterrupt:
            break

        if key in ("h", "H", "LEFT"):
            idx = (idx - 1) % len(sheet_names)
            dirty = True
        elif key in ("l", "L", "RIGHT"):
            idx = (idx + 1) % len(sheet_names)
            dirty = True
        elif key in ("q", "Q", "\x1b"):
            break


def main():
    parser = argparse.ArgumentParser(description="Interactively compare two Excel workbooks.")
    parser.add_argument("oldest", help="Path to the older Excel file")
    parser.add_argument("newest", help="Path to the newer Excel file")
    args = parser.parse_args()

    oldest = pd.read_excel(args.oldest, sheet_name=None)
    newest = pd.read_excel(args.newest, sheet_name=None)

    sheet_names, diffs, dfs = build_diffs(oldest, newest)
    run_viewer(sheet_names, diffs, dfs)


if __name__ == "__main__":
    main()
