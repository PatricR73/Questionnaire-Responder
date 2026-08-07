"""Write drafted answers back into the original workbook, in place, touching only answer/vocab cells."""

from openpyxl.comments import Comment
from openpyxl.styles import PatternFill
from openpyxl.worksheet.worksheet import Worksheet

from src.questionnaire.parse_xlsx import ColumnMap

NOT_FOUND_MARKER = "NOT FOUND IN PROVIDED DOCUMENTS"
ERROR_MARKER = "NOT PROCESSED — RUN FAILED, SEE AUDIT LOG"
FLAG_FILL = PatternFill(start_color="FFF9C4", end_color="FFF9C4", fill_type="solid")  # pale yellow
ERROR_FILL = PatternFill(start_color="FFCDD2", end_color="FFCDD2", fill_type="solid")  # pale red


def write_answer(
    ws: Worksheet,
    row_index: int,
    column_map: ColumnMap,
    answer_text: str,
    vocab_selection: str | None,
    final_confidence: str,
) -> None:
    """final_confidence is one of 'high', 'low', 'none' (checked, no evidence), or 'error'
    (not checked — a per-row failure, distinct from 'none' so it is never mistaken for a
    verified absence of evidence)."""
    answer_cell = ws.cell(row=row_index, column=column_map.answer_col)

    if final_confidence == "error":
        answer_cell.value = ERROR_MARKER
        answer_cell.fill = ERROR_FILL
        answer_cell.comment = Comment("This row was not processed due to an error — re-run it, do not treat as 'no evidence'.", "Questionnaire Responder")
        return

    if final_confidence == "none":
        answer_cell.value = NOT_FOUND_MARKER
        return

    answer_cell.value = answer_text
    if column_map.vocab_col and vocab_selection:
        ws.cell(row=row_index, column=column_map.vocab_col).value = vocab_selection

    if final_confidence == "low":
        answer_cell.fill = FLAG_FILL
        answer_cell.comment = Comment("Low confidence — needs human review before sending.", "Questionnaire Responder")
