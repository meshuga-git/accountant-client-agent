"""One-command reproducible corpus/evaluation and local demo setup."""

import argparse
import json
from pathlib import Path

from .bank_import import import_csv
from .extraction import extract_document
from .service import Workflow


def main():
    p = argparse.ArgumentParser(description="Ledgerline synthetic accounting workbench")
    sub = p.add_subparsers(dest="command", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--out", default="demo_data")
    g.add_argument("--seed", type=int, default=42)
    e = sub.add_parser("eval")
    e.add_argument("action", choices=["run"])
    e.add_argument("--corpus", default="demo_data")
    e.add_argument("--out", default="reports")
    e.add_argument("--split", choices=["dev", "heldout", "all"], default="heldout")
    e.add_argument("--backend", choices=["offline", "llm"], default="offline")
    d = sub.add_parser("demo")
    d.add_argument("--corpus", default="demo_data")
    d.add_argument("--limit", type=int, default=33)
    sub.add_parser("seed")
    args = p.parse_args()
    if args.command == "generate":
        from .synthetic import generate_corpus

        print(json.dumps(generate_corpus(args.out, args.seed), indent=2))
        return
    if args.command == "eval":
        from .evaluation import run_evaluation
        from .providers import Provider

        print(
            json.dumps(
                run_evaluation(
                    args.corpus,
                    args.out,
                    backend=args.backend,
                    provider=Provider() if args.backend == "llm" else None,
                    split=args.split,
                ),
                indent=2,
                default=str,
            )
        )
        return
    work = Workflow()
    work.seed()
    if args.command == "seed":
        print("Three fictional clients created. Nothing approved or posted.")
        return
    root = Path(args.corpus).resolve()
    manifest = json.loads((root / "manifest.json").read_text())
    selected = []
    # One month per client first, rather than filling only the first tenant.
    for row in sorted(
        manifest["documents"], key=lambda r: (r["month"], r["client_id"], r["id"])
    ):
        if row["adversarial"]:
            continue
        selected.append(row)
        if len(selected) >= args.limit:
            break
    existing = {
        cid: {d["hash"] for d in work.list(cid, "document")}
        for cid in ("cafe", "designer", "construction")
    }
    imported = 0
    for row in selected:
        path = root / row["path"]
        extraction = extract_document(
            path, row["client_id"], render_dir=path.parent / "renders"
        )
        if extraction["hash"] in existing[row["client_id"]]:
            continue
        work.ingest(
            row["client_id"],
            {**extraction["values"], "type": extraction["type"]},
            extraction,
            str(path),
        )
        existing[row["client_id"]].add(extraction["hash"])
        imported += 1
    for cid in existing:
        work.import_bank(cid, import_csv((root / f"bank-{cid}.csv").read_bytes(), cid))
    print(
        f"Loaded {imported} review drafts plus synthetic bank lines. No automatic categories, approvals or postings. Sources stay local."
    )


if __name__ == "__main__":
    main()
