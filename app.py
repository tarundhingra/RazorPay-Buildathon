import streamlit as st
import pandas as pd
import json
import os
from dotenv import load_dotenv
from google import genai

# Import our existing engine so the UI can trigger it
import recon_engine

# Load environment variables (API Key)
load_dotenv()

def load_audit_log():
    """Reads the JSONL log file and splits it into matches and exceptions."""
    matches = []
    exceptions = []
    
    if not os.path.exists("audit_log.jsonl"):
        return matches, exceptions

    with open("audit_log.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                record = json.loads(line)
                if record.get("method") in ["exact", "fuzzy"]:
                    matches.append(record)
                else:
                    exceptions.append(record)
                    
    return matches, exceptions

def display_tables(matches, exceptions):
    """Draws the data tables on the screen using Pandas DataFrames."""
    st.header("1. Matched Records (Pass 1 & 2)")
    if matches:
        df_matches = pd.DataFrame(matches)
        # Reorder columns to look nice
        cols = ["method", "confidence", "ledger_id", "rzp_id", "bank_id", "reason"]
        st.dataframe(df_matches[cols], use_container_width=True)
    else:
        st.info("No matches found.")

    st.header("2. AI Exceptions (Pass 3)")
    if exceptions:
        df_exceptions = pd.DataFrame(exceptions)
        cols = ["category", "primary_id", "matched_rzp_ids", "reason"]
        st.dataframe(df_exceptions[cols], use_container_width=True)
    else:
        st.info("No exceptions found.")

def ask_the_agent(user_query, raw_log_data):
    """Sends the user's question and the raw audit log to Gemini to get an answer."""
    client = genai.Client()
    
    prompt = f"""
    You are ReconAgent, an AI finance assistant. 
    A merchant is asking a question about their reconciliation results.
    Answer their question clearly and concisely, based ONLY on this audit log:
    
    {raw_log_data}
    
    Merchant Question: {user_query}
    """
    
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt
    )
    return response.text

def main():
    st.set_page_config(page_title="ReconAgent Dashboard", layout="wide")
    st.title("ReconAgent: AI-Powered Reconciliation")
    st.markdown("Automatically reconcile Razorpay, Bank, and Ledger records.")

    # 1. Run Pipeline Button
    if st.button("Run Recon Pipeline", type="primary"):
        with st.spinner("Running Exact, Fuzzy, and AI passes..."):
            recon_engine.main()
        st.success("Reconciliation complete! Audit log updated.")

    # 2. Load Data
    matches, exceptions = load_audit_log()

    if not matches and not exceptions:
        st.info("Click the button above to run the pipeline and see results.")
        return

    # 3. Show Tables
    display_tables(matches, exceptions)

    # 4. Agent Q&A Chatbox
    st.divider()
    st.header("3. Ask the Agent")
    st.markdown("Type a question like: *'Why wasn't ORD_1001 matched?'* or *'Explain batch BNK_BATCH_1'*")
    
    user_query = st.text_input("Your question:")
    
    if user_query:
        with st.spinner("Agent is checking the logs..."):
            with open("audit_log.jsonl", "r") as f:
                raw_logs = f.read()
                
            try:
                answer = ask_the_agent(user_query, raw_logs)
                st.info(answer)
            except Exception as e:
                st.error(f"Failed to reach AI: {e}")

if __name__ == "__main__":
    main()