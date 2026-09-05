import csv
import json
import os
from datetime import datetime
from dotenv import load_dotenv

# Import the new SDK
from google import genai
from google.genai import types

# Load variables from .env file so the SDK can find GEMINI_API_KEY
load_dotenv()


def load_csv(filepath: str) -> list[dict]:
    """Reads a CSV file and returns a list of dictionaries."""
    with open(filepath, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def parse_date(date_str: str) -> datetime:
    """Converts a string date to a datetime object for comparison."""
    return datetime.strptime(date_str, "%Y-%m-%d")


def get_exact_matches(ledgers: list[dict], rzps: list[dict], banks: list[dict]) -> tuple:
    """
    Pass 1: Exact Match.

    FIX: previously took the first qualifying bank candidate and moved on
    (`break`). If two bank rows were identical on every checked field
    (e.g. an accidental duplicate bank posting), the loop would silently
    accept one of them as the "real" match and leave the true original
    unaccounted for, with no signal that a collision ever happened.

    Now we collect ALL qualifying candidates for each Razorpay transaction.
    If exactly one candidate exists, we match as before. If more than one
    exists, we do NOT guess — we flag the collision and leave every tied
    candidate (and the transaction) in the unmatched pool so Pass 3 can
    route it to human/AI review instead of it being silently resolved.
    """
    matches = []
    flagged = []
    unmatched_ledgers = ledgers.copy()
    unmatched_rzps = rzps.copy()
    unmatched_banks = banks.copy()

    for rzp in list(unmatched_rzps):
        matching_ledger = None
        for l in unmatched_ledgers:
            if l["order_id"] == rzp["order_id"] and float(l["expected_amount"]) == float(rzp["amount"]):
                matching_ledger = l
                break

        # Collect ALL bank rows that qualify as a match for this transaction,
        # instead of stopping at the first one.
        bank_candidates = []
        for b in unmatched_banks:
            if rzp["transaction_id"] in b["narration_text"] and float(b["amount"]) == float(rzp["settled_amount"]):
                date_diff = (parse_date(b["value_date"]) - parse_date(rzp["settlement_date"])).days
                if 0 <= date_diff <= 1:
                    bank_candidates.append(b)

        if matching_ledger and len(bank_candidates) == 1:
            matching_bank = bank_candidates[0]
            matches.append({
                "method": "exact",
                "confidence": "high",
                "reason": "Exact ID and amount match with standard timing.",
                "ledger_id": matching_ledger["order_id"],
                "rzp_id": rzp["transaction_id"],
                "bank_id": matching_bank["bank_line_id"]
            })
            unmatched_ledgers.remove(matching_ledger)
            unmatched_rzps.remove(rzp)
            unmatched_banks.remove(matching_bank)

        elif matching_ledger and len(bank_candidates) > 1:
            # COLLISION: more than one bank row ties for this transaction.
            # Do not resolve automatically — flag it and let Pass 3 decide.
            flagged.append({
                "method": "flagged",
                "confidence": "n/a",
                "reason": f"{len(bank_candidates)} bank lines are equally valid candidates "
                          f"for this transaction (possible duplicate bank entry). "
                          f"Routed to AI review instead of auto-matched.",
                "ledger_id": matching_ledger["order_id"],
                "rzp_id": rzp["transaction_id"],
                "candidate_bank_ids": [b["bank_line_id"] for b in bank_candidates]
            })
            # Intentionally do NOT remove rzp, ledger, or any bank candidates
            # from the unmatched pools — they all flow through to Pass 2/3.

    return matches, flagged, unmatched_ledgers, unmatched_rzps, unmatched_banks


def get_fuzzy_matches(unmatched_ledgers, unmatched_rzps, unmatched_banks) -> tuple:
    """
    Pass 2: Fuzzy Match.

    FIX: same collision risk as Pass 1 existed here (arguably worse, since
    the match condition is looser — ID substring OR order_id substring,
    plus a $200 / 5-day tolerance window). Applies the same
    collect-all-candidates-then-check-cardinality pattern.
    """
    matches = []
    flagged = []

    for rzp in list(unmatched_rzps):
        best_ledger = None
        for l in unmatched_ledgers:
            if l["order_id"] == rzp["order_id"]:
                best_ledger = l
                break

        bank_candidates = []
        for b in unmatched_banks:
            has_id = rzp["transaction_id"] in b["narration_text"] or rzp["order_id"] in b["narration_text"]
            amount_diff = abs(float(b["amount"]) - float(rzp["settled_amount"]))
            date_diff = (parse_date(b["value_date"]) - parse_date(rzp["settlement_date"])).days

            if has_id and amount_diff < 200 and 0 <= date_diff <= 5:
                bank_candidates.append(b)

        if best_ledger and len(bank_candidates) == 1:
            best_bank = bank_candidates[0]
            matches.append({
                "method": "fuzzy",
                "confidence": "medium",
                "reason": "Matched via IDs, despite minor fee discrepancy or date delay.",
                "ledger_id": best_ledger["order_id"],
                "rzp_id": rzp["transaction_id"],
                "bank_id": best_bank["bank_line_id"]
            })
            unmatched_ledgers.remove(best_ledger)
            unmatched_rzps.remove(rzp)
            unmatched_banks.remove(best_bank)

        elif best_ledger and len(bank_candidates) > 1:
            flagged.append({
                "method": "flagged",
                "confidence": "n/a",
                "reason": f"{len(bank_candidates)} bank lines tie within fuzzy tolerance "
                          f"for this transaction (possible duplicate bank entry). "
                          f"Routed to AI review instead of auto-matched.",
                "ledger_id": best_ledger["order_id"],
                "rzp_id": rzp["transaction_id"],
                "candidate_bank_ids": [b["bank_line_id"] for b in bank_candidates]
            })

    return matches, flagged, unmatched_ledgers, unmatched_rzps, unmatched_banks


def _fallback_manual_review(unmatched_ledgers, unmatched_rzps, unmatched_banks, reason: str) -> list[dict]:
    """
    FIX: previously, if the AI pass failed for any reason (missing API key,
    API error, malformed response), every leftover record simply vanished
    from the output — write_audit_log iterated an empty list and the
    audit trail undercounted with no trace of why. For a tool whose whole
    pitch is auditable explainability, a silently truncated audit log is
    worse than a visible crash.

    This produces an honest "needs manual review" entry for every record
    that the AI pass could not resolve, so the total record count in the
    audit log always reconciles with the total input count.
    """
    fallback = []
    for rzp in unmatched_rzps:
        fallback.append({
            "primary_id": rzp["transaction_id"],
            "category": "needs_manual_review",
            "matched_rzp_ids": [],
            "reason": f"AI reconciliation pass unavailable ({reason}) — "
                      f"flagged for manual review, not auto-resolved."
        })
    # Bank/ledger rows with no corresponding rzp entry also need a record
    # so nothing silently disappears from the audit trail.
    for l in unmatched_ledgers:
        fallback.append({
            "primary_id": l.get("order_id", "UNKNOWN"),
            "category": "needs_manual_review",
            "matched_rzp_ids": [],
            "reason": f"AI reconciliation pass unavailable ({reason}) — "
                      f"unmatched ledger row flagged for manual review."
        })
    for b in unmatched_banks:
        fallback.append({
            "primary_id": b.get("bank_line_id", "UNKNOWN"),
            "category": "needs_manual_review",
            "matched_rzp_ids": [],
            "reason": f"AI reconciliation pass unavailable ({reason}) — "
                      f"unmatched bank row flagged for manual review."
        })
    return fallback


def run_ai_pass(unmatched_ledgers, unmatched_rzps, unmatched_banks) -> list[dict]:
    """
    Pass 3: AI Match using the google-genai SDK.
    Uses native JSON schema enforcement to get reliable output.

    FIX: both failure paths (missing API key, exception during the call)
    now route through _fallback_manual_review instead of returning [],
    so no record is ever silently dropped from the audit trail.
    """
    if not os.environ.get("GEMINI_API_KEY"):
        print("WARNING: GEMINI_API_KEY not found in environment.")
        return _fallback_manual_review(
            unmatched_ledgers, unmatched_rzps, unmatched_banks,
            reason="missing GEMINI_API_KEY"
        )

    client = genai.Client()

    prompt = f"""
    You are an AI Finance Controller reconciling payments.
    I have 3 lists of unmatched financial records:
    Ledger: {json.dumps(unmatched_ledgers, default=str)}
    Razorpay: {json.dumps(unmatched_rzps, default=str)}
    Bank: {json.dumps(unmatched_banks, default=str)}

    Analyze these to find the remaining connections. Look specifically for:
    1. batched_settlement: Multiple Razorpay records whose settled amounts sum up to one Bank payout amount.
    2. duplicate: Rows that look like accidental identical copies (including bank
       lines flagged upstream as tied candidates for the same transaction).
    3. orphan: Bank fees, cancelled orders, or completely unrelated records.

    If two records are indistinguishable from each other in every field
    provided, do NOT guess which is "real" — flag both as category
    "duplicate" and explain that they could not be distinguished.
    """

    expected_schema = {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "primary_id": {"type": "STRING"},
                "category": {"type": "STRING"},
                "matched_rzp_ids": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"}
                },
                "reason": {"type": "STRING"}
            },
            "required": ["primary_id", "category", "matched_rzp_ids", "reason"]
        }
    }

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=expected_schema,
                temperature=0.1,  # Keep it low so the AI is analytical, not creative
            )
        )
        return json.loads(response.text)
    except Exception as e:
        print(f"AI pass failed: {e}")
        return _fallback_manual_review(
            unmatched_ledgers, unmatched_rzps, unmatched_banks,
            reason=str(e)
        )


def write_audit_log(exact, flagged_p1, fuzzy, flagged_p2, ai_results):
    """Saves every decision to a JSONL file (one JSON object per line)."""
    with open("audit_log.jsonl", "w", encoding="utf-8") as f:
        for match in exact:
            f.write(json.dumps(match) + "\n")
        for match in flagged_p1:
            f.write(json.dumps(match) + "\n")
        for match in fuzzy:
            f.write(json.dumps(match) + "\n")
        for match in flagged_p2:
            f.write(json.dumps(match) + "\n")
        for result in ai_results:
            ai_log = {
                "method": "ai",
                "confidence": "variable",
                "reason": result.get("reason", ""),
                "primary_id": result.get("primary_id"),
                "category": result.get("category"),
                "matched_rzp_ids": result.get("matched_rzp_ids", [])
            }
            f.write(json.dumps(ai_log) + "\n")


def main():
    ledgers = load_csv("merchant_ledger.csv")
    rzps = load_csv("razorpay_settlements.csv")
    banks = load_csv("bank_statement.csv")

    print("Running Pass 1 (Exact)...")
    exact_matches, flagged_p1, un_l, un_r, un_b = get_exact_matches(ledgers, rzps, banks)

    print("Running Pass 2 (Fuzzy)...")
    fuzzy_matches, flagged_p2, un_l, un_r, un_b = get_fuzzy_matches(un_l, un_r, un_b)

    print("Running Pass 3 (AI)...")
    ai_results = run_ai_pass(un_l, un_r, un_b)

    write_audit_log(exact_matches, flagged_p1, fuzzy_matches, flagged_p2, ai_results)

    total_flagged = len(flagged_p1) + len(flagged_p2)
    print("\n--- Final Reconciliation Summary ---")
    print(f"Total Razorpay Transactions: {len(rzps)}")
    print(f"Exact Matches: {len(exact_matches)}")
    print(f"Fuzzy Matches: {len(fuzzy_matches)}")
    print(f"Collisions Flagged (duplicate candidates, not auto-matched): {total_flagged}")
    print(f"AI Exceptions/Reviews Found: {len(ai_results)}")
    print("Full explainability trail saved to 'audit_log.jsonl'.")


if __name__ == "__main__":
    main()