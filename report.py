import json

def load_json_data() -> tuple[list[dict], list[dict]]:
    """Loads the audit log and the ground truth files."""
    audit_log = []
    with open("audit_log.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                audit_log.append(json.loads(line))

    with open("ground_truth.json", "r", encoding="utf-8") as f:
        ground_truth = json.load(f)

    return audit_log, ground_truth


def calculate_metrics(audit_log: list[dict], ground_truth: list[dict]) -> dict:
    """
    Calculates precision (accuracy of made matches) and
    recall (coverage of total expected matches).

    FIX (this version): previously, each audit entry was graded against
    ground_truth independently, with no tracking of which ground-truth
    record had already been "claimed" by an earlier audit entry. When two
    different audit entries both pointed at the same underlying
    ground-truth record — e.g. two DUPLICATE entries both referencing
    ORD_1001 (the real ledger row and its accidental duplicate) — both
    would independently match against the SAME single ground-truth record
    and both would increment true_positives, silently double-counting one
    real scenario as two correct resolutions.

    This version tracks claimed_truth_indices and only allows each
    ground-truth record to contribute one true positive, no matter how
    many audit entries reference it.
    """
    exact = [m for m in audit_log if m["method"] == "exact"]
    fuzzy = [m for m in audit_log if m["method"] == "fuzzy"]
    ai = [m for m in audit_log if m["method"] == "ai" and m.get("category") != "needs_manual_review"]
    needs_review = [m for m in audit_log if m.get("category") == "needs_manual_review"]
    flagged_collisions = [m for m in audit_log if m["method"] == "flagged"]

    total_expected = len(ground_truth)
    true_positives = 0
    claimed_truth_indices = set()  # NEW: prevents the same truth record being credited twice

    # 1. Grade Exact and Fuzzy Matches
    for match in exact + fuzzy:
        for i, truth in enumerate(ground_truth):
            if i in claimed_truth_indices:
                continue
            if truth.get("order_id") == match["ledger_id"]:
                if truth.get("transaction_id") == match["rzp_id"] and truth.get("bank_line_id") == match["bank_id"]:
                    true_positives += 1
                    claimed_truth_indices.add(i)
                break

    # 2. Grade AI Exceptions (excluding manual-review fallback entries,
    #    which reflect an unavailable AI pass, not the AI's own judgment)
    for match in ai:
        for i, truth in enumerate(ground_truth):
            if i in claimed_truth_indices:
                continue
            if truth.get("entity_id") == match["primary_id"] or truth.get("bank_line_id") == match["primary_id"]:
                if truth.get("match_type") == match["category"]:
                    true_positives += 1
                    claimed_truth_indices.add(i)
                break

    # NOTE: "flagged" collisions and "needs_manual_review" entries are
    # intentionally NOT counted as true positives or false positives.
    # They represent the system correctly declining to guess rather than
    # a match or a miss — they're reported explicitly below so they're
    # never invisible in the summary.

    total_made = len(exact) + len(fuzzy) + len(ai)

    precision = (true_positives / total_made) * 100 if total_made > 0 else 0
    recall = (true_positives / total_expected) * 100 if total_expected > 0 else 0

    return {
        "exact_count": len(exact),
        "fuzzy_count": len(fuzzy),
        "ai_count": len(ai),
        "flagged_count": len(flagged_collisions),
        "needs_review_count": len(needs_review),
        "precision": precision,
        "recall": recall,
        "true_positives": true_positives,
        "total_made": total_made,
        "total_audit_entries": len(audit_log),
        "total_expected": total_expected,
        "unclaimed_truth_count": total_expected - len(claimed_truth_indices),
    }


def print_report(metrics: dict, audit_log: list[dict]):
    """Prints a clean summary for the terminal."""
    total_resolved = metrics["exact_count"] + metrics["fuzzy_count"] + metrics["ai_count"]

    print("\n" + "=" * 50)
    print(" RECONCILIATION REPORT ")
    print("=" * 50)
    print(f"Total Ground Truth Scenarios: {metrics['total_expected']}")
    print(f"Total Audit Log Entries: {metrics['total_audit_entries']}")
    print(f"  - Exact Matches: {metrics['exact_count']}")
    print(f"  - Fuzzy Matches: {metrics['fuzzy_count']}")
    print(f"  - AI Exceptions Resolved: {metrics['ai_count']}")
    print(f"  - Flagged Collisions (duplicate candidates, not auto-matched): {metrics['flagged_count']}")
    print(f"  - Needs Manual Review (AI pass unavailable): {metrics['needs_review_count']}")

    # Sanity check #1: do audit entries reconcile with the counted buckets?
    accounted_for = total_resolved + metrics["flagged_count"] + metrics["needs_review_count"]
    if accounted_for != metrics["total_audit_entries"]:
        print(f"\n⚠️  WARNING: {metrics['total_audit_entries']} audit entries logged but only "
              f"{accounted_for} accounted for in the breakdown above. Investigate before trusting these numbers.")

    # Sanity check #2: were any ground-truth scenarios never claimed by
    # anything in the audit log at all? (True misses, not double-counts.)
    if metrics["unclaimed_truth_count"] > 0:
        print(f"\n⚠️  NOTE: {metrics['unclaimed_truth_count']} ground-truth scenario(s) were never "
              f"claimed by any audit entry — these are true recall misses, not just collisions.")

    print(f"\nTrue Positives: {metrics['true_positives']} "
          f"(out of {metrics['total_made']} resolved attempts, {metrics['total_expected']} total scenarios)")

    print("\n--- PERFORMANCE METRICS (scored against exact/fuzzy/AI-resolved only) ---")
    print(f"Precision (Accuracy of matches made): {metrics['precision']:.1f}%")
    print(f"Recall (Percentage of all issues found): {metrics['recall']:.1f}%")
    if metrics["flagged_count"] or metrics["needs_review_count"]:
        print(f"Note: {metrics['flagged_count'] + metrics['needs_review_count']} record(s) were deliberately "
              f"NOT auto-resolved (collision or AI unavailable) and are excluded from precision/recall — "
              f"they still require human review.")

    print("\n--- AI EXCEPTION LOG (What needs human review) ---")
    ai_logs = [m for m in audit_log if m["method"] == "ai"]
    if not ai_logs:
        print("No exceptions found.")
    else:
        for log in ai_logs:
            cat = log.get('category', 'UNKNOWN').upper()
            pid = log.get('primary_id', 'N/A')
            reason = log.get('reason', 'No reason provided.')
            print(f"[{cat}] ID: {pid}")
            print(f"       Reason: {reason}")

    if metrics["flagged_count"] > 0:
        print("\n--- FLAGGED COLLISIONS (possible duplicates, needs manual resolution) ---")
        for log in [m for m in audit_log if m["method"] == "flagged"]:
            print(f"[COLLISION] rzp_id: {log.get('rzp_id', 'N/A')}")
            print(f"       Candidates: {log.get('candidate_bank_ids', [])}")
            print(f"       Reason: {log.get('reason', 'No reason provided.')}")

    print("=" * 50 + "\n")


def main():
    audit_log, ground_truth = load_json_data()
    metrics = calculate_metrics(audit_log, ground_truth)
    print_report(metrics, audit_log)


if __name__ == "__main__":
    main()