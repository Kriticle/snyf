from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


OUT = Path("/Users/kriti/Documents/New project/artifacts/fashion_enrichment_system_design_v1.3.docx")

ACCENT = RGBColor(31, 78, 121)
MUTED = RGBColor(102, 112, 133)
LIGHT_FILL = "EEF4FA"
CODE_FILL = "F6F8FA"
BORDER = "D0D5DD"


def set_cell_shading(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_text(cell, text, bold=False, color=None):
    cell.text = ""
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    run = p.add_run(text)
    run.bold = bold
    run.font.name = "Arial"
    run.font.size = Pt(9)
    if color:
        run.font.color.rgb = color
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def set_table_borders(table, color=BORDER, size="6"):
    tbl = table._tbl
    tbl_pr = tbl.tblPr
    borders = tbl_pr.first_child_found_in("w:tblBorders")
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tbl_pr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        tag = "w:{}".format(edge)
        element = borders.find(qn(tag))
        if element is None:
            element = OxmlElement(tag)
            borders.append(element)
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), size)
        element.set(qn("w:space"), "0")
        element.set(qn("w:color"), color)


def set_cell_margins(table, top=100, start=140, bottom=100, end=140):
    tbl_pr = table._tbl.tblPr
    tbl_cell_mar = tbl_pr.find(qn("w:tblCellMar"))
    if tbl_cell_mar is None:
        tbl_cell_mar = OxmlElement("w:tblCellMar")
        tbl_pr.append(tbl_cell_mar)
    for m, v in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tbl_cell_mar.find(qn(f"w:{m}"))
        if node is None:
            node = OxmlElement(f"w:{m}")
            tbl_cell_mar.append(node)
        node.set(qn("w:w"), str(v))
        node.set(qn("w:type"), "dxa")


def set_table_width(table, widths):
    table.autofit = False
    tbl_pr = table._tbl.tblPr
    tbl_w = tbl_pr.find(qn("w:tblW"))
    if tbl_w is None:
        tbl_w = OxmlElement("w:tblW")
        tbl_pr.append(tbl_w)
    tbl_w.set(qn("w:type"), "dxa")
    tbl_w.set(qn("w:w"), str(sum(widths)))
    grid = table._tbl.tblGrid
    if grid is None:
        grid = OxmlElement("w:tblGrid")
        table._tbl.insert(0, grid)
    for child in list(grid):
        grid.remove(child)
    for width in widths:
        col = OxmlElement("w:gridCol")
        col.set(qn("w:w"), str(width))
        grid.append(col)
    for row in table.rows:
        for idx, cell in enumerate(row.cells):
            tc_w = cell._tc.get_or_add_tcPr().find(qn("w:tcW"))
            if tc_w is None:
                tc_w = OxmlElement("w:tcW")
                cell._tc.get_or_add_tcPr().append(tc_w)
            tc_w.set(qn("w:type"), "dxa")
            tc_w.set(qn("w:w"), str(widths[idx]))


def add_heading(doc, text, level=1):
    p = doc.add_heading(text, level=level)
    for run in p.runs:
        run.font.name = "Arial"
        run.font.color.rgb = ACCENT if level == 1 else RGBColor(0, 0, 0)
    return p


def add_body(doc, text="", bold_prefix=None):
    p = doc.add_paragraph(style="Body Text")
    if bold_prefix and text.startswith(bold_prefix):
        r = p.add_run(bold_prefix)
        r.bold = True
        p.add_run(text[len(bold_prefix):])
    else:
        p.add_run(text)
    return p


def add_bullet(doc, text, level=0):
    p = doc.add_paragraph(style="List Bullet" if level == 0 else "List Bullet 2")
    p.add_run(text)
    return p


def add_number(doc, text):
    p = doc.add_paragraph(style="List Number")
    p.add_run(text)
    return p


def add_code(doc, text):
    lines = text.strip("\n").split("\n")
    for i, line in enumerate(lines):
        p = doc.add_paragraph(style="Code Block")
        p.paragraph_format.space_after = Pt(0 if i < len(lines) - 1 else 8)
        run = p.add_run(line)
        run.font.name = "Courier New"
        run.font.size = Pt(9)
        shade_paragraph(p, CODE_FILL)


def shade_paragraph(paragraph, fill):
    p_pr = paragraph._p.get_or_add_pPr()
    shd = p_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        p_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def add_note(doc, title, body):
    table = doc.add_table(rows=1, cols=1)
    set_table_borders(table, color="BFD7EA", size="8")
    set_cell_margins(table, 160, 180, 160, 180)
    set_table_width(table, [9360])
    cell = table.cell(0, 0)
    set_cell_shading(cell, LIGHT_FILL)
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(4)
    r = p.add_run(title)
    r.bold = True
    r.font.name = "Arial"
    r.font.size = Pt(11)
    r.font.color.rgb = ACCENT
    p2 = cell.add_paragraph()
    p2.paragraph_format.space_after = Pt(0)
    p2.add_run(body)
    doc.add_paragraph()


def add_simple_table(doc, headers, rows, widths):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    set_table_width(table, widths)
    set_cell_margins(table)
    set_table_borders(table)
    for idx, header in enumerate(headers):
        set_cell_text(table.rows[0].cells[idx], header, bold=True, color=ACCENT)
        set_cell_shading(table.rows[0].cells[idx], LIGHT_FILL)
    for row in rows:
        cells = table.add_row().cells
        for idx, value in enumerate(row):
            set_cell_text(cells[idx], value)
    set_table_width(table, widths)
    return table


def configure_doc(doc):
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(1)
    section.bottom_margin = Inches(0.8)
    section.left_margin = Inches(1)
    section.right_margin = Inches(1)

    styles = doc.styles
    normal = styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(10.5)

    for style_name in ("Body Text", "List Bullet", "List Bullet 2", "List Number"):
        style = styles[style_name]
        style.font.name = "Arial"
        style.font.size = Pt(10.5)
        style.paragraph_format.space_after = Pt(6)
        style.paragraph_format.line_spacing = 1.08

    code = styles.add_style("Code Block", 1)
    code.font.name = "Courier New"
    code.font.size = Pt(9)
    code.paragraph_format.left_indent = Inches(0.15)
    code.paragraph_format.right_indent = Inches(0.15)
    code.paragraph_format.space_before = Pt(2)
    code.paragraph_format.space_after = Pt(2)

    for name, size, before, after in (
        ("Title", 22, 0, 6),
        ("Subtitle", 11, 0, 12),
        ("Heading 1", 15, 14, 6),
        ("Heading 2", 12.5, 10, 4),
        ("Heading 3", 11, 8, 3),
    ):
        style = styles[name]
        style.font.name = "Arial"
        style.font.size = Pt(size)
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)

    header = section.header.paragraphs[0]
    header.text = "Fashion Attribute Enrichment Platform"
    header.alignment = WD_ALIGN_PARAGRAPH.LEFT
    header.runs[0].font.name = "Arial"
    header.runs[0].font.size = Pt(8)
    header.runs[0].font.color.rgb = MUTED

    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    footer.text = "System Design v1.3   |   Confidential"
    footer.runs[0].font.name = "Arial"
    footer.runs[0].font.size = Pt(8)
    footer.runs[0].font.color.rgb = MUTED


def build():
    doc = Document()
    configure_doc(doc)

    title = doc.add_paragraph(style="Title")
    title.add_run("Fashion Attribute Enrichment Platform").bold = True
    sub = doc.add_paragraph(style="Subtitle")
    sub.add_run("System Design Document - v1.3").bold = True
    sub.add_run("\nCSV ingestion -> LLM enrichment -> vector search")

    add_heading(doc, "1. Problem Statement & Goals", 1)
    add_body(doc, "Fashion brands struggle to surface products based on subjective or nuanced attributes that are never captured in standard metadata - 'effortless vacation look', 'structured blazer energy', 'prints that pop'. Manual tagging is expensive and inconsistent at scale.")
    add_body(doc, "This platform accepts a customer's product catalog as a CSV of public image URLs plus raw metadata, extracts a configurable set of rich attributes using a pluggable LLM, and stores those attributes so they support both exact filtering and semantic vector search - all scoped to a customer-defined collection.")
    add_note(doc, "Goal", "Any customer uploads a CSV, defines their attribute taxonomy and which embedding/LLM functions to use, and gets a fully searchable enriched catalog - scoped to their collection - with products processed the moment they are ingested.")

    add_heading(doc, "NEW: Ranking & Scoring Strategy (v1)", 1)
    add_heading(doc, "1. Feature-Based Scoring Model", 2)
    add_body(doc, "Search ranking uses a linear feature-based model. Only features present in the query are included, every feature is constrained to [0,1], and all weights are equal for v1.")
    add_code(doc, "score = (f1 + f2 + f3 + ... + fn) / n")
    add_heading(doc, "2. Feature Definitions", 2)
    add_simple_table(
        doc,
        ["Feature", "Score definition"],
        [
            ["Vibe / semantic match", "Cosine similarity between the query and enriched product text."],
            ["Color", "Exact match = 1; fuzzy match via embedding or ontology mapping is in (0,1)."],
            ["Category", "Exact match = 1; otherwise 0."],
            ["Occasion / style attributes", "Cosine similarity between extracted query attributes and enriched product attributes."],
        ],
        [2700, 6660],
    )
    add_heading(doc, "3. Cosine Similarity Normalization", 2)
    add_body(doc, "All cosine similarity scores must be normalized before combination so heterogeneous features stay on the same scale.")
    add_code(doc, "normalized_score = (cosine_similarity + 1) / 2")
    add_bullet(doc, "All feature scores remain in [0,1].")
    add_bullet(doc, "No semantic feature dominates unintentionally.")
    add_bullet(doc, "Exact, fuzzy, and semantic signals can be combined safely.")
    add_heading(doc, "4. Retrieval + Re-ranking Pipeline", 2)
    for step in [
        "Embed the full query.",
        "Retrieve top-K candidates via vector search.",
        "Extract query attributes using a basic parser initially.",
        "Compute feature scores per candidate.",
        "Average the included feature scores into a final score.",
        "Return results sorted by final score.",
    ]:
        add_number(doc, step)

    add_heading(doc, "2. Key Design Decisions", 1)
    decisions = [
        ("Ingestion via CSV of public image URLs", "Customers do not stream images through our service. They supply a CSV file containing public image URLs hosted on their CDN, Shopify, or similar infrastructure alongside product metadata. Our system downloads images at enrichment time. We own the CSV schema, but v1 validation is intentionally permissive so minor row issues do not block an entire catalog upload."),
        ("Attributes are a first-class input parameter", "The attribute list is passed explicitly per request, or inferred from CSV metadata when appropriate. This allows different collections, or different runs on the same collection, to extract entirely different attribute sets. Attributes are stored alongside the enriched values so the schema is always self-describing."),
        ("Both enrich() and embed() are pluggable functions", "The worker calls two injected functions: enrich() for LLM-based attribute extraction, and embed() for converting the enriched text profile into a vector. Either can be swapped independently without touching worker logic."),
        ("Collection model - 1 customer : many collections", "A customer maps to a customer_id. They own many collections, such as 'SS25 Womenswear' or 'AW25 Accessories'. All data is namespaced by collection_id. Search is always scoped to one collection."),
        ("Per-product SQS messages - no batching delay", "Each CSV row becomes its own SQS message immediately after validation. Workers pick it up within seconds and Fargate scales to queue depth."),
        ("Update & delete are first-class operations", "Re-uploading a CSV row for an existing product_id triggers re-enrichment and atomically overwrites both the DynamoDB record and the OpenSearch document. Deletion is handled by a _deleted column in the CSV or an explicit API call."),
    ]
    for title_text, body in decisions:
        add_heading(doc, title_text, 2)
        add_body(doc, body)

    add_heading(doc, "3. CSV Format (Simplified v1)", 1)
    add_body(doc, "The v1 ingestion contract favors permissive validation. The ingestion API should accept the rows it can process, surface structured row-level errors and warnings, and avoid rejecting an entire CSV for minor issues.")
    add_heading(doc, "Required columns", 2)
    for item in ["product_id", "collection_id", "name", "image_urls"]:
        add_bullet(doc, item)
    add_heading(doc, "Optional columns", 2)
    for item in ["category", "description", "price", "currency", "brand", "attributes", "_deleted"]:
        add_bullet(doc, item)
    add_heading(doc, "Philosophy change", 2)
    for item in [
        "Do not reject the entire CSV for minor issues.",
        "Prefer permissive validation in v1.",
        "Attribute schema can be inferred from CSV metadata or provided separately.",
    ]:
        add_bullet(doc, item)
    add_heading(doc, "Example CSV row", 2)
    add_code(doc, """product_id,collection_id,name,category,image_urls,attributes,description,price,currency,brand
prod_001,coll_abc,Linen Wide-Leg Trouser,bottoms,https://cdn.brand.com/img1.jpg|https://cdn.brand.com/img2.jpg,"[""occasion"",""silhouette"",""fabric_feel"",""trend_vibe""]","Relaxed linen trousers with wide leg cut.",89.00,USD,Example Brand""")

    add_heading(doc, "4. Data Model", 1)
    add_heading(doc, "4.1 DynamoDB - ProductEnrichments table", 2)
    add_simple_table(
        doc,
        ["Field", "Purpose"],
        [
            ["PK", "collection_id"],
            ["SK", "product_id"],
            ["customer_id", "Owner namespace for authorization and billing."],
            ["raw_metadata", "Original catalog fields from CSV."],
            ["image_urls", "Public image URLs downloaded at enrichment time."],
            ["enriched_attrs", "Attribute name-value map returned by enrich()."],
            ["embedding_id", "Reference to the vector document/index entry."],
            ["status", "QUEUED, ENRICHING, ENRICHED, FAILED, DELETED."],
            ["version", "Optimistic concurrency guard for updates."],
        ],
        [2300, 7060],
    )
    add_heading(doc, "4.2 OpenSearch - vector index document", 2)
    add_code(doc, """{
  "_id": "{collection_id}#{product_id}",
  "collection_id": "coll_abc",
  "customer_id":   "cust_xyz",
  "product_id":    "prod_001",
  "enriched_text": "linen wide-leg trouser, occasion: resort, silhouette: wide-leg, fabric_feel: flowy, trend_vibe: coastal grandmother",
  "embedding":     [0.023, -0.118, 0.401, ...],
  "attrs": {
    "occasion":    "resort",
    "silhouette":  "wide-leg",
    "fabric_feel": "flowy",
    "trend_vibe":  "coastal grandmother"
  },
  "category": "bottoms",
  "price":    89.00
}""")

    add_heading(doc, "5. Pluggable Functions", 1)
    add_heading(doc, "5.1 enrich() - attribute extraction", 2)
    add_body(doc, "Called once per product. Receives the product metadata, raw image bytes downloaded from the CSV URLs, and the attribute list from the CSV row or resolved schema. Returns a dict mapping each attribute name to an extracted value.")
    add_code(doc, """async def enrich(
    product_id:       str,
    metadata:         dict,
    image_bytes:      list[bytes],
    attribute_schema: list[str],
) -> dict[str, Any]:
    # Returns: { "occasion": "resort", "silhouette": "wide-leg", ... }
    # Must return a value for every key in attribute_schema.
    # Missing keys are stored as null; never raise solely for a missing attr.""")
    add_heading(doc, "5.2 embed() - text vectorisation", 2)
    add_body(doc, "Called after enrich(). Receives the enriched text profile and returns a float vector. The dimensionality is determined by the injected embedding model. Dimension must be consistent within a collection; changing embed() for an existing collection requires full re-indexing.")
    add_code(doc, """async def embed(text: str) -> list[float]:
    # Returns: [0.023, -0.118, 0.401, ...]
    # Dimension must be consistent within a collection.""")

    add_heading(doc, "6. API Endpoints", 1)
    add_heading(doc, "6.1 Ingestion API", 2)
    add_code(doc, """POST /collections/{collection_id}/upload
Content-Type: multipart/form-data

{
  "csv": "<file>",
  "attribute_schema_id": "schema_womenswear_ss25",  // optional
  "enrich_function": "default_vision_enricher",     // optional
  "embed_function":  "default_text_embedder"        // optional
}""")
    add_heading(doc, "6.2 Attribute schema IDs - zero latency cost", 2)
    add_body(doc, "Customers may save a schema once via POST /attribute-schemas and get back a schema_id. They can reference that ID during upload instead of inlining the full JSON array on every row.")
    add_code(doc, """# Option A - inline attributes per row
product_id,...,attributes
prod_001,...,"[""occasion"",""silhouette"",""fabric_feel""]"

# Option B - reference a saved schema
product_id,...,attribute_schema_id
prod_001,...,schema_womenswear_ss25""")
    add_body(doc, "Why zero latency: the schema is resolved once during CSV validation via a single DynamoDB read at the start of the upload job. The resolved attribute list is written into every SQS message. The enrichment worker reads attributes directly from the SQS message and never looks up DynamoDB for the schema.")
    add_heading(doc, "6.3 Search API", 2)
    add_body(doc, "Two independent mechanisms compose in every search request and must not be confused.")
    add_bullet(doc, "Hard attribute filters are applied as OpenSearch keyword pre-filters on stored attrs.* fields before vector search. They are exact or range matches, use the inverted index, and remain deterministic.")
    add_bullet(doc, "Semantic retrieval embeds the query text using embed() and runs k-NN only on documents that survive the pre-filter step.")
    add_bullet(doc, "Feature re-ranking computes normalized per-feature scores on top-K candidates and sorts by the averaged final score.")
    add_code(doc, """POST /search
{
  "collection_id": "coll_abc",
  "query": "flowy summer dress for a beach wedding",
  "filters": {
    "attrs.occasion": ["resort", "wedding"],
    "category": "dresses",
    "price_max": 250
  },
  "top_k": 20
}""")
    add_heading(doc, "6.4 POST /search/filters - facet counts", 2)
    add_code(doc, """{ "collection_id": "coll_abc" }

{
  "facets": {
    "attrs.occasion":    { "casual": 312, "resort": 87, "evening": 44 },
    "attrs.silhouette":  { "wide-leg": 201, "slim": 180, "midi-wrap": 62 },
    "attrs.fabric_feel": { "flowy": 290, "structured": 143, "knit": 110 },
    "category":          { "bottoms": 280, "dresses": 150, "tops": 130 }
  },
  "total_products": 500
}""")

    add_heading(doc, "7. Product Lifecycle - Update & Delete", 1)
    add_heading(doc, "7.1 Update", 2)
    for step in [
        "CSV row is uploaded with a product_id that already exists in DynamoDB.",
        "Validation passes as normal and the existing record's version number is read.",
        "A new UPSERT SQS message is enqueued with the current version number.",
        "Worker checks the DynamoDB version before writing. If another update raced ahead, the message is retried with the new version.",
        "enrich() and embed() are called fresh. Old enriched_attrs and embedding are not reused.",
        "DynamoDB is overwritten with version incremented. OpenSearch is upserted by collection_id#product_id.",
    ]:
        add_number(doc, step)
    add_heading(doc, "7.2 Delete", 2)
    add_body(doc, "Deletion may be triggered via _deleted: true in the CSV for an existing product_id or via DELETE /products/{product_id}. The worker removes or tombstones the DynamoDB record, deletes the OpenSearch document, and tags related S3 objects for lifecycle deletion after 30 days.")

    add_heading(doc, "8. End-to-End Example - Upload to Search Result", 1)
    add_heading(doc, "8.1 Setup", 2)
    add_body(doc, "A customer, Arket (customer_id: cust_arket), creates a collection called 'SS25 Womenswear' (collection_id: coll_ss25). They want to extract occasion, silhouette, fabric_feel, and trend_vibe from every product.")
    add_heading(doc, "8.2 Customer uploads CSV", 2)
    add_code(doc, """product_id,collection_id,name,category,image_urls,attributes,description,price
prod_001,coll_ss25,Linen Wide-Leg Trouser,bottoms,https://arket.com/img/p1a.jpg|https://arket.com/img/p1b.jpg,"[""occasion"",""silhouette"",""fabric_feel"",""trend_vibe""]","Relaxed linen pull-on trousers.",89.00""")
    add_code(doc, """{
  "job_id": "job_8a3f",
  "accepted_rows": 498,
  "rejected_rows": 2,
  "warnings": 4,
  "status": "QUEUED"
}""")
    add_heading(doc, "8.3 Per-product SQS messages published", 2)
    add_code(doc, """{
  "customer_id": "cust_arket",
  "collection_id": "coll_ss25",
  "product_id": "prod_001",
  "image_urls": ["https://arket.com/img/p1a.jpg", "https://arket.com/img/p1b.jpg"],
  "metadata": { "name": "Linen Wide-Leg Trouser", "category": "bottoms" },
  "attribute_schema": ["occasion", "silhouette", "fabric_feel", "trend_vibe"],
  "operation": "UPSERT",
  "version": 0
}""")
    add_heading(doc, "8.4 Enrichment worker processes the message", 2)
    add_body(doc, "A Fargate worker downloads the images, calls enrich(), writes the enriched attributes, then calls embed() on the concatenated enriched profile.")
    add_code(doc, """{
  "occasion": "casual",
  "silhouette": "wide-leg",
  "fabric_feel": "flowy",
  "trend_vibe": "quiet luxury"
}""")
    add_heading(doc, "8.5 Customer searches", 2)
    add_body(doc, "A shopper searches for 'flowy summer trousers for a holiday'. Search embeds the query, retrieves candidates from the collection, extracts basic query attributes, computes normalized feature scores, averages included features, and returns the sorted product list.")
    add_code(doc, """{
  "results": [
    {
      "product_id": "prod_001",
      "name": "Linen Wide-Leg Trouser",
      "score": 0.934,
      "feature_scores": {
        "semantic": 0.91,
        "category": 1.0,
        "occasion": 0.89
      },
      "enriched_attrs": { "occasion": "casual", "silhouette": "wide-leg" },
      "price": 89.00
    }
  ]
}""")

    add_heading(doc, "9. Infrastructure", 1)
    add_heading(doc, "9.1 AWS services", 2)
    add_simple_table(
        doc,
        ["Service", "Role"],
        [
            ["API Gateway / ALB", "Routes ingestion and search API traffic."],
            ["ECS Fargate", "Runs ingestion API, search API, and enrichment workers."],
            ["SQS", "Buffers per-product UPSERT and DELETE messages."],
            ["DynamoDB", "Stores product state, schema references, job status, and metadata."],
            ["OpenSearch", "Stores vector embeddings, keyword attrs, filters, and facets."],
            ["S3", "Stores CSV uploads, job artifacts, and optional downloaded image caches."],
            ["CloudWatch", "Metrics, logs, alarms, and dashboards."],
        ],
        [2600, 6760],
    )
    add_heading(doc, "9.2 Fargate task sizing (initial)", 2)
    add_bullet(doc, "Ingestion API: 0.5 vCPU / 1 GB memory.")
    add_bullet(doc, "Search API: 1 vCPU / 2 GB memory, horizontally scaled by request rate.")
    add_bullet(doc, "Enrichment worker: 1-2 vCPU / 4 GB memory, scaled by SQS queue depth and LLM latency.")

    add_heading(doc, "10. Container Strategy", 1)
    add_heading(doc, "10.1 Repository layout", 2)
    add_code(doc, """repo/
  services/
    ingestion_api/      # FastAPI - CSV upload, validation, collection mgmt
    search_api/         # FastAPI - vector retrieval + feature re-ranking
    enrichment_worker/  # SQS poller, image downloader, enrich() + embed()
  shared/               # Pydantic models, AWS/OpenSearch clients
  functions/            # Default enrich() and embed() implementations
  infra/                # Terraform modules
  .github/workflows/    # CI/CD pipelines""")
    add_heading(doc, "10.2 Dockerfile pattern", 2)
    add_code(doc, """FROM python:3.12-slim
WORKDIR /app
COPY shared/ ./shared/
COPY functions/ ./functions/
COPY services/{service}/ ./
RUN pip install --no-cache-dir -r requirements.txt
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd -m appuser && chown -R appuser /app
USER appuser
CMD ["python", "-m", "main"]""")

    add_heading(doc, "11. CI/CD Pipeline", 1)
    add_heading(doc, "11.1 Trigger strategy", 2)
    add_body(doc, "Use three GitHub Actions workflows, one per service. Each triggers only on changes to its service directory or shared dependencies, preventing unrelated redeploys.")
    add_code(doc, """on:
  push:
    paths:
      - 'services/enrichment_worker/**'
      - 'shared/**'
      - 'functions/**'""")
    add_heading(doc, "11.2 Pipeline stages", 2)
    for item in ["Lint and type-check.", "Run unit tests with coverage threshold.", "Build and push Docker image tagged by git SHA.", "Scan image for HIGH and CRITICAL vulnerabilities.", "Deploy to staging and run smoke tests.", "Promote to production behind manual approval."]:
        add_bullet(doc, item)
    add_heading(doc, "11.3 Branch strategy", 2)
    add_bullet(doc, "feature/* - lint and unit tests only.")
    add_bullet(doc, "main - full pipeline: test -> build -> scan -> push -> staging -> integration -> prod.")
    add_bullet(doc, "Hotfix branches - expedited PRs to main with the same production gates.")
    add_bullet(doc, "Image tags use git SHA always; :latest and :stable only after prod deploy succeeds.")

    add_heading(doc, "12. Observability", 1)
    add_bullet(doc, "Upload metrics: accepted_rows, rejected_rows, warnings, validation_latency_ms.")
    add_bullet(doc, "Worker metrics: queue_depth, processing_latency_ms, image_download_failures, enrichment_failures, embedding_failures, DLQ count.")
    add_bullet(doc, "Search metrics: p50/p95 latency, vector retrieval latency, re-ranking latency, zero-result rate, top-K candidate count.")
    add_bullet(doc, "Business metrics: products enriched per collection, active collections, searches per customer, enrichment cost per product.")
    add_bullet(doc, "Structured logs include customer_id, collection_id, product_id, job_id, operation, version, and error_code.")

    add_heading(doc, "13. Open Questions & Next Steps", 1)
    for item in [
        "Query parsing sophistication: decide when to introduce LLM-based parsing beyond basic extraction.",
        "Weight learning: evaluate linear regression or learning-to-rank once click/purchase feedback exists.",
        "Attribute confidence handling: decide whether enrich() returns per-attribute confidence and how ranking consumes it.",
        "Color ontology / fuzzy attribute handling: define canonical color families and embedding-backed fuzzy matches.",
        "OpenSearch vs pgvector tradeoff: pgvector on RDS may be simpler if scale stays modest, especially below roughly 5M products.",
        "Rate limiting: start with one active ingestion job per collection and per-customer upload limits.",
        "Authentication: API key per customer_id initially; revisit JWT/OAuth2 for multi-user organizations.",
        "Image download failures: recommend three retries with exponential backoff before DLQ.",
        "Attribute schema versioning: decide whether new attributes trigger automatic re-enrichment or leave older products with null values.",
    ]:
        add_bullet(doc, item)

    doc.add_section(WD_SECTION.CONTINUOUS)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUT)


if __name__ == "__main__":
    build()
