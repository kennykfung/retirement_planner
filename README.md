Retirement Planner — Offline HTML Report Generator

A small, fully-offline retirement planning tool that reads a plaintext configuration (`retirement_input.txt`) and generates an interactive, self-contained HTML report (`retirement_report.html`).

Features
- Two-phase simulation: accumulation then distribution (year-by-year).
- Tax-aware withdrawals with two-pass AGI calculation (provisional + final).
- RMD enforcement (age 73+), and a strict withdrawal hierarchy: RMD → Taxable → Traditional (bracket-aware) → HSA → Cash → Roth.
- Roth conversion evaluator that considers filling lower brackets (12% / 22%) and reports chosen conversion amounts.
- California state tax (simple model) and IRMAA (Medicare surcharge) using a 2-year MAGI lookback.
- CPI-indexed tax thresholds (baseline 2026) and basic validation checks (NaN/negative/tax integrity).
- Fully offline charts (bundled `chart.min.js` if available).

Usage
1. Edit your inputs in `retirement_input.txt`.
2. Generate the report:

```bash
python3 retirement_planner.py          # uses retirement_input.txt
python3 retirement_planner.py my_plan.txt  # use a custom config filename
```

3. Open `retirement_report.html` in your browser (the script will try to open it automatically).

Notes
- A backup of the script may be saved as `retirement_planner.py.bak` before edits.
- This tool is intended for educational/illustrative planning only; consult a tax professional for real-life decisions.

Files
- `retirement_planner.py` — main generator (embeds JS + template).
- `retirement_input.txt` — editable configuration used as input.
- `retirement_report.html` — generated interactive report.
- `chart.min.js` — optional local Chart.js bundle for fully offline viewing.

If you want additional features (Monte Carlo engine, multi-state support, more precise tax rules), ask and I can add them.
