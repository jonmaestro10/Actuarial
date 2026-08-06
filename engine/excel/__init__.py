"""The Excel surface: the format an audit file is actually kept in.

RFC-047. Behind the ``[excel]`` extra (openpyxl), so nothing here is
imported unless somebody asks for a workbook.
"""

from engine.excel.workbook import (
    EXCEL_MAX_COLUMNS,
    EXCEL_MAX_ROWS,
    SIGNIFICANT_DIGITS,
    ExcelError,
    Stamp,
    WorkbookWrite,
    as_written,
    assumption_rows,
    read_stamps,
    record_workbook,
    workbook_artifact,
    write_workbook,
)

__all__ = [
    "EXCEL_MAX_COLUMNS",
    "EXCEL_MAX_ROWS",
    "SIGNIFICANT_DIGITS",
    "ExcelError",
    "Stamp",
    "WorkbookWrite",
    "as_written",
    "assumption_rows",
    "read_stamps",
    "record_workbook",
    "workbook_artifact",
    "write_workbook",
]
