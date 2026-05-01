"""Generate a styled PDF report for the QSE/SWEBOK study."""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    ListFlowable,
    ListItem,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

CLASSIFICATIONS_DIR = Path("out/classifications")
EXTRACTED_DIR = Path("out/extracted")
ANALYSIS_DIR = Path("out/analysis")
SUBJECTS_PATH = Path("swebok_subjects.json")
OUTPUT_PATH = Path("out/report/qse_swebok_report.pdf")

PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN_X = 2.0 * cm
MARGIN_Y = 1.8 * cm

COLOR_NAVY = colors.HexColor("#123B5D")
COLOR_BLUE = colors.HexColor("#1F5F8B")
COLOR_TEAL = colors.HexColor("#3BA4A0")
COLOR_GOLD = colors.HexColor("#D9A441")
COLOR_SOFT = colors.HexColor("#EEF4F8")
COLOR_BORDER = colors.HexColor("#C8D7E1")
COLOR_TEXT = colors.HexColor("#23323F")
COLOR_MUTED = colors.HexColor("#5B6E7E")

THEME_PATTERNS = {
    "ferramentas e frameworks": [
        "tool",
        "framework",
        "platform",
        "sdk",
        "library",
        "prototype",
        "environment",
    ],
    "métodos, modelos e formalizações": [
        "model",
        "method",
        "formal",
        "metamodel",
        "language",
        "dsl",
        "workflow",
        "ontology",
    ],
    "estudos empíricos, benchmarks e surveys": [
        "empirical",
        "survey",
        "mapping",
        "benchmark",
        "case study",
        "dataset",
        "landscape",
        "review",
    ],
    "testes, verificação e depuração": [
        "testing",
        "test",
        "verification",
        "debug",
        "oracle",
        "fault",
        "bug",
        "assertion",
    ],
    "qualidade, smells e manutenção": [
        "quality",
        "smell",
        "maintenance",
        "evolution",
        "refactor",
        "reliability",
    ],
    "arquiteturas, integração e sistemas híbridos": [
        "architecture",
        "hybrid",
        "integration",
        "service",
        "orchestration",
        "pipeline",
        "system",
    ],
    "segurança, proteção e resiliência": [
        "security",
        "secure",
        "attack",
        "resilience",
        "privacy",
        "safety",
    ],
    "compilação, transpilation e otimização": [
        "compiler",
        "compilation",
        "transpilation",
        "transpile",
        "optimization",
        "routing",
        "schedule",
    ],
    "prática profissional, adoção e educação": [
        "education",
        "teaching",
        "practice",
        "developer",
        "adoption",
        "skill",
        "industry",
    ],
    "custos, gestão e planejamento": [
        "economics",
        "cost",
        "management",
        "planning",
        "roadmap",
        "project",
    ],
    "requisitos e elicitação": [
        "requirement",
        "requirements",
        "elicitation",
        "specification",
    ],
}


@dataclass(slots=True)
class PaperRecord:
    stem: str
    filename: str
    title: str
    year: int | None
    authors: list[str]
    subjects: list[str]
    primary_subject: str
    summary: str
    confidence: str
    text_for_classification: str
    full_text: str


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_subjects(path: Path) -> list[str]:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalise_whitespace(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _format_percentage(value: float) -> str:
    return f"{value:.1f}%".replace(".", ",")


def _format_list_pt(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} e {items[1]}"
    return f"{', '.join(items[:-1])} e {items[-1]}"


def _truncate(text: str, max_chars: int = 240) -> str:
    compact = _normalise_whitespace(text)
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 1].rstrip() + "…"


def _authors_label(authors: list[str], max_authors: int = 3) -> str:
    if not authors:
        return "Autoria não informada"
    if len(authors) <= max_authors:
        return ", ".join(authors)
    return ", ".join(authors[:max_authors]) + " et al."


def load_corpus(classifications_dir: Path, extracted_dir: Path) -> list[PaperRecord]:
    records: list[PaperRecord] = []
    missing_extracted: list[str] = []

    for classification_path in sorted(classifications_dir.glob("*.json")):
        classification_data = _load_json(classification_path)
        extracted_path = extracted_dir / classification_path.name
        extracted_data = _load_json(extracted_path) if extracted_path.exists() else {}
        if not extracted_path.exists():
            missing_extracted.append(classification_path.name)

        bibliographic = extracted_data.get("bibliographic") or {}
        authors = bibliographic.get("authors") or []
        if isinstance(authors, str):
            authors = [authors]

        title = (
            bibliographic.get("title")
            or extracted_data.get("stem")
            or classification_data.get("stem")
            or classification_path.stem
        )
        clf = classification_data["classification"]
        records.append(
            PaperRecord(
                stem=classification_data["stem"],
                filename=classification_data["filename"],
                title=_normalise_whitespace(title),
                year=bibliographic.get("year"),
                authors=list(authors),
                subjects=list(clf["subjects"]),
                primary_subject=clf["primary_subject"],
                summary=_normalise_whitespace(clf["summary"]),
                confidence=clf["confidence"],
                text_for_classification=_normalise_whitespace(
                    extracted_data.get("text_for_classification", "")
                ),
                full_text=_normalise_whitespace(extracted_data.get("full_text", "")),
            )
        )

    if missing_extracted:
        logger.warning(
            "Missing extracted counterparts for %d classification file(s).",
            len(missing_extracted),
        )
    return records


def build_subject_index(records: list[PaperRecord], subjects: list[str]) -> dict[str, list[PaperRecord]]:
    index: dict[str, list[PaperRecord]] = {subject: [] for subject in subjects}
    for record in records:
        for subject in record.subjects:
            index.setdefault(subject, []).append(record)
    for current_subject, subject_records in index.items():
        subject_records.sort(
            key=lambda record, subject=current_subject: (
                0 if record.primary_subject == subject else 1,
                -(record.year or 0),
                record.title.lower(),
            )
        )
    return index


def build_cooccurrence(records: list[PaperRecord], subjects: list[str]) -> dict[str, Counter]:
    cooc: dict[str, Counter] = {subject: Counter() for subject in subjects}
    for record in records:
        for i, left in enumerate(record.subjects):
            for right in record.subjects[i + 1 :]:
                cooc[left][right] += 1
                cooc[right][left] += 1
    return cooc


def extract_theme_counter(records: list[PaperRecord]) -> Counter:
    counter: Counter = Counter()
    for record in records:
        text = f"{record.title} {record.summary}".lower()
        for label, keywords in THEME_PATTERNS.items():
            if any(keyword in text for keyword in keywords):
                counter[label] += 1
    return counter


def representative_papers(records: list[PaperRecord], subject: str, limit: int = 3) -> list[PaperRecord]:
    ranked = sorted(
        records,
        key=lambda record: (
            0 if record.primary_subject == subject else 1,
            -(record.year or 0),
            record.title.lower(),
        ),
    )
    return ranked[:limit]


def subject_discussion(
    subject: str,
    records: list[PaperRecord],
    total_papers: int,
    cooccurrence: dict[str, Counter],
) -> list[str]:
    count = len(records)
    primary_count = sum(1 for record in records if record.primary_subject == subject)
    percentage = _format_percentage((count / total_papers * 100.0) if total_papers else 0.0)
    theme_counter = extract_theme_counter(records)
    top_themes = [label for label, value in theme_counter.most_common(3) if value > 0]
    overlap_items = [
        related_subject
        for related_subject, value in cooccurrence.get(subject, Counter()).most_common(3)
        if value > 0
    ]
    reps = representative_papers(records, subject, limit=3)

    intro = (
        f"A área <b>{subject}</b> aparece em <b>{count}</b> artigos, o que corresponde a "
        f"<b>{percentage}</b> do corpus classificado, e é tema principal em "
        f"<b>{primary_count}</b> desses trabalhos."
    )

    if top_themes:
        theme_text = (
            "Pelos resumos e títulos dos artigos, esta frente de pesquisa se concentra "
            f"sobretudo em <b>{_format_list_pt(top_themes)}</b>."
        )
    else:
        theme_text = (
            "Os trabalhos desta área são heterogêneos e não deixam um único padrão de "
            "contribuição tão dominante quanto em outras frentes do corpus."
        )

    if overlap_items:
        overlap_text = (
            "Os cruzamentos mais frequentes desta área com outras classes do SWEBOK "
            f"ocorrem com <b>{_format_list_pt(overlap_items)}</b>, o que sugere uma agenda "
            "de pesquisa fortemente interdisciplinar dentro da engenharia de software quântica."
        )
    else:
        overlap_text = (
            "Os artigos desta área aparecem com baixo acoplamento às demais classes, o que "
            "indica uma linha de pesquisa mais concentrada e específica."
        )

    if reps:
        rep_titles = [f"“{record.title}”" for record in reps]
        rep_text = (
            "Entre os trabalhos mais representativos para caracterizar esta área estão "
            f"{_format_list_pt(rep_titles)}."
        )
    else:
        rep_text = "Não há trabalhos suficientes para destacar exemplares representativos."

    if count <= 3:
        maturity_text = (
            "Como a quantidade de artigos é pequena, esta área ainda parece emergente no corpus "
            "e demanda mais investigações para consolidar subtemas recorrentes."
        )
    elif count >= math.ceil(total_papers * 0.15):
        maturity_text = (
            "O volume de artigos indica uma frente relativamente madura dentro do corpus, "
            "com diversidade suficiente para combinar propostas de solução, estudos empíricos e "
            "instrumentos de apoio."
        )
    else:
        maturity_text = (
            "O volume de artigos sugere uma frente em consolidação: já existe massa crítica "
            "para identificar tendências, mas ainda com espaço para ampliar métodos, ferramentas "
            "e evidências empíricas."
        )

    return [intro, theme_text, overlap_text, rep_text, maturity_text]


def corpus_highlights(records: list[PaperRecord], subjects: list[str]) -> tuple[list[dict], float]:
    counter: Counter = Counter()
    for record in records:
        for subject in record.subjects:
            counter[subject] += 1
    frequencies = [{"subject": subject, "count": counter.get(subject, 0)} for subject in subjects]
    frequencies.sort(key=lambda item: (-item["count"], item["subject"]))
    avg_subjects = sum(len(record.subjects) for record in records) / max(1, len(records))
    return frequencies, avg_subjects


def _image_flowable(path: Path, width: float, max_height: float) -> Image:
    image = Image(str(path))
    factor = min(width / image.imageWidth, max_height / image.imageHeight)
    image.drawWidth = image.imageWidth * factor
    image.drawHeight = image.imageHeight * factor
    image.hAlign = "CENTER"
    return image


def build_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "ReportTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=24,
            leading=28,
            alignment=TA_CENTER,
            textColor=colors.white,
            spaceAfter=6,
        ),
        "subtitle": ParagraphStyle(
            "ReportSubtitle",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=11,
            leading=14,
            alignment=TA_CENTER,
            textColor=colors.white,
            spaceAfter=6,
        ),
        "section": ParagraphStyle(
            "SectionTitle",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=22,
            textColor=COLOR_NAVY,
            spaceBefore=10,
            spaceAfter=10,
        ),
        "subject": ParagraphStyle(
            "SubjectTitle",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=15,
            leading=18,
            textColor=COLOR_BLUE,
            spaceBefore=8,
            spaceAfter=8,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=10.5,
            leading=15,
            alignment=TA_JUSTIFY,
            textColor=COLOR_TEXT,
            spaceAfter=8,
        ),
        "lead": ParagraphStyle(
            "Lead",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=12,
            leading=17,
            alignment=TA_JUSTIFY,
            textColor=COLOR_TEXT,
            spaceAfter=10,
        ),
        "caption": ParagraphStyle(
            "Caption",
            parent=base["BodyText"],
            fontName="Helvetica-Oblique",
            fontSize=8.5,
            leading=11,
            alignment=TA_CENTER,
            textColor=COLOR_MUTED,
            spaceAfter=10,
        ),
        "metric": ParagraphStyle(
            "Metric",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=16,
            alignment=TA_CENTER,
            textColor=COLOR_NAVY,
        ),
        "metric_label": ParagraphStyle(
            "MetricLabel",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=10,
            alignment=TA_CENTER,
            textColor=COLOR_MUTED,
        ),
        "small": ParagraphStyle(
            "Small",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            textColor=COLOR_MUTED,
        ),
        "paper_title": ParagraphStyle(
            "PaperTitle",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=13,
            textColor=COLOR_NAVY,
            spaceAfter=2,
        ),
        "paper_meta": ParagraphStyle(
            "PaperMeta",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=11,
            textColor=COLOR_MUTED,
            spaceAfter=3,
        ),
        "paper_body": ParagraphStyle(
            "PaperBody",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=13,
            alignment=TA_LEFT,
            textColor=COLOR_TEXT,
            spaceAfter=8,
        ),
        "toc_heading": ParagraphStyle(
            "TOCHeading",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=22,
            textColor=COLOR_NAVY,
            alignment=TA_LEFT,
            spaceAfter=12,
        ),
    }
    return styles


class ReportDocTemplate(BaseDocTemplate):
    def __init__(self, filename: str, styles: dict[str, ParagraphStyle], **kwargs):
        self.styles = styles
        frame = Frame(MARGIN_X, MARGIN_Y, PAGE_WIDTH - 2 * MARGIN_X, PAGE_HEIGHT - 2 * MARGIN_Y, id="normal")
        super().__init__(filename, pagesize=A4, leftMargin=MARGIN_X, rightMargin=MARGIN_X, topMargin=MARGIN_Y, bottomMargin=MARGIN_Y, **kwargs)
        self.addPageTemplates(
            [
                PageTemplate(id="cover", frames=[frame], onPage=self._draw_cover_background),
                PageTemplate(id="body", frames=[frame], onPage=self._draw_body_chrome),
            ]
        )

    def _draw_cover_background(self, canvas, doc):  # noqa: ANN001
        canvas.saveState()
        canvas.setFillColor(COLOR_NAVY)
        canvas.rect(0, PAGE_HEIGHT - 8.2 * cm, PAGE_WIDTH, 8.2 * cm, fill=1, stroke=0)
        canvas.setFillColor(COLOR_TEAL)
        canvas.rect(0, PAGE_HEIGHT - 8.7 * cm, PAGE_WIDTH, 0.5 * cm, fill=1, stroke=0)
        canvas.setFillColor(COLOR_GOLD)
        canvas.rect(0, 0, PAGE_WIDTH, 0.6 * cm, fill=1, stroke=0)
        canvas.restoreState()

    def _draw_body_chrome(self, canvas, doc):  # noqa: ANN001
        canvas.saveState()
        canvas.setStrokeColor(COLOR_BORDER)
        canvas.setLineWidth(0.4)
        canvas.line(MARGIN_X, PAGE_HEIGHT - 1.2 * cm, PAGE_WIDTH - MARGIN_X, PAGE_HEIGHT - 1.2 * cm)
        canvas.setFillColor(COLOR_MUTED)
        canvas.setFont("Helvetica", 8.5)
        canvas.drawString(MARGIN_X, PAGE_HEIGHT - 0.9 * cm, "Relatório QSE x SWEBOK")
        canvas.drawRightString(PAGE_WIDTH - MARGIN_X, PAGE_HEIGHT - 0.9 * cm, f"Página {canvas.getPageNumber()}")
        canvas.setFillColor(COLOR_MUTED)
        canvas.drawString(MARGIN_X, 0.9 * cm, "Gerado automaticamente a partir do corpus classificado do projeto qse-review")
        canvas.restoreState()

    def afterFlowable(self, flowable):  # noqa: ANN001
        if not isinstance(flowable, Paragraph):
            return
        plain = flowable.getPlainText()
        if flowable.style.name == "SectionTitle":
            key = f"section-{self.seq.nextf('section')}"
            self.canv.bookmarkPage(key)
            self.notify("TOCEntry", (0, plain, self.page, key))
        elif flowable.style.name == "SubjectTitle":
            key = f"subject-{self.seq.nextf('subject')}"
            self.canv.bookmarkPage(key)
            self.notify("TOCEntry", (1, plain, self.page, key))


def _metric_table(styles: dict[str, ParagraphStyle], data: list[tuple[str, str]]) -> Table:
    value_row = [Paragraph(value, styles["metric"]) for value, _ in data]
    label_row = [Paragraph(label, styles["metric_label"]) for _, label in data]
    table = Table(
        [value_row, label_row],
        colWidths=[4.0 * cm] * len(data),
        rowHeights=[0.9 * cm, 0.8 * cm],
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), COLOR_SOFT),
                ("BOX", (0, 0), (-1, -1), 0.6, COLOR_BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.white),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


def _summary_box(styles: dict[str, ParagraphStyle], title: str, lines: list[str]) -> Table:
    rows = [[Paragraph(f"<b>{title}</b>", styles["body"])]]
    rows.extend([[Paragraph(line, styles["body"])] for line in lines])
    box = Table(rows, colWidths=[PAGE_WIDTH - 2 * MARGIN_X])
    box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), COLOR_SOFT),
                ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                ("BOX", (0, 0), (-1, -1), 0.7, COLOR_BORDER),
                ("LINEBELOW", (0, 0), (-1, 0), 0.7, COLOR_BORDER),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return box


def build_report(
    output_path: Path,
    records: list[PaperRecord],
    subjects: list[str],
    analysis_dir: Path,
) -> None:
    styles = build_styles()
    subject_index = build_subject_index(records, subjects)
    cooccurrence = build_cooccurrence(records, subjects)
    frequencies, avg_subjects = corpus_highlights(records, subjects)
    total_assignments = sum(len(record.subjects) for record in records)
    top_subject = frequencies[0]
    bottom_subject = frequencies[-1]
    top_primary = Counter(record.primary_subject for record in records).most_common(3)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = ReportDocTemplate(str(output_path), styles=styles, title="Relatório QSE x SWEBOK")
    story = []

    generated_at = datetime.now(timezone.utc).astimezone().strftime("%d/%m/%Y %H:%M")
    story.extend(
        [
            Spacer(1, 3.6 * cm),
            Paragraph("Mapeamento da Literatura de Quantum Software Engineering segundo o SWEBOK", styles["title"]),
            Paragraph(
                "Relatório analítico em PDF gerado a partir do corpus classificado do projeto qse-review",
                styles["subtitle"],
            ),
            Spacer(1, 0.6 * cm),
            Table(
                [
                    [Paragraph(f"<b>Corpus analisado</b><br/>{len(records)} artigos", styles["subtitle"])],
                    [Paragraph(f"<b>Gerado em</b><br/>{generated_at}", styles["subtitle"])],
                ],
                colWidths=[9.5 * cm],
            ),
            Spacer(1, 7.5 * cm),
            Paragraph(
                "Este relatório consolida o contexto, o processo executado, os resultados quantitativos e uma discussão temática por área do SWEBOK, sempre baseada nos artigos estudados.",
                styles["subtitle"],
            ),
            NextPageTemplate("body"),
            PageBreak(),
        ]
    )

    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle(name="TOCLevel1", fontName="Helvetica", fontSize=10.5, leftIndent=12, firstLineIndent=-4, leading=14, textColor=COLOR_TEXT),
        ParagraphStyle(name="TOCLevel2", fontName="Helvetica", fontSize=9.0, leftIndent=28, firstLineIndent=-4, leading=12, textColor=COLOR_MUTED),
    ]
    story.extend(
        [
            Paragraph("Sumário", styles["toc_heading"]),
            toc,
            PageBreak(),
        ]
    )

    story.append(Paragraph("1. Contexto do estudo", styles["section"]))
    story.append(
        Paragraph(
            "O estudo organiza a literatura de <i>Quantum Software Engineering</i> (QSE) segundo as áreas de conhecimento do SWEBOK, permitindo observar quais temas da engenharia de software têm recebido maior atenção quando aplicados ao desenvolvimento, teste, operação e evolução de software quântico.",
            styles["lead"],
        )
    )
    story.append(
        Paragraph(
            "Na base atual, o corpus reúne artigos identificados a partir de planilhas bibliográficas, enriquecidos com metadados e texto de apoio, classificados manualmente/agenticamente por área SWEBOK e revisados para manter coerência entre o conteúdo dos papers e os rótulos atribuídos.",
            styles["body"],
        )
    )
    story.append(Spacer(1, 0.2 * cm))
    story.append(
        _metric_table(
            styles,
            [
                (str(len(records)), "artigos classificados"),
                (str(total_assignments), "atribuições de área"),
                (f"{avg_subjects:.2f}".replace(".", ","), "áreas por artigo (média)"),
                (str(len(subjects)), "áreas SWEBOK cobertas"),
            ],
        )
    )
    story.append(Spacer(1, 0.4 * cm))

    story.append(Paragraph("2. O que foi feito neste estudo", styles["section"]))
    pipeline_items = [
        "resolução e consolidação de DOIs a partir das planilhas em `papers/`;",
        "coleta de metadados bibliográficos por API, com enriquecimento local via PDFs quando disponível;",
        "classificação de cada artigo nas áreas do SWEBOK, incluindo assunto primário, assuntos secundários, resumo e confiança;",
        "revisão do corpus classificado para corrigir sobreclassificações e alinhar melhor os rótulos ao conteúdo dos artigos;",
        "geração de figuras analíticas de frequência e coocorrência entre áreas.",
    ]
    story.append(
        ListFlowable(
            [ListItem(Paragraph(item, styles["body"])) for item in pipeline_items],
            bulletType="bullet",
            start="circle",
            leftIndent=18,
        )
    )
    story.append(
        Paragraph(
            "O resultado é uma visão integrada que combina síntese narrativa, distribuição quantitativa por tema e um catálogo de artigos por área do SWEBOK.",
            styles["body"],
        )
    )

    story.append(Paragraph("3. Panorama quantitativo do corpus", styles["section"]))
    top_primary_text = ", ".join(f"{subject} ({count})" for subject, count in top_primary)
    story.append(
        _summary_box(
            styles,
            "Leituras principais do corpus",
            [
                f"A área mais frequente no corpus é <b>{top_subject['subject']}</b>, com <b>{top_subject['count']}</b> artigos.",
                f"A área menos frequente é <b>{bottom_subject['subject']}</b>, com <b>{bottom_subject['count']}</b> artigo(s).",
                f"Os assuntos primários mais recorrentes são: <b>{top_primary_text}</b>.",
            ],
        )
    )
    story.append(Spacer(1, 0.3 * cm))
    story.append(
        Paragraph(
            "Esse panorama já sugere que a produção de QSE se concentra mais fortemente em testes, qualidade e modelos/métodos, enquanto gestão, economia, requisitos e configuração aparecem como frentes menores, mas presentes.",
            styles["body"],
        )
    )

    histogram_path = analysis_dir / "histogram.png"
    if histogram_path.exists():
        story.append(_image_flowable(histogram_path, PAGE_WIDTH - 2 * MARGIN_X, 11.5 * cm))
        story.append(
            Paragraph(
                "Figura 1. Frequência de artigos por área SWEBOK no corpus classificado.",
                styles["caption"],
            )
        )

    cooccurrence_path = analysis_dir / "cooccurrence.png"
    if cooccurrence_path.exists():
        story.append(_image_flowable(cooccurrence_path, PAGE_WIDTH - 2 * MARGIN_X, 12.5 * cm))
        story.append(
            Paragraph(
                "Figura 2. Coocorrência entre áreas SWEBOK a partir das classificações atribuídas aos artigos.",
                styles["caption"],
            )
        )
        story.append(
            Paragraph(
                "A matriz de coocorrência ajuda a identificar quais áreas aparecem em conjunto. No corpus atual, isso é especialmente útil para entender como testes, qualidade, construção e modelos/métodos se articulam em torno de ferramentas, benchmarks, técnicas de análise e estudos empíricos.",
                styles["body"],
            )
        )

    story.append(Paragraph("4. Discussão por área do SWEBOK", styles["section"]))
    story.append(
        Paragraph(
            "Nas subseções a seguir, cada área do SWEBOK é discutida com base nos artigos classificados nela. A síntese textual usa o corpus já estudado, suas classificações e os resumos curtos produzidos durante a curadoria.",
            styles["body"],
        )
    )

    for index, subject in enumerate(subjects, start=1):
        subject_records = subject_index.get(subject, [])
        story.append(PageBreak())
        story.append(Paragraph(f"4.{index} {subject}", styles["subject"]))

        subject_count = len(subject_records)
        primary_count = sum(1 for record in subject_records if record.primary_subject == subject)
        related = cooccurrence.get(subject, Counter()).most_common(3)
        related_text = _format_list_pt(
            [f"{related_subject} ({count})" for related_subject, count in related if count > 0]
        ) or "sem coocorrências expressivas"
        story.append(
            _metric_table(
                styles,
                [
                    (str(subject_count), "artigos na área"),
                    (str(primary_count), "tema primário"),
                    (_format_percentage(subject_count / max(1, len(records)) * 100.0), "% do corpus"),
                    (_truncate(related_text, 30), "maiores cruzamentos"),
                ],
            )
        )
        story.append(Spacer(1, 0.25 * cm))

        for paragraph in subject_discussion(subject, subject_records, len(records), cooccurrence):
            story.append(Paragraph(paragraph, styles["body"]))

        story.append(Paragraph("Artigos classificados nesta área", styles["body"]))
        for paper_number, record in enumerate(subject_records, start=1):
            meta_bits = []
            if record.year:
                meta_bits.append(str(record.year))
            meta_bits.append(_authors_label(record.authors))
            meta_bits.append(f"confiança {record.confidence}")
            if record.primary_subject == subject:
                meta_bits.append("tema primário nesta área")
            story.append(Paragraph(f"{paper_number}. {record.title}", styles["paper_title"]))
            story.append(Paragraph(" • ".join(meta_bits), styles["paper_meta"]))
            story.append(Paragraph(record.summary, styles["paper_body"]))

    story.append(PageBreak())
    story.append(Paragraph("5. Conclusões", styles["section"]))
    story.append(
        Paragraph(
            "O corpus analisado mostra uma literatura de QSE com forte presença de pesquisas sobre testes, qualidade e modelos/métodos, frequentemente conectadas a ferramentas, estudos empíricos e propostas de suporte ao desenvolvimento de software quântico. Ao mesmo tempo, áreas como gestão, economia, requisitos e configuração já aparecem no corpus, embora ainda com menor volume de trabalhos.",
            styles["body"],
        )
    )
    story.append(
        Paragraph(
            "Como este relatório é gerado automaticamente a partir dos artefatos do projeto, ele pode ser reexecutado sempre que novas classificações, revisões ou figuras forem produzidas.",
            styles["body"],
        )
    )

    logger.info("Building PDF report at %s", output_path)
    doc.multiBuild(story)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate a styled PDF report for the QSE/SWEBOK study.")
    parser.add_argument("--classifications-dir", type=Path, default=CLASSIFICATIONS_DIR)
    parser.add_argument("--extracted-dir", type=Path, default=EXTRACTED_DIR)
    parser.add_argument("--analysis-dir", type=Path, default=ANALYSIS_DIR)
    parser.add_argument("--subjects-path", type=Path, default=SUBJECTS_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args(argv)

    if not args.classifications_dir.exists():
        raise SystemExit(f"Classifications directory not found: {args.classifications_dir}")
    if not args.extracted_dir.exists():
        raise SystemExit(f"Extracted directory not found: {args.extracted_dir}")
    if not args.subjects_path.exists():
        raise SystemExit(f"SWEBOK subjects file not found: {args.subjects_path}")

    subjects = _load_subjects(args.subjects_path)
    records = load_corpus(args.classifications_dir, args.extracted_dir)
    if not records:
        raise SystemExit("No classification records available to generate the report.")

    build_report(args.output, records, subjects, args.analysis_dir)
    logger.info("Report generated successfully at %s", args.output)


if __name__ == "__main__":
    main()
