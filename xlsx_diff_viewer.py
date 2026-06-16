import argparse
import pandas as pd
from rich.console import Console
from rich.table import Table

console = Console()


def display_diff(diff):
    table = Table(show_header=True, header_style="bold white")
    table.add_column("Row", style="dim")
    table.add_column("", style="dim")
    for col in diff.columns:
        table.add_column(str(col))

    for orig_idx in diff.index.get_level_values(0).unique():
        old_row = diff.loc[(orig_idx, "Oldest")]
        new_row = diff.loc[(orig_idx, "Newest")]

        old_cells = [str(orig_idx), "Oldest"]
        new_cells = ["", "Newest"]

        for col in diff.columns:
            old_val = str(old_row[col])
            new_val = str(new_row[col])

            if old_val != new_val:
                old_cells.append(f"[red]{old_val}[/red]")
                new_cells.append(f"[green]{new_val}[/green]")
            else:
                old_cells.append(old_val)
                new_cells.append(new_val)

        table.add_row(*old_cells)
        table.add_row(*new_cells)
        table.add_section()

    console.print(table)


def get_difference(df_old, df_new):
    changed_cols = [col for col in df_old.columns if not df_old[col].equals(df_new[col])]
    diff = df_old.compare(df_new, keep_equal=True, align_axis=0, result_names=("Oldest", "Newest"))
    return diff[changed_cols]


def main():
    parser = argparse.ArgumentParser(description="Compare two Excel files and highlight differences.")
    parser.add_argument("oldest", help="Path to the older Excel file")
    parser.add_argument("newest", help="Path to the newer Excel file")
    args = parser.parse_args()

    oldest = pd.read_excel(args.oldest, sheet_name=None)
    newest = pd.read_excel(args.newest, sheet_name=None)

    for sheet in set(oldest) | set(newest):
        console.print(f"\n[bold]--- {sheet} ---[/bold]")

        if sheet not in oldest:
            console.print("  [green]Sheet added[/green]")
            continue
        if sheet not in newest:
            console.print("  [red]Sheet removed[/red]")
            continue

        df_old, df_new = oldest[sheet], newest[sheet]

        if df_old.equals(df_new):
            console.print("  [dim]No differences[/dim]")
            continue

        if df_old.shape != df_new.shape or list(df_old.columns) != list(df_new.columns):
            console.print(f"  [yellow]Shape mismatch: {df_old.shape} vs {df_new.shape} — cannot compare[/yellow]")
            continue

        display_diff(get_difference(df_old, df_new))


if __name__ == "__main__":
    main()
