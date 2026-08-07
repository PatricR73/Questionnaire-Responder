"""Detect question/answer/vocab columns in a vendor questionnaire workbook and read question rows.

Reads the workbook without altering it (write_xlsx.py owns writing back into the same
loaded Workbook object) so merged cells, section header rows, blank spacer rows, and
formatting stay untouched until an answer is actually written.
"""

from dataclasses import dataclass

from openpyxl.worksheet.worksheet import Worksheet

HEADER_SCAN_ROWS = 15

QUESTION_KEYWORDS = ("question", "control", "requirement", "item", "criteria")
ANSWER_KEYWORDS = ("answer", "response")
VOCAB_KEYWORDS = ("yes/no", "compliance", "applicable", "status", "y/n")


@dataclass
class ColumnMap:
    header_row: int
    question_col: int  # 1-based column index
    answer_col: int
    vocab_col: int | None
    vocab_values: list[str] | None


@dataclass
class QuestionRow:
    row_index: int  # 1-based row number in the sheet
    question_text: str


def _cell_text(ws: Worksheet, row: int, col: int) -> str:
    value = ws.cell(row=row, column=col).value
    return str(value).strip().lower() if value is not None else ""


def _find_header_row(ws: Worksheet) -> int:
    for row in range(1, min(HEADER_SCAN_ROWS, ws.max_row) + 1):
        row_texts = [_cell_text(ws, row, col) for col in range(1, ws.max_column + 1)]
        has_question = any(any(kw in text for kw in QUESTION_KEYWORDS) for text in row_texts)
        has_answer = any(any(kw in text for kw in ANSWER_KEYWORDS) for text in row_texts)
        if has_question and has_answer:
            return row
    raise ValueError(f"Could not find a header row with both a question and an answer column in the first {HEADER_SCAN_ROWS} rows")


def _find_column(ws: Worksheet, header_row: int, keywords: tuple[str, ...]) -> int | None:
    for col in range(1, ws.max_column + 1):
        text = _cell_text(ws, header_row, col)
        if any(kw in text for kw in keywords):
            return col
    return None


def _vocab_values_for_column(ws: Worksheet, col: int, header_row: int) -> list[str] | None:
    """Look for an openpyxl data validation (dropdown list) bound to this column."""
    col_letter = ws.cell(row=header_row, column=col).column_letter
    for dv in ws.data_validations.dataValidation:
        if dv.type != "list":
            continue
        if not any(col_letter in str(rng) for rng in dv.sqref.ranges):
            continue
        formula = (dv.formula1 or "").strip('"')
        if formula:
            return [v.strip() for v in formula.split(",")]
    return None


def detect_columns(ws: Worksheet) -> ColumnMap:
    header_row = _find_header_row(ws)
    question_col = _find_column(ws, header_row, QUESTION_KEYWORDS)
    answer_col = _find_column(ws, header_row, ANSWER_KEYWORDS)
    if question_col is None or answer_col is None:
        raise ValueError(f"Header row {header_row} is missing a question or answer column")

    vocab_col = _find_column(ws, header_row, VOCAB_KEYWORDS)
    vocab_values = _vocab_values_for_column(ws, vocab_col, header_row) if vocab_col else None

    return ColumnMap(header_row=header_row, question_col=question_col, answer_col=answer_col, vocab_col=vocab_col, vocab_values=vocab_values)


def _is_section_header_row(ws: Worksheet, row: int, question_col: int) -> bool:
    """A row is a section header (not a real question) if its question cell is merged across multiple columns."""
    cell = ws.cell(row=row, column=question_col)
    for merged_range in ws.merged_cells.ranges:
        if cell.coordinate in merged_range and (merged_range.max_col - merged_range.min_col) > 0:
            return True
    return False


def read_questions(ws: Worksheet, column_map: ColumnMap) -> list[QuestionRow]:
    questions: list[QuestionRow] = []
    for row in range(column_map.header_row + 1, ws.max_row + 1):
        value = ws.cell(row=row, column=column_map.question_col).value
        text = str(value).strip() if value is not None else ""
        if not text:
            continue  # blank spacer row
        if _is_section_header_row(ws, row, column_map.question_col):
            continue
        questions.append(QuestionRow(row_index=row, question_text=text))
    return questions
