# ReconAgent 🕵️‍♂️💸

**Track 04: AI Finance Controller — Razorpay AI Buildathon**

ReconAgent is a lightweight, 3-pass reconciliation engine designed to solve the "three-way match" problem. When merchants receive a settlement from Razorpay, they have to align their internal Order Ledger, Razorpay's Settlement Report, and their actual Bank Statement. Due to timing lags, batched payouts, non-standard fee deductions, and accidental duplicates, these three sources rarely line up perfectly. ReconAgent automates the obvious matches and uses Google's Gemini 2.5 Flash to intelligently resolve the messy exceptions, providing a fully explainable audit trail.

## The 3-Pass Architecture
```mermaid
graph TD
    %% Define the input data sources
    subgraph Data Sources
        L[Merchant Ledger] 
        R[Razorpay Settlements] 
        B[Bank Statement]
    end

    %% Define the matching engine
    subgraph Core Engine: recon_engine.py
        P1[Pass 1: Exact Match<br/><i>Instant 1:1 ID & Amount match</i>]
        P2[Pass 2: Fuzzy Match<br/><i>Tolerance for slight fee & date gaps</i>]
        P3[Pass 3: AI Exception Handler<br/><i>Gemini 2.5 Flash unbundles batches</i>]
    end
    
    %% Define the outputs
    subgraph Output & UI
        AL[(Audit Log<br/>JSONL)]
        UI[Streamlit Dashboard<br/><i>+ Interactive Agent Q&A</i>]
    end

    %% Draw the connections
    L & R & B --> P1
    P1 -->|Clean Matches| AL
    P1 -->|Unmatched Leftovers| P2
    
    P2 -->|Fuzzy Matches| AL
    P2 -->|Messy Leftovers| P3
    
    P3 -->|Batches, Orphans, Duplicates| AL
    
    AL --> UI

**Design principle:** at every stage, if the system finds more than one equally valid candidate for a match, it does not guess — it flags the collision and routes it to the AI pass (and ultimately a human) rather than silently picking one. This is the core lesson from our own testing: a duplicate bank entry that's byte-for-byte identical to the original is fundamentally unresolvable by matching logic alone, and pretending otherwise produces silent, undetectable errors.

## Setup & Running

This project uses `uv` for fast dependency management.

**Clone & Setup:**
```bash
git clone <your-repo-url>
cd recon-agent
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
uv pip install google-genai python-dotenv streamlit pandas pytest
```

**Configure API:**
Create a `.env` file in the root directory:
```
GEMINI_API_KEY=your_api_key_here
```

**Generate Test Data & Run:**
```bash
# Generates realistic synthetic CSVs with hidden edge cases
python generate_data.py

# Runs the core matching engine (Passes 1, 2, and 3)
python recon_engine.py

# Scores the engine against the hidden ground truth
python report.py

# Launch the interactive dashboard
streamlit run app.py
```

## Performance Metrics

Tested on a synthetic dataset of **66 ground-truth scenarios** (clean matches, weekend timing delays, fee mismatches, batched bank payouts, duplicate ledger entries, and a deliberately unresolvable duplicate bank-line collision), scored against hidden ground truth with duplicate-credit protection (each ground-truth scenario can only be counted once, no matter how many audit entries reference it):

- **Exact Matches:** 49
- **Fuzzy Matches:** 8
- **AI-Resolved Exceptions:** 10 (batched settlements, orphans, and duplicates)
- **Flagged Collisions (deliberately not auto-matched):** 2
- **Precision:** 97.0%
- **Recall:** 98.5%

## Known Limitations & Edge Cases

The agent operates strictly on the data provided in the current batch. This leads to honest, logical limitations — including one we found, root-caused, and fixed during our own testing:

- **Identical duplicate bank entries:** If a bank statement contains two byte-for-byte identical lines (same narration, amount, and date — e.g. a bank-side double-post), there is no field-level signal that can distinguish which one is "real." Our first implementation silently accepted the first candidate found and mis-flagged the true original as an orphan. We added a **cardinality check**: if more than one bank line ties as a valid candidate for the same transaction, the system does not guess — it flags the collision and routes all tied candidates to the AI pass for explicit review. In our test data, the AI correctly identified both lines as mutual duplicates rather than picking a "winner." This costs us one recall point against a ground truth that happens to know which line was original — a trade-off we consider correct, since guessing confidently on unresolvable data is worse than an honest deferral.
- **"Orphaned" Duplicates:** If a merchant accidentally includes a duplicate ledger entry (not a bank-side duplicate), Pass 1 consumes the valid transaction and the AI correctly flags the leftover ledger row as an orphan/duplicate rather than force-matching it.
- **Missing Data:** If an order was cancelled before reaching Razorpay, or a bank fee was charged directly to the account, the AI correctly identifies these as unrelated exceptions needing human review rather than forcing an inaccurate match.
- **AI pass availability:** If the Gemini API call fails or the API key is missing, every unresolved record is written to the audit log as `needs_manual_review` rather than silently dropped — the total record count in the audit trail always reconciles with the input batch size.
- **Fee-mismatch detection** currently relies on a general amount/date tolerance window rather than an explicit expected-fee-vs-actual-fee comparison — a near-term improvement, not yet a distinct check.

## Grading Methodology Note

Our scoring script (`report.py`) tracks which ground-truth scenarios have already been credited, so a scenario referenced by multiple audit entries (e.g. both sides of a flagged duplicate pair) can only contribute one true positive. This matters: an earlier version of our scoring double-counted such cases and reported inflated numbers (98.5% precision / 100% recall). The corrected, honest numbers above (97.0% / 98.5%) are what we stand behind.