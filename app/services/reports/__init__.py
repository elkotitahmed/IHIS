"""iHIS Reports Service: generate professional PDF documents.

All report types build on reportlab and share a consistent branded header.
Each generator returns raw PDF bytes ready to be sent as a Flask response.
"""
import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, HRFlowable,
)

HOSPITAL_NAME = "iHIS Intelligent Health Information System"

PRIMARY = colors.HexColor("#0d2b4e")
ACCENT = colors.HexColor("#1f6feb")
LIGHT = colors.HexColor("#eef3fa")
DANGER = colors.HexColor("#c62828")


def _base_styles():
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "IhisTitle", parent=styles["Title"], fontName="Helvetica-Bold",
        fontSize=18, leading=22, textColor=PRIMARY,
    )
    subtitle = ParagraphStyle(
        "IhisSubtitle", parent=styles["Normal"], fontSize=10,
        leading=13, textColor=ACCENT, alignment=TA_CENTER,
    )
    h2 = ParagraphStyle(
        "IhisH2", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=13, leading=16, textColor=PRIMARY, spaceAfter=6,
    )
    body = ParagraphStyle(
        "IhisBody", parent=styles["Normal"], fontSize=10, leading=14,
        alignment=TA_LEFT,
    )
    small = ParagraphStyle(
        "IhisSmall", parent=styles["Normal"], fontSize=8.5, leading=11,
        textColor=colors.HexColor("#444444"),
    )
    return title, subtitle, h2, body, small


def _header(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(PRIMARY)
    canvas.rect(0, doc.pagesize[1] - 36, doc.pagesize[0], 36, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 12)
    canvas.drawString(18 * mm, doc.pagesize[1] - 22, HOSPITAL_NAME)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(18 * mm, doc.pagesize[1] - 32,
                     f"Generated on {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    canvas.setFillColor(colors.white)
    canvas.drawRightString(doc.pagesize[0] - 18 * mm, doc.pagesize[1] - 22, "Confidential")
    canvas.setStrokeColor(ACCENT)
    canvas.setLineWidth(1)
    canvas.line(0, doc.pagesize[1] - 38, doc.pagesize[0], doc.pagesize[1] - 38)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.grey)
    canvas.drawRightString(doc.pagesize[0] - 18 * mm, 12 * mm, f"Page {doc.page}")
    canvas.restoreState()


def _wrap(text):
    return text if isinstance(text, str) else (str(text) if text is not None else "")


def _table(headers, rows, col_widths=None):
    data = [[Paragraph(_wrap(h), ParagraphStyle(
        "th", parent=getSampleStyleSheet()["Normal"], fontName="Helvetica-Bold",
        fontSize=9, textColor=colors.white)) for h in headers]]
    for row in rows:
        cells = [Paragraph(_wrap(v), ParagraphStyle(
            "td", parent=getSampleStyleSheet()["Normal"], fontSize=9, leading=12))
            for v in row]
        data.append(cells)
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9d6e3")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def _keyvalue(rows, col_widths=(55 * mm, 120 * mm)):
    _ps = getSampleStyleSheet()["Normal"]
    data = []
    for k, v in rows:
        data.append([
            Paragraph("<b>%s</b>" % k,
                      ParagraphStyle("kvk", parent=_ps, fontSize=9, textColor=PRIMARY)),
            Paragraph(_wrap(v),
                      ParagraphStyle("kvv", parent=_ps, fontSize=9)),
        ])
    t = Table(data, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#dde5f0")),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, LIGHT]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def _build(builder, filename):
    title, subtitle, h2, body, small = _base_styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        rightMargin=18 * mm, leftMargin=18 * mm,
        topMargin=50 * mm, bottomMargin=20 * mm,
    )
    story = []
    story.append(Paragraph(filename, title))
    story.append(Paragraph("Clinical / Administrative Document", subtitle))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1.2, color=PRIMARY))
    story.append(Spacer(1, 10))
    builder(story, h2, body, small)
    doc.build(story, onFirstPage=_header, onLaterPages=_header)
    return buf.getvalue()


def medical_record_pdf(patient_name, doctor_name, record, diagnoses, prescriptions):
    """Single patient medical record summary."""
    def build(story, h2, body, small):
        story.append(Paragraph("Medical Record Report", h2))
        story.append(_keyvalue([
            ("Patient", patient_name),
            ("Attending Physician", doctor_name or "-"),
            ("Visit Date", record.visit_date.strftime("%Y-%m-%d %H:%M") if record.visit_date else "-"),
            ("Diagnosis", record.diagnosis or "-"),
            ("Treatment Plan", record.treatment_plan or "-"),
            ("Clinical Notes", record.clinical_notes or "-"),
        ]))
        if diagnoses:
            story.append(Spacer(1, 10))
            story.append(Paragraph("Diagnoses", h2))
            story.append(_table(
                ["ICD-10", "Description", "Primary", "Date"],
                [[d.icd10_code or "-", d.description or "-",
                  "Yes" if d.is_primary else "No",
                  d.date_diagnosed.strftime("%Y-%m-%d") if d.date_diagnosed else "-"]
                 for d in diagnoses],
                col_widths=[30 * mm] + [40 * mm] * 3,
            ))
        if prescriptions:
            story.append(Spacer(1, 10))
            story.append(Paragraph("Prescriptions", h2))
            rows = []
            for p in prescriptions:
                if p.items:
                    for i in p.items:
                        rows.append([
                            (i.medication.generic_name if i.medication else "-"),
                            i.dosage or "-", i.frequency or "-",
                            i.duration or "-", p.status])
                else:
                    rows.append(["-", "-", "-", "-", p.status])
            story.append(_table(
                ["Medication", "Dosage", "Frequency", "Duration", "Status"],
                rows,
                col_widths=[40 * mm] + [27.5 * mm] * 4,
            ))
    return _build(build, "Medical Record Report")


def lab_result_pdf(order, result):
    """Laboratory result report for a completed lab order."""
    def build(story, h2, body, small):
        story.append(Paragraph("Laboratory Result Report", h2))
        t = order.test
        story.append(_keyvalue([
            ("Patient", order.patient.user.full_name if order.patient else "-"),
            ("Test Name", t.test_name if t else "-"),
            ("Category", t.category if t else "-"),
            ("Requested By", order.doctor.user.full_name if order.doctor and order.doctor.user else "-"),
            ("Order Date", order.order_date.strftime("%Y-%m-%d %H:%M") if order.order_date else "-"),
            ("Result Date", result.result_date.strftime("%Y-%m-%d %H:%M") if result.result_date else "-"),
            ("Status", order.status),
        ]))
        story.append(Spacer(1, 10))
        result_val = result.result_value or "-"
        if result.is_abnormal:
            result_val = f'<b><font color="#c62828">{result_val} &#9888; ABNORMAL</font></b>'
        story.append(_keyvalue([
            ("Result Value", result_val),
            ("Unit", t.unit if t else "-"),
            ("Normal Range", t.normal_range if t else "-"),
            ("Notes", result.result_notes or "-"),
        ]))
    return _build(build, "Laboratory Result Report")


def radiology_report_pdf(order, report):
    """Radiology diagnostic report."""
    def build(story, h2, body, small):
        story.append(Paragraph("Radiology Report", h2))
        it = order.imaging_type
        story.append(_keyvalue([
            ("Patient", order.patient.user.full_name if order.patient else "-"),
            ("Imaging Type", it.name if it else "-"),
            ("Requested By", order.doctor.user.full_name if order.doctor and order.doctor.user else "-"),
            ("Reported By", report.reporter.full_name if report.reporter else "-"),
            ("Order Date", order.order_date.strftime("%Y-%m-%d") if order.order_date else "-"),
            ("Report Date", report.report_date.strftime("%Y-%m-%d %H:%M") if report.report_date else "-"),
        ]))
        story.append(Spacer(1, 8))
        for label, value in [("Findings", report.findings),
                             ("Impression", report.impression),
                             ("Recommendation", report.recommendation)]:
            story.append(Paragraph(label, h2))
            story.append(Paragraph(_wrap(value) or "-", body))
            story.append(Spacer(1, 6))
    return _build(build, "Radiology Report")


def prescription_pdf(prescription):
    """Printable prescription."""
    def build(story, h2, body, small):
        story.append(Paragraph("Prescription", h2))
        story.append(_keyvalue([
            ("Patient", prescription.patient.user.full_name if prescription.patient else "-"),
            ("Prescribed By", prescription.doctor.user.full_name if prescription.doctor and prescription.doctor.user else "-"),
            ("Refills", prescription.refills or 0),
            ("Status", prescription.status),
            ("Date", prescription.prescribed_date.strftime("%Y-%m-%d") if prescription.prescribed_date else "-"),
        ]))
        if prescription.items:
            story.append(Spacer(1, 10))
            story.append(_table(
                ["Medication", "Dosage", "Frequency", "Duration", "Qty"],
                [[(i.medication.generic_name + (f" ({i.medication.brand_name})" if i.medication.brand_name else "")) if i.medication else "-",
                  i.dosage or "-", i.frequency or "-", i.duration or "-", i.quantity or 1]
                 for i in prescription.items],
                col_widths=[45 * mm, 28 * mm, 30 * mm, 25 * mm, 15 * mm],
            ))
        story.append(Spacer(1, 10))
        story.append(Paragraph("Instructions", h2))
        story.append(Paragraph(_wrap(prescription.items[0].instructions) if prescription.items and prescription.items[0].instructions else "-", body))
    return _build(build, "Prescription")


def inventory_pdf(items):
    """Pharmacy stock report."""
    def build(story, h2, body, small):
        low = [i for i in items if i.quantity <= i.reorder_level]
        story.append(Paragraph("Pharmacy Inventory Report", h2))
        story.append(Paragraph(
            f"Total items: <b>{len(items)}</b> &nbsp;&nbsp; Low-stock alerts: "
            f'<font color="#c62828"><b>{len(low)}</b></font>', body))
        story.append(Spacer(1, 8))
        story.append(_table(
            ["Medication", "Qty", "Reorder Level", "Unit Cost", "Selling Price", "Batch", "Expiry"],
            [[(i.medication.generic_name if i.medication else "-"),
              i.quantity, i.reorder_level,
              f"{i.unit_cost:.2f}" if i.unit_cost is not None else "-",
              f"{i.selling_price:.2f}" if i.selling_price is not None else "-",
              i.batch_number or "-",
              i.expiry_date.strftime("%Y-%m-%d") if i.expiry_date else "-"]
             for i in items],
            col_widths=[38 * mm, 16 * mm, 24 * mm, 22 * mm, 24 * mm, 24 * mm, 22 * mm],
        ))
    return _build(build, "Pharmacy Inventory Report")


def statistics_pdf(title, rows):
    """Administrative operational statistics report."""
    def build(story, h2, body, small):
        story.append(Paragraph(title, h2))
        story.append(_table(
            ["Indicator", "Count"],
            [[r[0], r[1]] for r in rows],
            col_widths=[130 * mm, 45 * mm],
        ))
    return _build(build, title)
