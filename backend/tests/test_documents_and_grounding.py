"""Zero-cost checks for the generic document pipeline and answer routing (no network, no paid calls).

Run:  .venv\\Scripts\\python.exe tests\\test_documents_and_grounding.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.prompt import build_messages  # noqa: E402
from app.services.document_service import (  # noqa: E402
    ExtractionError, _quality_gate, Extraction, build_chunks, extract, redact_contact_pii, sectionize,
    vocabulary_terms,
)
from app.services.meeting_service import clean_transcript_text  # noqa: E402

PASSED = 0


def check(name, cond):
    global PASSED
    assert cond, name
    PASSED += 1
    print(f"[PASS] {name}")


# --- Intent routing: general questions never pull CV evidence -------------------------------------
# (keyword intent routing was removed: documents are always provided and the model decides relevance)

# --- Pattern-based contact redaction (no stored personal values) -----------------------------------
red = redact_contact_pii("Jane Doe\nFlat 12, Green Society\nSome City, State - 560001\n(+1) 555 123 4567\njane.doe@ex.\ncom\nSkills: SQL")
check("redacts multi-line postal address", "Green Society" not in red and "560001" not in red)
check("redacts phone", "555 123 4567" not in red)
check("redacts email split across lines", "jane.doe" not in red)
check("keeps normal content", "Skills: SQL" in red and "Jane Doe" in red)
check("does not redact date ranges", "January 2019 - March 2023" in redact_contact_pii("Role A\nJanuary 2019 - March 2023"))

# --- Sectionizing keeps role + dates together --------------------------------------------------------
blocks = sectionize([(1, "EXPERIENCE\nAcme Corp - Data Engineer\nJANUARY 2021 - PRESENT\n• Built pipelines\nSKILLS\nPython, SQL")])
titles = [t for _, t, _ in blocks]
check("date-range heading merged with role line", any("Acme Corp - Data Engineer" in (t or "") and "2021" in (t or "") for t in titles))
check("skills section detected", "Skills" in titles)

# --- Quality gate: never index empty or garbled text --------------------------------------------------
try:
    _quality_gate(Extraction(pages=[(1, "   \n ")], method="x"), is_pdf=True)
    check("empty PDF rejected", False)
except ExtractionError as exc:
    check("empty/scanned PDF rejected with OCR message", "OCR" in str(exc))
try:
    _quality_gate(Extraction(pages=[(1, "(cid:12)(cid:44)" * 30)], method="x"), is_pdf=True)
    check("garbled rejected", False)
except ExtractionError as exc:
    check("garbled PDF text rejected", "garbled" in str(exc))

with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "notes.txt"
    p.write_text("Project Notes\nWe built a Databricks pipeline with Delta Lake and Airflow on MWAA.\n", encoding="utf-8")
    ex = extract(str(p))
    chunks = build_chunks(ex.pages)
    check("plain text extracted and chunked", chunks and "Databricks" in chunks[0]["content"])
    bad = Path(td) / "x.txt"
    bad.write_bytes(b"\x00\x01\x02" * 100)
    try:
        extract(str(bad))
        check("binary .txt rejected", False)
    except ExtractionError:
        check("binary file disguised as .txt rejected", True)

cv = Path(__file__).resolve().parents[1] / "uploads" / "00000000-0000-0000-0000-000000000002" / "Ravishankar_Gavhane_RDSolution1.pdf"
if cv.exists():  # optional real-file check (two-column layout)
    ex = extract(str(cv))
    check("two-column layout detected in sample CV", "multi-column" in ex.method)
    text = "\n".join(t for _, t in ex.pages)
    i_head, i_bullet = text.find("ALPHA ICT"), text.find("Alexa-enabled")
    check("employer heading precedes its own bullets", 0 <= i_head < i_bullet)

# --- Vocabulary + transcript cleanup -----------------------------------------------------------------
v = vocabulary_terms(["We use PySpark and Delta Lake on Databricks, loading S3 data into Redshift via MWAA."])
check("vocabulary picks technical terms", {"PySpark", "S3", "MWAA", "Redshift"} <= set(v))
check("repetition loop collapsed", clean_transcript_text("security, security, security, security access") == "security access")
check("genuine doubles kept", clean_transcript_text("I had had a plan") == "I had had a plan")

# --- Prompt: general questions carry no evidence; JD is never evidence --------------------------------
msgs = build_messages("What is a tuple?", None, "chat", full_docs=[], passages=[], transcript=[], prior_qas=[],
                      has_any_documents=True)
check("no documents -> only system + question", len(msgs) == 2 and "## EVIDENCE" not in msgs[1]["content"])
check("system prompt forbids inventing frameworks", "framework" in msgs[0]["content"] and "Never invent" in msgs[0]["content"])


# --- Transcript assembly: chunk-edge punctuation must not split one spoken question ----------------
from app.services.question_detector import detect_questions  # noqa: E402
from app.services.transcript_assembler import assemble_utterances  # noqa: E402
from app.rag.retriever import TranscriptHit  # noqa: E402
import uuid as _uuid  # noqa: E402


def _segs(items):
    return [{"id": str(i), "start_offset_ms": o, "end_offset_ms": o + 2000, "text": t} for i, (t, o) in enumerate(items)]


utts = assemble_utterances(_segs([
    ("Thanks for joining today.", 0),
    ("What data quality framework?", 6000), ("Did you implement, or was it?", 8000),
    ("Implemented in the project.", 10000), ("What were some valuable", 12000), ("rules enforced?", 14000),
    ("Okay, next topic.", 20000)]))
check("pause splits utterances; continuous chunks stay together", len(utts) == 3)
check("split question merged into ONE complete question",
      utts[1].questions == ["What data quality framework? Did you implement, or was it? Implemented in the project. "
                            "What were some valuable rules enforced?"])
check("raw segment ids/timestamps preserved", utts[1].segment_ids == ["1", "2", "3", "4", "5"] and utts[1].start_offset_ms == 6000)
check("reply after a question is not part of it", [q.text for q in detect_questions("Do you use Spark? Yes. We run it nightly on a big cluster with autoscaling.")] == ["Do you use Spark?"])
check("lead-in words still detect a question", detect_questions("That was the plan. Next, tell me about yourself.")[0].text.startswith("Next, tell me"))
check("statements are not questions", detect_questions("The weather is nice today and we are happy.") == [])
check("short answer after a complete question is excluded",
      [q.text for q in detect_questions("What happens if the job fails halfway? We retry it.")] == ["What happens if the job fails halfway?"])

# --- Prompt: the exact current question leads; transcript is bounded background -------------------
hist = [TranscriptHit(_uuid.uuid4(), i * 2000, f"unrelated line {i}: can you explain python decorators?") for i in range(30)]
msgs = build_messages("What is SCD type 2?", "selected SCD text", "chat", full_docs=[], passages=[], transcript=hist,
                      prior_qas=[], has_any_documents=True)
u = msgs[-1]["content"]
check("question is last", u.rstrip().endswith("What is SCD type 2?"))
check("whole session transcript kept", u.count("unrelated line") == 30)
check("selected text kept as context", "selected SCD text" in u)

# --- Chat memory: documents always present, earlier turns replayed as real chat messages --------------
from app.rag.retriever import FullDoc, PriorQA  # noqa: E402
cv = FullDoc(_uuid.uuid4(), "cv.pdf", "cv", "Built pipelines on Glue and Redshift.", None)
jdoc = FullDoc(_uuid.uuid4(), "jd.txt", "jd", "Need AWS data engineer.", None)
prior = [PriorQA(_uuid.uuid4(), "How would you load data?", "full", "I'd use Glue, not EMR, for loading.", None),
         PriorQA(_uuid.uuid4(), "And scheduling?", "full", "MWAA for scheduling.", None)]
msgs = build_messages("Why did you choose that?", None, "chat", full_docs=[cv, jdoc], passages=[], transcript=[],
                      prior_qas=prior, has_any_documents=True)
roles = [m["role"] for m in msgs]
check("layout: system, docs, 2 past turns, final", roles == ["system", "user", "user", "assistant", "user", "assistant", "user"])
check("CV and JD present for a follow-up", "Built pipelines on Glue" in msgs[1]["content"] and "Need AWS data engineer" in msgs[1]["content"])
check("earlier decision kept in history", "Glue, not EMR" in msgs[3]["content"])
check("follow-up is last message", msgs[-1]["content"].rstrip().endswith("Why did you choose that?"))
check("system prompt defines conversation memory", "CONVERSATION MEMORY" in msgs[0]["content"])
check("system prompt forbids guessing acronyms and requires clarification", "Clarify" in msgs[0]["content"] and "acronym" in msgs[0]["content"].lower())

print(f"\nAll {PASSED} checks passed.")
