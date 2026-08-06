"""The Excel surface: the format an audit file is actually kept in.

RFC-047 writes a workbook after a run; RFC-056 makes a live sheet a client
of the API. Behind the ``[excel]`` extra — openpyxl for the workbook,
xlwings for the add-in — and both imports are guarded at their own module
boundary, so importing this package costs neither.
"""

from engine.excel.addin import (
    AddIn,
    AddInError,
    Block,
    EngineClient,
    XlwingsBook,
    read_block,
    read_request,
    write_block,
)
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
    "AddIn",
    "AddInError",
    "Block",
    "EXCEL_MAX_COLUMNS",
    "EXCEL_MAX_ROWS",
    "SIGNIFICANT_DIGITS",
    "EngineClient",
    "ExcelError",
    "Stamp",
    "XlwingsBook",
    "WorkbookWrite",
    "as_written",
    "assumption_rows",
    "read_block",
    "read_request",
    "read_stamps",
    "record_workbook",
    "workbook_artifact",
    "write_block",
    "write_workbook",
]
