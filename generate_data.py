import csv
import json
import random
from datetime import datetime, timedelta

# Fixed seed so outputs are 100% deterministic and reproducible
random.seed(42)

START_DATE = datetime(2026, 3, 1)

FIRST_NAMES = ["Aarav", "Priya", "Rohan", "Sneha", "Vikram", "Ananya", "Karan", "Pooja"]
LAST_NAMES = ["Sharma", "Verma", "Patel", "Mehta", "Nair", "Iyer", "Reddy", "Gupta"]


def generate_customer_name() -> str:
    """Returns a random full name from preset lists."""
    return f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"


def create_clean_transaction(index: int) -> dict:
    """
    Creates a normal transaction that matches cleanly across all three sources.
    Standard Razorpay fee is 2% + 18% GST on fee (2.36% total).
    """
    order_id = f"ORD_{1000 + index}"
    txn_id = f"TXN_{2000 + index}"
    amount = round(random.uniform(500, 15000), 2)
    fee = round(amount * 0.0236, 2)
    settled_amount = round(amount - fee, 2)

    order_date = START_DATE + timedelta(days=random.randint(0, 10))
    settlement_date = order_date + timedelta(days=1)  # T+1 settlement

    ledger_entry = {
        "order_id": order_id,
        "expected_amount": amount,
        "order_date": order_date.strftime("%Y-%m-%d"),
        "customer_name": generate_customer_name(),
        "status": "COMPLETED"
    }

    razorpay_entry = {
        "transaction_id": txn_id,
        "order_id": order_id,
        "amount": amount,
        "fee": fee,
        "settled_amount": settled_amount,
        "settlement_date": settlement_date.strftime("%Y-%m-%d")
    }

    bank_entry = {
        "bank_line_id": f"BNK_{3000 + index}",
        "narration_text": f"CMS/RAZORPAY/{txn_id}/{order_id}",
        "amount": settled_amount,
        "value_date": settlement_date.strftime("%Y-%m-%d")
    }

    match_record = {
        "order_id": order_id,
        "transaction_id": txn_id,
        "bank_line_id": bank_entry["bank_line_id"],
        "match_type": "clean",
        "description": "Clean 1-to-1 match across all three sources"
    }

    return {
        "ledger": ledger_entry,
        "razorpay": razorpay_entry,
        "bank": bank_entry,
        "ground_truth": match_record
    }


def create_timing_lag_transaction(index: int) -> dict:
    """
    Creates a transaction where the bank credit lands 2-3 days late
    due to bank holidays or weekend clearing lags.
    """
    data = create_clean_transaction(index)
    lag_days = random.randint(2, 3)

    bank_val_date = datetime.strptime(data["bank"]["value_date"], "%Y-%m-%d") + timedelta(days=lag_days)
    data["bank"]["value_date"] = bank_val_date.strftime("%Y-%m-%d")

    data["ground_truth"]["match_type"] = "timing_lag"
    data["ground_truth"]["description"] = f"Bank credit delayed by {lag_days} days"
    return data


def create_fee_mismatch_transaction(index: int) -> dict:
    """
    Creates a transaction where Razorpay charged a different fee rate
    (e.g., international card tier at ~3.5%), causing settled amount discrepancy.
    """
    data = create_clean_transaction(index)
    amount = data["ledger"]["expected_amount"]

    # Deduct non-standard 3.5% fee
    new_fee = round(amount * 0.035, 2)
    new_settled = round(amount - new_fee, 2)

    data["razorpay"]["fee"] = new_fee
    data["razorpay"]["settled_amount"] = new_settled
    data["bank"]["amount"] = new_settled

    data["ground_truth"]["match_type"] = "fee_mismatch"
    data["ground_truth"]["description"] = "Fee charged at non-standard rate (3.5% instead of 2%)"
    return data


def create_batched_settlement(batch_id: int, start_index: int, count: int = 3) -> dict:
    """
    Bundles multiple Razorpay settled transactions into one single lumped bank payout.
    Banks often credit a single batch payout instead of individual lines.
    """
    orders = []
    txns = []
    total_settled = 0.0
    settlement_date = (START_DATE + timedelta(days=12)).strftime("%Y-%m-%d")

    batch_ledger_entries = []
    batch_razorpay_entries = []

    for i in range(count):
        idx = start_index + i
        order_id = f"ORD_{1000 + idx}"
        txn_id = f"TXN_{2000 + idx}"
        amount = round(random.uniform(1000, 5000), 2)
        fee = round(amount * 0.0236, 2)
        settled = round(amount - fee, 2)
        total_settled += settled

        orders.append(order_id)
        txns.append(txn_id)

        batch_ledger_entries.append({
            "order_id": order_id,
            "expected_amount": amount,
            "order_date": (START_DATE + timedelta(days=11)).strftime("%Y-%m-%d"),
            "customer_name": generate_customer_name(),
            "status": "COMPLETED"
        })

        batch_razorpay_entries.append({
            "transaction_id": txn_id,
            "order_id": order_id,
            "amount": amount,
            "fee": fee,
            "settled_amount": settled,
            "settlement_date": settlement_date
        })

    bank_entry = {
        "bank_line_id": f"BNK_BATCH_{batch_id}",
        "narration_text": f"RAZORPAY NODAL PAYOUT BATCH_{batch_id}",
        "amount": round(total_settled, 2),
        "value_date": settlement_date
    }

    ground_truth = {
        "batch_id": f"BATCH_{batch_id}",
        "bank_line_id": bank_entry["bank_line_id"],
        "order_ids": orders,
        "transaction_ids": txns,
        "match_type": "batched_settlement",
        "total_amount": round(total_settled, 2),
        "description": f"Single bank credit covering {count} separate Razorpay transactions"
    }

    return {
        "ledger": batch_ledger_entries,
        "razorpay": batch_razorpay_entries,
        "bank": bank_entry,
        "ground_truth": ground_truth
    }


def create_orphan_records() -> tuple:
    """
    Creates records that genuinely have no match in the other systems
    (e.g., bank account charges, cancelled orders never paid).
    """
    orphan_bank = [
        {
            "bank_line_id": "BNK_ORPHAN_1",
            "narration_text": "ANNUAL DEBIT CARD AMC CHARGES",
            "amount": 295.00,
            "value_date": "2026-03-05"
        },
        {
            "bank_line_id": "BNK_ORPHAN_2",
            "narration_text": "SWEEPIN INTEREST CREDIT",
            "amount": 412.50,
            "value_date": "2026-03-10"
        }
    ]

    orphan_ledger = [
        {
            "order_id": "ORD_ABANDONED_1",
            "expected_amount": 3499.00,
            "order_date": "2026-03-04",
            "customer_name": generate_customer_name(),
            "status": "PAYMENT_FAILED"
        },
        {
            "order_id": "ORD_ABANDONED_2",
            "expected_amount": 1290.00,
            "order_date": "2026-03-08",
            "customer_name": generate_customer_name(),
            "status": "PENDING"
        }
    ]

    orphan_truth = [
        {"entity_id": "BNK_ORPHAN_1", "source": "bank", "match_type": "orphan", "description": "Bank account fee"},
        {"entity_id": "BNK_ORPHAN_2", "source": "bank", "match_type": "orphan", "description": "Bank interest credit"},
        {"entity_id": "ORD_ABANDONED_1", "source": "ledger", "match_type": "orphan", "description": "Failed order never processed by Razorpay"},
        {"entity_id": "ORD_ABANDONED_2", "source": "ledger", "match_type": "orphan", "description": "Pending/abandoned checkout"}
    ]

    return orphan_bank, orphan_ledger, orphan_truth


def save_csv(filename: str, fieldnames: list, rows: list) -> None:
    """Helper to write a list of dictionaries to a CSV file."""
    with open(filename, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    ledger_rows = []
    razorpay_rows = []
    bank_rows = []
    ground_truth_records = []

    current_idx = 1

    # 1. 45 Clean matches
    for _ in range(45):
        clean = create_clean_transaction(current_idx)
        ledger_rows.append(clean["ledger"])
        razorpay_rows.append(clean["razorpay"])
        bank_rows.append(clean["bank"])
        ground_truth_records.append(clean["ground_truth"])
        current_idx += 1

    # 2. 8 Timing lag matches
    for _ in range(8):
        lag = create_timing_lag_transaction(current_idx)
        ledger_rows.append(lag["ledger"])
        razorpay_rows.append(lag["razorpay"])
        bank_rows.append(lag["bank"])
        ground_truth_records.append(lag["ground_truth"])
        current_idx += 1

    # 3. 5 Fee mismatch matches
    for _ in range(5):
        fee_mismatch = create_fee_mismatch_transaction(current_idx)
        ledger_rows.append(fee_mismatch["ledger"])
        razorpay_rows.append(fee_mismatch["razorpay"])
        bank_rows.append(fee_mismatch["bank"])
        ground_truth_records.append(fee_mismatch["ground_truth"])
        current_idx += 1

    # 4. 2 Batched settlements (each bundling 3 Razorpay transactions)
    for b in range(1, 3):
        batch = create_batched_settlement(b, current_idx, count=3)
        ledger_rows.extend(batch["ledger"])
        razorpay_rows.extend(batch["razorpay"])
        bank_rows.append(batch["bank"])
        ground_truth_records.append(batch["ground_truth"])
        current_idx += 3

    # 5. Add 2 accidental duplicate entries (e.g. duplicate export row in ledger and bank)
    duplicate_ledger_entry = dict(ledger_rows[0])
    ledger_rows.append(duplicate_ledger_entry)

    duplicate_bank_entry = dict(bank_rows[0])
    duplicate_bank_entry["bank_line_id"] = f"{bank_rows[0]['bank_line_id']}_DUP"
    bank_rows.append(duplicate_bank_entry)

    ground_truth_records.append({
        "entity_id": duplicate_ledger_entry["order_id"],
        "source": "ledger",
        "match_type": "duplicate",
        "description": "Accidental duplicate row in merchant ledger"
    })
    ground_truth_records.append({
        "entity_id": duplicate_bank_entry["bank_line_id"],
        "source": "bank",
        "match_type": "duplicate",
        "description": "Accidental duplicate entry in bank statement"
    })

    # 6. Add orphans
    orphan_banks, orphan_ledgers, orphan_truths = create_orphan_records()
    bank_rows.extend(orphan_banks)
    ledger_rows.extend(orphan_ledgers)
    ground_truth_records.extend(orphan_truths)

    # Shuffle rows so they don't arrive in neatly aligned order
    random.shuffle(ledger_rows)
    random.shuffle(razorpay_rows)
    random.shuffle(bank_rows)

    # Save 3 CSV files
    save_csv(
        "razorpay_settlements.csv",
        ["transaction_id", "order_id", "amount", "fee", "settled_amount", "settlement_date"],
        razorpay_rows
    )

    save_csv(
        "bank_statement.csv",
        ["bank_line_id", "narration_text", "amount", "value_date"],
        bank_rows
    )

    save_csv(
        "merchant_ledger.csv",
        ["order_id", "expected_amount", "order_date", "customer_name", "status"],
        ledger_rows
    )

    # Save Ground Truth JSON
    with open("ground_truth.json", "w", encoding="utf-8") as f:
        json.dump(ground_truth_records, f, indent=2)

    print(f"Data generation complete:")
    print(f" - razorpay_settlements.csv : {len(razorpay_rows)} rows")
    print(f" - bank_statement.csv        : {len(bank_rows)} rows")
    print(f" - merchant_ledger.csv       : {len(ledger_rows)} rows")
    print(f" - ground_truth.json         : {len(ground_truth_records)} test scenarios recorded")


if __name__ == "__main__":
    main()