from recon_engine import get_exact_matches, get_fuzzy_matches

def test_exact_match():
    """Tests that a perfectly aligned transaction is caught by Pass 1."""
    ledgers = [{"order_id": "ORD_1", "expected_amount": "100.00"}]
    rzps = [{
        "transaction_id": "TXN_1", "order_id": "ORD_1", 
        "amount": "100.00", "settled_amount": "98.00", "settlement_date": "2026-03-01"
    }]
    banks = [{
        "bank_line_id": "BNK_1", "narration_text": "RAZORPAY/TXN_1", 
        "amount": "98.00", "value_date": "2026-03-02" # 1 day later is standard
    }]
    
    matches, un_l, un_r, un_b = get_exact_matches(ledgers, rzps, banks)
    
    # It should find 1 exact match and leave the unmatched lists empty
    assert len(matches) == 1
    assert matches[0]["method"] == "exact"
    assert len(un_l) == 0
    assert len(un_r) == 0

def test_fuzzy_match_timing_lag():
    """Tests that a transaction delayed by a long weekend is caught by Pass 2, not Pass 1."""
    ledgers = [{"order_id": "ORD_2", "expected_amount": "100.00"}]
    rzps = [{
        "transaction_id": "TXN_2", "order_id": "ORD_2", 
        "amount": "100.00", "settled_amount": "98.00", "settlement_date": "2026-03-01"
    }]
    banks = [{
        "bank_line_id": "BNK_2", "narration_text": "RAZORPAY/TXN_2", 
        "amount": "98.00", "value_date": "2026-03-05" # 4 days later (too late for exact match)
    }]
    
    # 1. Exact match should reject it (date gap > 1)
    exact, un_l, un_r, un_b = get_exact_matches(ledgers, rzps, banks)
    assert len(exact) == 0
    
    # 2. Fuzzy match should catch it (date gap is <= 5)
    fuzzy, final_l, final_r, final_b = get_fuzzy_matches(un_l, un_r, un_b)
    
    assert len(fuzzy) == 1
    assert fuzzy[0]["method"] == "fuzzy"
    assert len(final_b) == 0

def test_fuzzy_match_fee_mismatch():
    """Tests that a transaction with a slightly wrong bank amount is caught by Pass 2."""
    ledgers = [{"order_id": "ORD_3", "expected_amount": "100.00"}]
    rzps = [{
        "transaction_id": "TXN_3", "order_id": "ORD_3", 
        "amount": "100.00", "settled_amount": "98.00", "settlement_date": "2026-03-01"
    }]
    banks = [{
        "bank_line_id": "BNK_3", "narration_text": "RAZORPAY/TXN_3", 
        "amount": "96.50", "value_date": "2026-03-02" # Amount is slightly off
    }]
    
    # Exact match rejects bad amounts
    exact, un_l, un_r, un_b = get_exact_matches(ledgers, rzps, banks)
    assert len(exact) == 0
    
    # Fuzzy match catches small fee differences
    fuzzy, final_l, final_r, final_b = get_fuzzy_matches(un_l, un_r, un_b)
    assert len(fuzzy) == 1
    assert fuzzy[0]["method"] == "fuzzy"