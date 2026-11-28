"""Strict bounded imports. OFX XML only; legacy unclosed SGML is rejected."""

import csv
import hashlib
import io
import re
from datetime import date
from decimal import Decimal

from defusedxml import ElementTree

MAX_BYTES = 5_000_000
MAX_ROWS = 20000


def _data(data):
    if not isinstance(data, bytes):
        raise ValueError("Import expects bytes")
    if len(data) > MAX_BYTES:
        raise ValueError("Bank import size limit")
    if b"\x00" in data:
        raise ValueError("NUL bytes prohibited")
    return data.decode("utf-8-sig", errors="strict")


def _row(client_id, number, dt, amount, currency, description, reference=""):
    if not client_id:
        raise ValueError("Client required")
    date.fromisoformat(dt)
    if not re.fullmatch(r"-?\d+(?:\.\d{1,2})?", amount):
        raise ValueError("Amount must be finite decimal with at most two places")
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("ISO currency required")
    if len(description) > 1000 or len(reference) > 200:
        raise ValueError("Oversized description/reference")
    if any(ord(c) < 32 for c in description + reference):
        raise ValueError("Control characters prohibited")
    normalized = format(Decimal(amount).quantize(Decimal(".01")), "f")
    identifier = (
        reference
        or hashlib.sha256(
            f"{dt}|{normalized}|{currency}|{description}|{number}".encode()
        ).hexdigest()[:24]
    )
    return {
        "id": identifier,
        "client_id": client_id,
        "date": dt,
        "amount": normalized,
        "currency": currency,
        "description": description,
        "reference": reference,
        "direction": "outflow" if Decimal(amount) < 0 else "inflow",
        "status": "unmatched",
    }


def import_csv(data, client_id):
    text = _data(data)
    reader = csv.DictReader(io.StringIO(text), strict=True)
    expected = {"date", "amount", "currency", "description"}
    headers = reader.fieldnames or []
    if (
        len(headers) != len(set(headers))
        or not expected <= set(headers)
        or set(headers) - expected - {"reference", "id"}
    ):
        raise ValueError(
            "CSV requires date,amount,currency,description; optional reference,id"
        )
    result = []
    seen = set()
    for n, row in enumerate(reader, 1):
        if n > MAX_ROWS:
            raise ValueError("Row limit")
        if None in row or any(value is None for value in row.values()):
            raise ValueError("Malformed CSV row")
        item = _row(
            client_id,
            n,
            row["date"],
            row["amount"],
            row["currency"],
            row["description"],
            row.get("reference", ""),
        )
        if row.get("id"):
            item["id"] = row["id"]
        if item["id"] in seen:
            raise ValueError("Duplicate transaction identifier")
        seen.add(item["id"])
        result.append(item)
    return result


def import_ofx(data, client_id):
    text = _data(data)
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("DTD/entities prohibited")
    try:
        root = ElementTree.fromstring(text)
    except Exception as exc:
        raise ValueError("Strict XML OFX required") from exc
    if root.tag != "OFX":
        raise ValueError("OFX root required (namespaces unsupported)")
    currencies = root.findall(".//CURDEF")
    if len(currencies) != 1 or not currencies[0].text:
        raise ValueError("Exactly one statement currency required")
    currency = currencies[0].text.strip()
    result = []
    seen = set()
    for n, row in enumerate(root.findall(".//STMTTRN"), 1):
        if n > MAX_ROWS:
            raise ValueError("Row limit")

        def get(tag, required=True):
            nodes = row.findall(tag)
            if len(nodes) > 1 or (required and (not nodes or not nodes[0].text)):
                raise ValueError("Missing or repeated " + tag)
            return (nodes[0].text or "").strip() if nodes else ""

        stamp = get("DTPOSTED")
        if not re.fullmatch(r"\d{8}(?:\d{6}(?:\.\d+)?(?:\[[^\]]+\])?)?", stamp):
            raise ValueError("Invalid OFX date")
        dt = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}"
        item = _row(
            client_id,
            n,
            dt,
            get("TRNAMT"),
            currency,
            get("NAME", False) or get("MEMO", False),
            get("FITID"),
        )
        if item["id"] in seen:
            raise ValueError("Duplicate FITID")
        seen.add(item["id"])
        result.append(item)
    return result
