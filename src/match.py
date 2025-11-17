"""
This module implements deterministic, rule-based matching between financial transactions and document attachments (invoices, receipts).

The matching pipeline has three tiers:

1.  Reference Number Match (Golden Match)
    The reference number, once normalized, is treated as a high-confidence
    identifier. If both sides have a valid reference and they match,
    the system immediately returns a 1:1 match.

2.  Heuristic Scoring
    If no reference match is found, the system falls back
    to a scoring model based on three key signals:
      - Amount (absolute-value comparison)
      - Date (within a +/-14 day window)
      - Counterparty name (normalized exact match)

    A score of 0–3 is assigned based on how many signals agree.

3.  Confidence / Ambiguity Handling
    A match is only accepted if:
      - Score ≥ 2   (at least two signals agree)
      - Only one candidate has that highest score (no ties)
      - Counterparty mismatch veto applies: if both sides have names but they don't match after normalization, the score becomes 0.

Anything uncertain yields None - correctness is prioritized over guessing.
"""

import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Union

# --- Type aliases ---------------------------------------------------------

# Define types for clarity, matching according to run.py
Attachment = Dict[str, Any]
Transaction = Dict[str, Any]

# --- Global constants -----------------------------------------------------

# A +/-14 day window for comparing transaction dates with invoice/receipt dates.
DATE_WINDOW = timedelta(days=14)

# The user's own company. used to filter out own company name from attachments.
OUR_COMPANY_NAME = "Example Company Oy"

# A match is only "confident" if at least 2 of the 3 signals (Amount, Date, Name) match.
CONFIDENCE_THRESHOLD = 2


# --- Helper function: Reference number normalization -------------------------------

def _normalize_ref(ref: Optional[str]) -> Optional[str]:
    """
    Normalizes a reference number so it can be compared reliably.

    - Strips whitespace everywhere.
    - Removes leading zeros.
    - Returns None for empty or meaningless values.

    This allows:
       " 00123 " -> "123"
       "000"     -> None
    """
    if not ref:
        return None

    ref = re.sub(r'\s+', '', ref)   # remove all whitespace
    ref = ref.lstrip('0')           # remove leading zeros

    return ref or None              # empty -> None


# --- Helper function: Name normalization -------------------------------------------

def _normalize_name(name: Optional[str]) -> Optional[str]:
    """
    Normalizes names to allow consistent comparison of counterparties.

    Steps:
      - converts to lowercase
      - removes known business suffixes (oy, ltd, tmi etc.)
      - replaces non-alphanumeric characters with spaces
      - strips leading/trailing whitespace.
    """
    if not name:
        return None

    name = name.lower()

    # Remove common business suffixes
    suffixes = [r'\boy\b', r'\bab\b', r'\bltd\b', r'\binc\b', r'\bgmbh\b', r'\btmi\b']
    for suffix in suffixes:
        name = re.sub(suffix, '', name, flags=re.IGNORECASE)

    # Normalize punctuation to space. Keep Nordic characters.
    name = re.sub(r'[^a-z0-9äöå]+', ' ', name)

    return name.strip()


# --- Helper function: Extract attachment counterparties ----------------------------

def _get_att_counterparties(att: Attachment) -> Set[str]:
    """
    Returns a set of all normalized party names mentioned in an attachment
    (supplier, issuer, recipient) excluding the company's own name.

    This avoids incorrectly treating the user's company as the counterparty.
    """
    att_data = att.get('data', {})
    raw_parties = [
        att_data.get('supplier'),
        att_data.get('issuer'),
        att_data.get('recipient')
    ]

    our_name_norm = _normalize_name(OUR_COMPANY_NAME)
    normalized = set()

    for p in raw_parties:
        norm = _normalize_name(p)
        if norm and norm != our_name_norm:
            normalized.add(norm)

    return normalized


# --- Helper function: Amount match -------------------------------------------------

def _check_amount_match(tx: Transaction, att: Attachment) -> bool:
    """
    Returns "True" if the absolute values of transaction.amount and attachment.total_amount match.

    Transactions may be negative (-50.00) while invoices are positive (50.00). so absolute-value comparison is used.
    """
    try:
        tx_amount = float(tx.get('amount'))
        att_amount = float(att.get('data', {}).get('total_amount'))
    except (TypeError, ValueError, AttributeError):
        return False    # handle missing or non-numeric data (None or "")

    return abs(tx_amount) == abs(att_amount)


# --- Helper function: Date match ---------------------------------------------------

def _check_date_match(tx: Transaction, att: Attachment) -> bool:
    """
    Returns "True" if the transaction date is within a 14 day window of any attachment date: invoicing_date, due_date, or receiving_date.
    The presence of multiple possible attachment dates is common in invoices/receipts.
    """
    try:
        tx_date_str = tx.get('date')
        if not tx_date_str:
            return False

        tx_date = datetime.fromisoformat(tx_date_str).date()
    except Exception:
        return False

    # Create a list of all possible dates to check on the attachment
    att_dates_str = [
        att.get('data', {}).get('invoicing_date'),
        att.get('data', {}).get('due_date'),
        att.get('data', {}).get('receiving_date')
    ]

    for date_str in att_dates_str:
        if not date_str:
            continue    # Skip missing date fields
        try:
            att_date = datetime.fromisoformat(date_str).date()
            # Return True on the first date that falls within the window
            if abs(tx_date - att_date) <= DATE_WINDOW:
                return True
        except Exception:
            continue

    return False


# --- Helper function: Counterparty match ------------------------------------------

def _check_counterparty_match(tx: Transaction, att: Attachment) -> bool:
    """
    Returns "True" if the transaction's 'contact' matches any counterparties on the attachment.
    """
    tx_party_norm = _normalize_name(tx.get('contact'))
    if not tx_party_norm:
        # Transaction has no contact name, so we can't check.
        return False

    att_parties_norm = _get_att_counterparties(att)
    if not att_parties_norm:
        # Attachment has no counterparty names, so we can't check.
        return False

    # Check if the transaction's contact is in the set of attachment counterparties
    if tx_party_norm in att_parties_norm:
        return True

    return False


# --- Core matcher ---------------------------------------------------------

def _find_best_match(
    item_to_match: Union[Transaction, Attachment],
    candidates: List[Union[Transaction, Attachment]],
    is_tx_to_att: bool
) -> Optional[Union[Transaction, Attachment]]:
    """
    The central matching engine used for both directions:
        transaction -> attachment
        attachment -> transaction

    Workflow:
      1. Tries reference number matching.
      2. If that fails, compute a 0–3 heuristic score.
      3. Apply confidence + ambiguity rules.
      4. Return the single best candidate or None.
    """

    # ----------------------------------------------------------------------
    # Stage 1: Reference Number Match (Golden Match)
    # This is the highest-confidence signal. If it matches, we stop and return immediately because it's a guaranteed 1:1 match.
    # ----------------------------------------------------------------------

    # Get the normalized reference from the item being matched.
    if is_tx_to_att:
        # Matching a TX to many ATTs. The item_to_match is a Transaction.
        ref_to_match = _normalize_ref(item_to_match.get('reference'))
    else:
        # Matching an ATTs to many TXs. The item_to_match is an Attachment.
        ref_to_match = _normalize_ref(item_to_match.get('data', {}).get('reference'))

    # Attempt direct 1:1 matching against candidate references.
    if ref_to_match:
        for candidate in candidates:
            if is_tx_to_att:
                # The candidate is an Attachment
                candidate_data = candidate.get('data', {})
                candidate_ref = _normalize_ref(candidate_data.get('reference'))
            else:
                # The candidate is a Transaction
                candidate_ref = _normalize_ref(candidate.get('reference'))

            if candidate_ref and ref_to_match == candidate_ref:
                return candidate    # deterministic golden match

    # ----------------------------------------------------------------------
    # Stage 2: Heuristic scoring
    # No reference match was found. Fall back to scoring based on: Amount, Date, Counterparty.
    # ----------------------------------------------------------------------

    highest_score = 0
    best_candidates = []

    for candidate in candidates:
        # Determine which object is the transaction and which is the attachment.
        if is_tx_to_att:
            tx, att = item_to_match, candidate
        else:
            tx, att = candidate, item_to_match

        # --- Calculate the match signals ---
        amount_match = _check_amount_match(tx, att)
        date_match = _check_date_match(tx, att)
        name_match = _check_counterparty_match(tx, att)

        # --- VETO LOGIC ---
        # This is the critical rule to prevent false positives like Tx 2006.

        # Determine if a meaningful name comparison was possible.
        tx_name_norm = _normalize_name(tx.get('contact'))
        att_names_norm = _get_att_counterparties(att)
        name_check_possible = tx_name_norm is not None and len(att_names_norm) > 0

        # If a name check *was* possible but it *failed*...
        if name_check_possible and not name_match:
            score = 0  # VETO! This is a clear mismatch.
        else:
            # No veto. Proceed with normal scoring.
            score = 0
            if amount_match: score += 1
            if date_match: score += 1
            if name_match: score += 1
        # --- End Veto Logic ---

        # Track best-scoring candidates
        if score > highest_score:
            highest_score = score
            best_candidates = [candidate]  # New highest score, reset list
        elif score == highest_score:
            best_candidates.append(candidate) # Add to list of ties

    # ----------------------------------------------------------------------
    # Stage 3: Return based on Confidence & Ambiguity

    # Only return a match if:
    # 1. The score is confident (2 or 3)
    # 2. There is "exactly one" best candidate (no ambiguity/ties)
    # ----------------------------------------------------------------------

    if highest_score >= CONFIDENCE_THRESHOLD and len(best_candidates) == 1:
        return best_candidates[0]

    return None    # uncertainty -> no match


# --- Public Functions (as required by run.py) -------------------------------------------

def find_attachment(
        transaction: Transaction,
        attachments: List[Attachment]
) -> Optional[Attachment]:
    """
    Finds the single best attachment for a given transaction.

    This is a "wrapper" function that calls the main matching engine.
    """
    return _find_best_match(
        item_to_match=transaction,
        candidates=attachments,
        is_tx_to_att=True  # Set direction: Tx -> Att
    )


def find_transaction(
        attachment: Attachment,
        transactions: List[Transaction]
) -> Optional[Transaction]:
    """
    Finds the single best transaction for a given attachment.

    This is a "wrapper" function that calls the main matching engine.
    """
    return _find_best_match(
        item_to_match=attachment,
        candidates=transactions,
        is_tx_to_att=False  # Set direction: Att -> Tx
    )
