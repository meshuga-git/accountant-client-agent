"""Streamlit review console, always through the authorization-checked API."""

import json
import os

import httpx
import streamlit as st

st.set_page_config(page_title="Ledgerline | Review desk", page_icon="◫", layout="wide")
st.markdown(
    """<style>.stApp{background:#f5f4ee}h1,h2,h3{font-family:Georgia,serif;color:#163d36}[data-testid="stSidebar"]{background:#173f37}[data-testid="stSidebar"] *{color:#f5f4ee}.stMetric{background:white;padding:18px;border-radius:12px}div.stButton>button{border-radius:7px} </style>""",
    unsafe_allow_html=True,
)
API = os.getenv("LEDGERLINE_API", "http://127.0.0.1:8000")
with st.sidebar:
    st.title("◫ Ledgerline")
    st.caption("ACCOUNTANT WORKSPACE")
    token = st.text_input(
        "Demo bearer token", value="demo-accountant-token", type="password"
    )
    st.caption(
        "Known demo credentials. Local synthetic use only; not production authentication."
    )


def request(method, path, **kwargs):
    try:
        r = httpx.request(
            method,
            API + path,
            headers={"Authorization": "Bearer " + token},
            timeout=45,
            trust_env=False,
            **kwargs,
        )
        if r.status_code >= 400:
            st.error(r.json().get("detail", r.text))
            return None
        return r.json()
    except httpx.HTTPError:
        st.error(
            "API unavailable. Start: uvicorn accounting_agent.api:app --host 127.0.0.1"
        )
        return None


clients = request("GET", "/clients")
if not clients:
    st.stop()
with st.sidebar:
    cid = st.selectbox(
        "Client",
        options=[c["id"] for c in clients],
        format_func=lambda x: next(c["name"] for c in clients if c["id"] == x),
    )
    st.divider()
    st.caption("NEW YORK • USD BASE")
    st.caption(
        "Synthetic documents only. Suggestions are not tax advice. No filing, email sending, live bank access, or live ledger posting."
    )
client = next(c for c in clients if c["id"] == cid)
st.caption("REVIEW FIRST / EVERY CHANGE AUDITED")
st.title("A clearer month-end.")
st.write(client["name"])
docs = request("GET", f"/clients/{cid}/documents") or []
a, b, c, d = st.columns(4)
a.metric("Documents", len(docs))
b.metric("Need attention", sum(x["status"] == "draft" for x in docs))
c.metric("Approved", sum(x["status"] == "approved" for x in docs))
d.metric("Mock exported", sum(x["status"] == "exported" for x in docs))
st.warning(
    "Synthetic prototype • Offline template parsing is not an LLM. Scans abstain without configured vision. Tax rates are illustrative; locality and treatment require accountant review."
)
review, intake, bank, settings, trail = st.tabs(
    ["Review desk", "Intake", "Reconciliation", "Client controls", "Audit trail"]
)
with intake:
    st.subheader("Add source documents")
    file = st.file_uploader(
        "PDF, image or labeled text • 20 MB maximum",
        type=["pdf", "png", "jpg", "jpeg", "txt"],
    )
    backend = st.selectbox("Extraction path", ["offline", "llm", "vision"])
    if file and st.button("Queue document"):
        result = request(
            "POST",
            f"/clients/{cid}/upload",
            params={"backend": backend},
            files={"file": (file.name, file.getvalue(), file.type)},
        )
        if result:
            st.session_state["job_" + cid] = result["id"]
            st.success("Queued. Refresh status below; nothing posts automatically.")
    job = st.session_state.get("job_" + cid)
    if job:
        if st.button("Refresh processing status"):
            pass
        status = request("GET", f"/clients/{cid}/jobs/{job}")
        st.json(status)
    st.info(
        "CLI also supports bulk synthetic intake: ledgerline demo --limit 33. IMAP and watched-folder intake are not implemented."
    )
with review:
    if not docs:
        st.info("No documents yet. Upload a source or run ledgerline demo.")
    else:
        severity = {"Blocked": 0, "Needs review": 1, "Ready": 2}
        docs.sort(
            key=lambda x: (severity.get(x.get("review_status"), 3), x.get("date", ""))
        )
        selected = st.selectbox(
            "Exception queue",
            docs,
            format_func=lambda x: (
                f"{x.get('review_status')} · {x.get('vendor', 'Unknown vendor')} · {x.get('date', 'No date')} · {x.get('currency', '')} {x.get('total', '?')} · rev {x['version']}"
            ),
            key="doc_" + cid,
        )
        doc = selected
        identifier = doc["id"]
        prefix = f"/clients/{cid}/documents/{identifier}"
        left, right = st.columns([1, 1])
        with left:
            st.subheader("Source & evidence")
            pages = doc.get("extraction", {}).get("pages", [])
            if pages:
                page = st.selectbox(
                    "Page", [p["number"] for p in pages], key="page_" + identifier
                )
                r = httpx.get(
                    API + prefix + "/source",
                    params={"page": page},
                    headers={"Authorization": "Bearer " + token},
                    trust_env=False,
                )
                if r.status_code == 200:
                    st.image(r.content, use_container_width=True)
                else:
                    st.caption(
                        "Rendered page unavailable; original evidence text below."
                    )
            fields = doc.get("extraction", {}).get("fields", {})
            for name, field in fields.items():
                with st.expander(
                    f"{'✓' if field['grounded'] else '⚠'} {name}: {field['value']}"
                ):
                    st.code(field["evidence"], language=None)
                    st.caption(
                        f"Page {field['page']} · confidence {field['confidence']} · grounded {field['grounded']}"
                    )
            st.caption(
                "Corrected values are reviewer decisions, not rewritten source evidence."
            )
        with right:
            st.subheader("Review draft")
            if doc.get("issues"):
                st.dataframe(doc["issues"], hide_index=True, use_container_width=True)
            st.json(
                doc.get("journal")
                or {
                    "status": "Choose a valid category and correct fields to build a balanced draft"
                },
                expanded=True,
            )
            with st.form("correct_" + identifier):
                account = st.selectbox(
                    "Reviewed account",
                    [x["code"] for x in client["coa"]],
                    format_func=lambda x: (
                        x
                        + " · "
                        + next(c["name"] for c in client["coa"] if c["code"] == x)
                    ),
                )
                changes = st.text_area(
                    "Other corrections (JSON; amounts as strings)",
                    value="{}",
                    height=100,
                )
                correction_reason = st.text_input("Correction reason")
                if st.form_submit_button("Save new revision"):
                    try:
                        payload = json.loads(changes)
                        payload["category"] = {
                            "account": account,
                            "confidence": "1",
                            "source": "manual",
                            "rationale": correction_reason,
                        }
                        if request(
                            "POST",
                            prefix + "/correct",
                            json={
                                "version": doc["version"],
                                "reason": correction_reason,
                                "changes": payload,
                            },
                        ):
                            st.rerun()
                    except json.JSONDecodeError:
                        st.error("Enter valid JSON")
            reason = st.text_input(
                "Approval / export reason", key="reason_" + identifier
            )
            acknowledged = st.checkbox(
                "I reviewed the source and every listed major exception",
                key="ack_" + identifier,
            )
            confirmed = st.checkbox(
                "I explicitly confirm this exact revision for LOCAL MOCK export",
                key="export_" + identifier,
            )
            x, y = st.columns(2)
            if x.button("Approve revision", key="approve_" + identifier):
                if request(
                    "POST",
                    prefix + "/approve",
                    json={
                        "version": doc["version"],
                        "reason": reason,
                        "acknowledge_issues": acknowledged,
                    },
                ):
                    st.rerun()
            if y.button("Mock export", key="send_" + identifier):
                result = request(
                    "POST",
                    prefix + "/export",
                    json={
                        "version": doc["version"],
                        "reason": reason,
                        "confirmed": confirmed,
                    },
                )
                if result:
                    st.session_state["export_" + cid] = result
            result = st.session_state.get("export_" + cid)
            if result:
                st.download_button(
                    "Download mock journal CSV",
                    result["csv"],
                    file_name="ledgerline-mock-journal.csv",
                    mime="text/csv",
                )
with bank:
    st.subheader("Precision before recall")
    st.caption(
        "Suggestions never allocate or post automatically. Same currency, direction and exact amount; ambiguous candidates remain for review."
    )
    statement = st.file_uploader("Bank CSV or XML OFX", type=["csv", "ofx"])
    if statement and st.button("Import bank statement"):
        request(
            "POST",
            f"/clients/{cid}/bank",
            files={"file": (statement.name, statement.getvalue())},
        )
    matches = request("GET", f"/clients/{cid}/matches") or []
    for row in matches[:50]:
        with st.expander(
            f"{row['bank']['date']} · {row['bank']['description']} · {row['bank']['amount']} {row['bank']['currency']}"
        ):
            st.json(row)
    st.caption(
        "First 50 unmatched bank lines shown. Exact reviewed allocations, compound payments, partial payments and fee lines are available through the settlement API; no automatic settlement."
    )
    with st.expander("Review a settlement (advanced JSON)"):
        payload = st.text_area(
            "Explicit allocations, current versions and confirmation",
            value=json.dumps(
                {
                    "bank_ids": [],
                    "document_ids": [],
                    "allocations": {},
                    "version_map": {},
                    "reason": "",
                    "confirmed": False,
                    "difference_account": None,
                },
                indent=2,
            ),
        )
        if st.button("Confirm settlement proposal"):
            try:
                result = request(
                    "POST", f"/clients/{cid}/settlements", json=json.loads(payload)
                )
                if result:
                    st.json(result)
            except json.JSONDecodeError:
                st.error("Invalid JSON")
with settings:
    st.subheader("Period & client controls")
    st.json({"lock_date": client.get("lock_date"), "config": client["config"]})
    lock = st.text_input(
        "Lock through date (YYYY-MM-DD; blank clears)",
        value=client.get("lock_date") or "",
    )
    reason = st.text_input("Control change reason")
    if st.button("Save period lock"):
        if request(
            "POST",
            f"/clients/{cid}/configuration",
            json={"changes": {"lock_date": lock or None}, "reason": reason},
        ):
            st.rerun()
    st.caption(
        "Locks are rechecked at approval and export. Advanced configuration and manual vendor mappings are exposed in /docs."
    )
with trail:
    rows = request("GET", f"/clients/{cid}/audit") or []
    st.dataframe(
        [
            {
                k: r.get(k)
                for k in ["timestamp", "action", "subject", "reviewer", "reason"]
            }
            for r in sorted(rows, key=lambda r: r["timestamp"], reverse=True)
        ],
        hide_index=True,
        use_container_width=True,
    )
