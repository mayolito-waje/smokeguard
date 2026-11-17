"""Monthly detection PDF report (fpdf2).

Pure rendering: the route layer owns data access and settings. The table
explains each model score with color coding aimed at non-expert monitoring
staff — yellow = that signal crossed its alert threshold, and the probability
column maps the event class to HIGH (red), MEDIUM (yellow) or LOW (gray).

fpdf2's core fonts are Latin-1/cp1252 only, so all strings below must stay
ASCII or cp1252-safe (``µ`` and ``³`` are fine; avoid em dashes and ``>=``
glyphs).
"""

import logging
from datetime import datetime
from pathlib import Path

from fpdf import FPDF

logger = logging.getLogger("smokeguard")

# Palette (matches the dashboard's light theme).
ACCENT = (5, 150, 105)          # #059669
ACCENT_SOFT = (236, 253, 245)   # #ECFDF5
INK = (31, 41, 55)              # #1F2937
GRAY = (107, 114, 128)          # #6B7280
BORDER = (209, 213, 219)        # #D1D5DB
ROW_ALT = (247, 250, 248)       # subtle alternating row
WHITE = (255, 255, 255)
YELLOW_BG = (254, 243, 199)     # #FEF3C7
AMBER_TEXT = (146, 64, 14)      # #92400E
RED_BG = (254, 226, 226)        # #FEE2E2
RED_TEXT = (153, 27, 27)        # #991B1B
GRAY_BG = (229, 231, 235)       # #E5E7EB
GRAY_TEXT = (75, 85, 99)        # #4B5563

_SOURCE_TEXT = {
    "fusion": "CSI + smoke sensor",
    "smoke_only": "Smoke sensor only",
    "csi_only": "CSI only",
}

# (heading, width in mm, alignment)
_COLUMNS = (
    ("Date", 27, "L"),
    ("Time", 18, "L"),
    ("Facility", 36, "L"),
    ("Detection Source", 44, "L"),
    ("Wi-Fi CSI\nScore", 29, "C"),
    ("Air Quality\nScore", 29, "C"),
    ("Fusion\nScore", 25, "C"),
    ("Avg PM2.5\n(µg/m³)", 24, "C"),
    ("Smoking Activity\nProbability", 35, "C"),
)
_TABLE_WIDTH = sum(width for _, width, _ in _COLUMNS)
_HEADER_H = 11.0
_ROW_H = 7.0


def probability_level(
    label: str,
) -> tuple[str, tuple[int, int, int], tuple[int, int, int]]:
    """Map an event label to its probability text and highlight colors."""
    if label == "fusion":
        return "HIGH", RED_BG, RED_TEXT
    if label == "smoke_only":
        return "MEDIUM", YELLOW_BG, AMBER_TEXT
    return "LOW", GRAY_BG, GRAY_TEXT


def summarize(events: list[dict]) -> dict[str, int]:
    """Counts for the summary boxes: total plus HIGH/MEDIUM/LOW."""
    counts = {"total": len(events), "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for event in events:
        level = probability_level(str(event.get("label")))[0]
        counts[level] += 1
    return counts


def _fmt_pct(value: float | None) -> str:
    """0.87 -> '87%'; missing values render as 'n/a'."""
    return "n/a" if value is None else f"{value * 100:.0f}%"


def _csi_hit(event: dict) -> bool:
    """Stored gate flag, with the exact legacy label fallback."""
    hit = event.get("csi_hit")
    if hit is not None:
        return bool(hit)
    return event.get("label") in ("fusion", "csi_only")


def _sensor_hit(event: dict, threshold: float) -> bool:
    """Stored gate flag, with the exact legacy label / p_sensor fallback.

    The label is authoritative: csi_only means the sensor stayed below the
    gate by definition, even if an old/mock row stored a borderline p_sensor.
    """
    hit = event.get("sensor_hit")
    if hit is not None:
        return bool(hit)
    label = event.get("label")
    if label in ("fusion", "smoke_only"):
        return True
    if label == "csi_only":
        return False
    p_sensor = event.get("p_sensor")
    return p_sensor is not None and p_sensor >= threshold


def _fusion_hit(event: dict, threshold: float) -> bool:
    score = event.get("fused_score")
    return score is not None and score >= threshold


class _MonthlyReport(FPDF):
    """A4 landscape report with a logo header and page-numbered footer."""

    def __init__(
        self,
        *,
        period: str,
        facility: str,
        generated: str,
        logo_path: str | None,
    ) -> None:
        super().__init__(orientation="L", unit="mm", format="A4")
        self.period = period
        self.facility = facility
        self.generated = generated
        self.logo_path = logo_path
        self._logo_warned = False
        self.alias_nb_pages()
        self.set_auto_page_break(auto=True, margin=16)
        self.set_margins(15, 12, 15)

    # ------------------------------------------------------------------
    # Page furniture
    # ------------------------------------------------------------------

    def header(self) -> None:
        if self.page_no() > 1:
            self.set_font("Helvetica", "B", 8.5)
            self.set_text_color(*GRAY)
            self.cell(
                0, 5,
                f"SmokeGuard - Monthly Detection Report - {self.period}",
                new_x="LMARGIN", new_y="NEXT",
            )
            self.set_draw_color(*BORDER)
            self.set_line_width(0.2)
            self.line(self.l_margin, self.get_y(), self.w - self.r_margin,
                      self.get_y())
            self.ln(3)
            return

        text_x = self.l_margin
        if self.logo_path and Path(self.logo_path).is_file():
            try:
                self.image(self.logo_path, x=self.l_margin, y=11, h=18)
                text_x = self.l_margin + 30
            except Exception:
                logger.warning(
                    "Could not embed the report logo %s", self.logo_path
                )
                self._logo_warned = True

        self.set_xy(text_x, 11)
        self.set_font("Helvetica", "B", 20)
        self.set_text_color(*ACCENT)
        self.cell(0, 8, "SmokeGuard", new_x="LMARGIN", new_y="NEXT")
        self.set_x(text_x)
        self.set_font("Helvetica", "B", 12)
        self.set_text_color(*INK)
        self.cell(
            0, 6, "Monthly Smoking Activity Detection Report",
            new_x="LMARGIN", new_y="NEXT",
        )
        self.set_x(text_x)
        self.set_font("Helvetica", "", 9.5)
        self.set_text_color(*GRAY)
        for line in (
            f"Period: {self.period}",
            f"Generated: {self.generated}",
        ):
            self.set_x(text_x)
            self.cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")

        self.set_draw_color(*ACCENT)
        self.set_line_width(0.6)
        self.line(self.l_margin, 38, self.w - self.r_margin, 38)
        self.set_y(42)

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*GRAY)
        self.cell(0, 5, "SmokeGuard", align="L")
        self.set_y(-12)
        self.cell(0, 5, f"Page {self.page_no()} of {{nb}}", align="R")

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------

    def draw_summary(self, summary: dict[str, int]) -> None:
        boxes = (
            ("Total detections", summary["total"], ACCENT_SOFT, ACCENT),
            ("HIGH", summary["HIGH"], RED_BG, RED_TEXT),
            ("MEDIUM", summary["MEDIUM"], YELLOW_BG, AMBER_TEXT),
            ("LOW", summary["LOW"], GRAY_BG, GRAY_TEXT),
        )
        gap = 4.0
        box_w = (self.epw - gap * (len(boxes) - 1)) / len(boxes)
        x = self.l_margin
        y = self.get_y()
        for title, value, fill, text_color in boxes:
            self.set_fill_color(*fill)
            self.set_draw_color(*BORDER)
            self.set_line_width(0.2)
            self.rect(x, y, box_w, 18, style="DF")
            self.set_xy(x + 3, y + 2.5)
            self.set_font("Helvetica", "", 8.5)
            self.set_text_color(*GRAY)
            self.cell(box_w - 6, 4, title)
            self.set_xy(x + 3, y + 7)
            self.set_font("Helvetica", "B", 15)
            self.set_text_color(*text_color)
            self.cell(box_w - 6, 8, str(value))
            x += box_w + gap
        self.set_y(y + 22)

    def draw_empty_state(self) -> None:
        self.set_font("Helvetica", "I", 10)
        self.set_text_color(*GRAY)
        self.cell(0, 10, "No detections recorded for this month.",
                  align="C", new_x="LMARGIN", new_y="NEXT")

    def draw_table_header(self) -> None:
        x = self.l_margin
        y = self.get_y()
        self.set_fill_color(*ACCENT)
        self.rect(x, y, _TABLE_WIDTH, _HEADER_H, style="F")
        for title, width, align in _COLUMNS:
            lines = title.split("\n")
            self.set_xy(x, y + (_HEADER_H - 4 * len(lines)) / 2)
            self.set_font("Helvetica", "B", 8)
            self.set_text_color(*WHITE)
            self.multi_cell(width, 4, title, border=0, align=align)
            x += width
        self.set_y(y + _HEADER_H)

    def draw_events(
        self, events: list[dict], sensor_threshold: float,
        fusion_threshold: float,
    ) -> None:
        for index, event in enumerate(events):
            if self.will_page_break(_ROW_H):
                self.add_page()
                self.draw_table_header()
            self._draw_event_row(
                event, index, sensor_threshold, fusion_threshold
            )

    def _draw_event_row(
        self, event: dict, index: int, sensor_threshold: float,
        fusion_threshold: float,
    ) -> None:
        detected = datetime.fromtimestamp(float(event["detected_at"]))
        label = str(event.get("label"))
        level, level_fill, level_text_color = probability_level(label)
        csi_fill = YELLOW_BG if _csi_hit(event) else None
        sensor_fill = (
            YELLOW_BG if _sensor_hit(event, sensor_threshold) else None
        )
        fusion_fill = (
            YELLOW_BG if _fusion_hit(event, fusion_threshold) else None
        )
        avg_pm2_5 = event.get("avg_pm2_5")
        avg_text = "n/a" if avg_pm2_5 is None else f"{avg_pm2_5:.1f}"

        # (text, fill, text color, bold)
        cells = (
            (detected.strftime("%b %d, %Y"), None, None, False),
            (detected.strftime("%H:%M"), None, None, False),
            (self.facility, None, None, False),
            (_SOURCE_TEXT.get(label, label), None, None, False),
            (_fmt_pct(event.get("p_csi")), csi_fill,
             AMBER_TEXT if csi_fill else None, False),
            (_fmt_pct(event.get("p_sensor")), sensor_fill,
             AMBER_TEXT if sensor_fill else None, False),
            (_fmt_pct(event.get("fused_score")), fusion_fill,
             AMBER_TEXT if fusion_fill else None, False),
            (avg_text, None, None, False),
            (level, level_fill, level_text_color, True),
        )

        y = self.get_y()
        x = self.l_margin
        base_fill = ROW_ALT if index % 2 else WHITE
        for (text, width, align), (value, fill, text_color, bold) in zip(
            _COLUMNS, cells
        ):
            self.set_xy(x, y)
            self.set_fill_color(*(fill or base_fill))
            self.set_draw_color(*BORDER)
            self.set_line_width(0.15)
            self.set_text_color(*(text_color or INK))
            self.set_font("Helvetica", "B" if bold else "", 8.5)
            self.cell(width, _ROW_H, value, border="B", align=align, fill=True)
            x += width
        self.set_y(y + _ROW_H)

    def draw_legend(
        self, sensor_threshold: float, fusion_threshold: float
    ) -> None:
        self.ln(5)
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*INK)
        self.cell(0, 5, "How to read this report",
                  new_x="LMARGIN", new_y="NEXT")

        self.set_font("Helvetica", "", 8)
        self.set_text_color(*GRAY)
        for line in (
            "Each row is one smoking-activity alert recorded during the "
            "month.",
            "Scores show how confident the system was: 0% means not "
            "confident, 100% means very confident. A yellow score means that "
            "signal reached its alert level:",
        ):
            self.multi_cell(0, 4.2, line, new_x="LMARGIN", new_y="NEXT")

        for label, text in (
            ("Wi-Fi CSI", "the Wi-Fi signal analysis recognized a smoking "
                          "gesture."),
            ("Air Quality", f"the air sensor's smoke reading reached "
                            f"{sensor_threshold * 100:.1f}% or higher."),
            ("Fusion", f"the combined Wi-Fi + air-quality score reached "
                       f"{fusion_threshold * 100:.0f}% or higher."),
        ):
            self.set_x(self.l_margin + 4)
            self.set_font("Helvetica", "B", 8)
            self.set_text_color(*INK)
            self.cell(20, 4.2, label)
            self.set_font("Helvetica", "", 8)
            self.set_text_color(*GRAY)
            self.cell(0, 4.2, text, new_x="LMARGIN", new_y="NEXT")

        self.ln(1)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*GRAY)
        self.multi_cell(0, 4.2, "Smoking Activity Probability:",
                        new_x="LMARGIN", new_y="NEXT")

        for level, fill, text_color, text in (
            ("HIGH", RED_BG, RED_TEXT, "both Wi-Fi and air quality agree."),
            ("MEDIUM", YELLOW_BG, AMBER_TEXT, "air quality only."),
            ("LOW", GRAY_BG, GRAY_TEXT, "Wi-Fi only."),
        ):
            x = self.l_margin + 4
            y = self.get_y()
            self.set_fill_color(*fill)
            self.set_draw_color(*BORDER)
            self.set_line_width(0.15)
            self.rect(x, y + 0.8, 3, 3, style="DF")
            self.set_xy(x + 5, y)
            self.set_font("Helvetica", "B", 8)
            self.set_text_color(*text_color)
            self.cell(16, 4.2, level)
            self.set_font("Helvetica", "", 8)
            self.set_text_color(*GRAY)
            self.cell(0, 4.2, text, new_x="LMARGIN", new_y="NEXT")

        self.ln(1)
        self.multi_cell(
            0, 4.2,
            "Avg PM2.5 is the average amount of fine smoke particles in the "
            "air during the alert, measured in µg/m³. n/a means the "
            "measurement was not available for that alert.",
            new_x="LMARGIN", new_y="NEXT",
        )


def build_monthly_report_pdf(
    *,
    year: int,
    month: int,
    events: list[dict],
    sensor_prob_threshold: float,
    fusion_score_threshold: float,
    facility: str,
    logo_path: str | None,
    generated_at: datetime | None = None,
) -> bytes:
    """Render the monthly detection report and return the PDF bytes."""
    generated = generated_at or datetime.now()
    period = datetime(year, month, 1).strftime("%B %Y")
    pdf = _MonthlyReport(
        period=period,
        facility=facility,
        generated=generated.strftime("%b %d, %Y %H:%M"),
        logo_path=logo_path,
    )
    pdf.add_page()
    pdf.draw_summary(summarize(events))
    if events:
        pdf.draw_table_header()
        pdf.draw_events(events, sensor_prob_threshold, fusion_score_threshold)
    else:
        pdf.draw_empty_state()
    pdf.draw_legend(sensor_prob_threshold, fusion_score_threshold)
    return bytes(pdf.output())
