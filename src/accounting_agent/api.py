"""Local synthetic demo API. Bearer grants select permitted clients server-side."""

import hashlib
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .bank_import import import_csv, import_ofx
from .extraction import extract_document
from .providers import Provider
from .service import CLIENTS, Workflow

app = FastAPI(title="Ledgerline • synthetic review-first accounting", version="0.1.0")
provider = Provider() if os.getenv("ACCOUNTING_LLM_API_KEY") else None
workflow = Workflow(provider=provider)
executor = ThreadPoolExecutor(max_workers=2)
STORAGE = Path(os.getenv("LEDGERLINE_STORAGE", "data/uploads")).resolve()
# Production authentication and deployment intentionally out of scope.
GRANTS = json.loads(
    os.getenv(
        "LEDGERLINE_TOKENS",
        json.dumps(
            {
                "demo-accountant-token": {
                    "reviewer": "demo-accountant",
                    "clients": list(CLIENTS),
                },
                "demo-cafe-token": {"reviewer": "demo-cafe", "clients": ["cafe"]},
            }
        ),
    )
)


@app.on_event("startup")
def startup():
    workflow.seed()
    STORAGE.mkdir(parents=True, exist_ok=True)
    for cid in CLIENTS:
        with workflow.store.transaction(cid) as tx:
            for job in tx.list("job"):
                if job["status"] in ("queued", "running"):
                    job["status"] = "failed"
                    job["error"] = (
                        "Worker interrupted; resubmit source. No posting occurred."
                    )
                    tx.put("job", job["id"], job)


def identity(authorization: str = Header(default="")):
    token = authorization.removeprefix("Bearer ")
    if token not in GRANTS:
        raise HTTPException(401, "Unknown demo credential")
    return GRANTS[token]


def authorized(cid, user):
    if cid not in user["clients"]:
        raise HTTPException(403, "Client outside credential grants")


def call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except KeyError as exc:
        raise HTTPException(404, "Not found in authorized client") from exc
    except (ValueError, PermissionError) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/health")
def health():
    return {
        "status": "ok",
        "synthetic_only": True,
        "database": "postgresql_rls" if workflow.store.url else "sqlite_local_not_rls",
        "posting": "disabled",
    }


@app.get("/clients")
def clients(user=Depends(identity)):
    return [call(workflow.client, cid) for cid in user["clients"]]


@app.get("/clients/{cid}/documents")
def documents(cid: str, user=Depends(identity)):
    authorized(cid, user)
    return call(workflow.list, cid, "document")


@app.get("/clients/{cid}/documents/{identifier}")
def document(cid: str, identifier: str, user=Depends(identity)):
    authorized(cid, user)
    return call(workflow.get, cid, "document", identifier)


@app.get("/clients/{cid}/audit")
def audit(cid: str, user=Depends(identity)):
    authorized(cid, user)
    return call(workflow.list, cid, "audit")


@app.get("/clients/{cid}/jobs/{identifier}")
def job(cid: str, identifier: str, user=Depends(identity)):
    authorized(cid, user)
    return call(workflow.get, cid, "job", identifier)


async def upload_bytes(file):
    raw = await file.read(20_000_001)
    if len(raw) > 20_000_000:
        raise HTTPException(413, "20 MB upload limit")
    return raw


def process_job(cid, identifier, path, backend, reviewer):
    def save(status, **values):
        with workflow.store.transaction(cid) as tx:
            job = tx.get("job", identifier)
            job.update(status=status, **values)
            tx.put("job", identifier, job)

    try:
        save("running")
        extraction = extract_document(
            path,
            cid,
            backend=backend,
            provider=provider,
            render_dir=path.parent / "renders",
        )
        doc = workflow.ingest(
            cid,
            {
                **extraction["values"],
                "type": extraction["type"],
                "direction": "credit_note"
                if extraction["type"] == "credit_note"
                else "expense",
            },
            extraction=extraction,
            source=str(path),
            reviewer=reviewer,
        )
        save("complete", document_id=doc["id"])
    except Exception as exc:
        save(
            "failed",
            error=type(exc).__name__
            + ": processing failed closed; source preserved for review",
        )


@app.post("/clients/{cid}/upload")
async def upload(
    cid: str,
    file: UploadFile = File(...),
    backend: str = "offline",
    user=Depends(identity),
):
    authorized(cid, user)
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".pdf", ".png", ".jpg", ".jpeg", ".txt"):
        raise HTTPException(415, "PDF/image/text only")
    if backend not in ("offline", "llm", "vision"):
        raise HTTPException(400, "Unknown extraction backend")
    if backend != "offline" and provider is None:
        raise HTTPException(409, "No model credentials configured")
    raw = await upload_bytes(file)
    identifier = str(uuid.uuid4())
    folder = STORAGE / cid / identifier
    folder.mkdir(parents=True)
    path = folder / ("source" + suffix)
    path.write_bytes(raw)
    record = {
        "id": identifier,
        "client_id": cid,
        "status": "queued",
        "hash": hashlib.sha256(raw).hexdigest(),
    }
    with workflow.store.transaction(cid) as tx:
        tx.put("job", identifier, record)
    executor.submit(process_job, cid, identifier, path, backend, user["reviewer"])
    return record


@app.get("/clients/{cid}/documents/{identifier}/source")
def source(cid: str, identifier: str, page: int | None = None, user=Depends(identity)):
    authorized(cid, user)
    doc = call(workflow.get, cid, "document", identifier)
    ref = doc.get("source")
    if page is not None:
        ref = next(
            (
                p.get("image_ref")
                for p in doc.get("extraction", {}).get("pages", [])
                if p["number"] == page
            ),
            None,
        )
    if not ref:
        raise HTTPException(404, "Source unavailable")
    path = Path(ref).resolve()
    if not path.is_file():
        raise HTTPException(404, "Source unavailable")
    # Path selected from stored document only, not user path. It must also be
    # under this tenant's upload tree or explicit synthetic corpus document tree.
    roots = [
        STORAGE / cid,
        Path("data/corpus/documents").resolve() / cid,
        Path("demo_data/documents").resolve() / cid,
    ]
    if not any(path.is_relative_to(root) for root in roots):
        raise HTTPException(403, "Source path outside configured synthetic storage")
    return FileResponse(path)


class Review(BaseModel):
    version: int
    reason: str = Field(min_length=1)
    acknowledge_issues: bool = False
    confirmed: bool = False


class Correction(Review):
    changes: dict


@app.post("/clients/{cid}/documents/{identifier}/correct")
def correct(cid: str, identifier: str, body: Correction, user=Depends(identity)):
    authorized(cid, user)
    return call(
        workflow.correct,
        cid,
        identifier,
        body.changes,
        body.version,
        user["reviewer"],
        body.reason,
    )


@app.post("/clients/{cid}/documents/{identifier}/approve")
def approve(cid: str, identifier: str, body: Review, user=Depends(identity)):
    authorized(cid, user)
    return call(
        workflow.approve,
        cid,
        identifier,
        body.version,
        user["reviewer"],
        body.reason,
        body.acknowledge_issues,
    )


@app.post("/clients/{cid}/documents/{identifier}/export")
def export(cid: str, identifier: str, body: Review, user=Depends(identity)):
    authorized(cid, user)
    return call(
        workflow.export,
        cid,
        identifier,
        body.version,
        user["reviewer"],
        body.reason,
        body.confirmed,
    )


@app.post("/clients/{cid}/bank")
async def bank(cid: str, file: UploadFile = File(...), user=Depends(identity)):
    authorized(cid, user)
    raw = await upload_bytes(file)
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".csv", ".ofx"):
        raise HTTPException(415, "CSV or XML OFX only")
    rows = call(import_csv if suffix == ".csv" else import_ofx, raw, cid)
    return {"rows": call(workflow.import_bank, cid, rows, user["reviewer"])}


@app.get("/clients/{cid}/matches")
def matches(cid: str, user=Depends(identity)):
    authorized(cid, user)
    return call(workflow.matches, cid)


class Settlement(BaseModel):
    bank_ids: list[str]
    document_ids: list[str]
    allocations: dict[str, str]
    version_map: dict[str, int]
    reason: str
    confirmed: bool = False
    difference_account: str | None = None


@app.post("/clients/{cid}/settlements")
def settlement(cid: str, body: Settlement, user=Depends(identity)):
    authorized(cid, user)
    return call(
        workflow.settle,
        cid,
        body.bank_ids,
        body.document_ids,
        body.allocations,
        body.version_map,
        user["reviewer"],
        body.reason,
        body.difference_account,
        body.confirmed,
    )


class Configuration(BaseModel):
    changes: dict
    reason: str


@app.post("/clients/{cid}/configuration")
def configuration(cid: str, body: Configuration, user=Depends(identity)):
    authorized(cid, user)
    return call(workflow.configure, cid, body.changes, user["reviewer"], body.reason)


class VendorRule(BaseModel):
    vendor: str
    account: str
    reason: str


@app.post("/clients/{cid}/vendor-rules")
def vendor_rule(cid: str, body: VendorRule, user=Depends(identity)):
    authorized(cid, user)
    return call(
        workflow.vendor_rule,
        cid,
        body.vendor,
        body.account,
        user["reviewer"],
        body.reason,
    )
