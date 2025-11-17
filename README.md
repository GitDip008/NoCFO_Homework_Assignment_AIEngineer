# NoCFO Homework Assignment - AI Engineer

This repository contains my submission for the AI Engineer homework assignment. The project implements a deterministic, rule-based matching engine to link bank transactions with their corresponding attachments (invoices and receipts).

The matching logic is contained in `src/match.py`.

## 🚀 Instructions to Run

This project uses only the Python 3 standard library. No external dependencies are required.

1.  Clone this repository to your local machine.
```sh
git clone https://github.com/GitDip008/NoCFO_Homework_Assignment_AIEngineer
```
2.  Navigate to the root directory of the project.
```sh
cd NoCFO_Homework_Assignment_AIEngineer
```
3.  Run the application using the following command:
```sh
python run.py
```
The script will execute the matching logic from `src/match.py` and print a test report to the console, showing the success (✅) or failure (❌) of each expected match.

---

## 🧠 Architecture Overview

This system follows a transparent, deterministic, three-stage matching pipeline.

### 1. Reference Number Match (“Golden Match”)

Reference numbers are normalized by:
- removing whitespace  
- removing leading zeros  

If both sides have the same normalized reference number, it's an immediate **1:1 match**.

### 2. Stage 2: Heuristic Scoring

If no reference match is found, the system falls back to a scoring model based on three equally-weighted signals:

* **Amount:** Compares the **absolute value** of the transaction `amount` and attachment `total_amount`. This correctly handles debits (`-50.00`) matching positive invoice totals (`50.00`).
* **Date:** Uses a flexible **14-day window**. It validates the transaction date against *all* relevant attachment dates (`invoicing_date`, `due_date`, `receiving_date`) to account for early payments, late payments, and processing delays.
* **Counterparty:**
    * Names are normalized by converting to lowercase, removing business suffixes (like `Oy`, `Tmi`) and stripping special characters.
    * The logic correctly searches all required attachment fields (`supplier`, `issuer`, `recipient`).
    * It explicitly filters out the company's own name (`Example Company Oy`) to prevent matching against itself, as per the assignment's business logic.


### 3. Stage 3: Confidence, Ambiguity, and the "Veto"

* **Confidence:** A match is only considered "confident" if the score is **2 or higher** (`CONFIDENCE_THRESHOLD`). This fulfills the requirement that "none of the signals alone are sufficient."
* **Ambiguity:** If multiple candidates tie for the highest score, **no match is returned** (`None`). This enforces the "single best candidate" rule and prioritizes accuracy over guessing.
* **The "Veto" Logic (Edge Case Handling):** Important for preventing false positives.
    * **Scenario:** A transaction (like 2006) might match an attachment (like 3005) on `Amount` and `Date`.
    * **Problem:** The transaction's contact ("Matti Mei**tt**iläinen") is a clear mismatch for the attachment's supplier ("Matti Mei**k**äläinen").
    * **Solution:** The code checks if a name comparison was possible. If it was, and the names *did not* match, the score for that candidate is **forced to 0**. This veto prevents the false positive and ensures the system remains accurate.

---

## 🔍 Design Reasoning

- Deterministic behaviour → predictable, auditable outputs.  
- Counterparty normalization removes business suffixes and punctuation noise.  
- Strong **veto rule** prevents incorrect matches even when amount/date align.  
- The core engine is **symmetric**:
  - `find_attachment()` and `find_transaction()` reuse the same logic.  
- Helper functions remain **modular** and **easily testable**.

---

## 📌 Evaluation Criteria Coverage

**Matching Accuracy:**  
Deterministic scoring, strict confidence threshold, counterparty veto for safety.

**Code Clarity:**  
Modular helpers, docstrings, clean logic flow.

**Edge Cases:**  
Handles missing names, messy reference numbers, negative amounts, ambiguous matches.

**Reusability & Design:**  
Pure functions, deterministic behaviour, symmetric design.

**Documentation & Tests:**  
Includes README with logical and architecture explanation.


---
### 🛠 Design & Reusability

The core matching logic is consolidated in the `_find_best_match` function. The required public functions, `find_attachment` and `find_transaction`, are simple, clean wrappers that call this single engine. This makes the code DRY (Don't Repeat Yourself), easy to maintain, and ensures the same robust logic is applied in both directions.
