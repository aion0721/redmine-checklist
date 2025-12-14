from dataclasses import dataclass


@dataclass(frozen=True)
class ColumnDef:
    labels: tuple[str, ...]
    widths: tuple[int, ...]
    # index map for readability
    IDX_ID: int = 0
    IDX_OPEN: int = 1
    IDX_DONE: int = 2
    IDX_TOGGLE: int = 3
    IDX_SEARCH_HIT: int = 4
    IDX_UPDATED: int = 5
    IDX_DONE_AT: int = 6
    IDX_SUBJECT: int = 7


COLUMNS = ColumnDef(
    labels=("ID", "開く", "済", "済ボタン", "検索文字列有無", "更新日", "済日時", "件名"),
    widths=(80, 80, 50, 90, 120, 180, 160, 340),
)
