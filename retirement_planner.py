#!/usr/bin/env python3
"""
================================================================
 Retirement Planner — Offline HTML Report Generator
================================================================
 Usage:
   python retirement_planner.py                    (uses retirement_input.txt)
   python retirement_planner.py my_plan.txt        (uses a custom config file)

 On first run, a default retirement_input.txt is created.
 Edit it with your financial details, then re-run.

 Output: retirement_report.html  (self-contained, fully offline)
================================================================
"""

import configparser
import json
import os
import sys
import webbrowser
from datetime import datetime, date
from pathlib import Path
# No network calls during generation: prefer local bundled assets

CHARTJS_URL   = "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"
CHARTJS_CACHE = "chart.min.js"  # saved alongside this script

# ─────────────────────────────────────────────────────────────
#  DEFAULT CONFIG TEMPLATE
# ─────────────────────────────────────────────────────────────
DEFAULT_CONFIG = """\
# ================================================================
#  RETIREMENT PLANNER — INPUT CONFIGURATION
# ================================================================
#  Edit this file with your financial details, then run:
#      python retirement_planner.py
# ================================================================

[personal]
name                = John
birthday            = 04/15/1965  # MM/DD/YYYY — age is calculated automatically
retirement_age      = 65
life_expectancy     = 90
has_spouse          = true

[spouse]
name                = Jane
birthday            = 06/22/1967  # MM/DD/YYYY
retirement_age      = 63
life_expectancy     = 92

[social_security]
your_monthly_pia       = 2400
your_claiming_age      = 67
spouse_monthly_pia     = 0
spouse_claiming_age    = 67

[income]
your_annual_income           = 0    # Current gross salary/self-employment (for bracket analysis)
spouse_annual_income         = 0    # Spouse current gross income
your_annual_pension         = 0
spouse_annual_pension       = 0
pension_start_age           = 65
other_annual_income         = 0
other_income_stop_age       =

[contributions]
contrib_401k                = max   # "max" or dollar amount
contrib_roth                = max   # Backdoor Roth — "max" or dollar amount
contrib_taxable             = 0     # Additional annual savings to taxable brokerage
spouse_contrib_401k         = 0
spouse_contrib_roth         = 0

[accounts]
trad_401k           = 500000
trad_ira            = 100000
roth_ira            = 80000
roth_401k           = 0
hsa                 = 20000
taxable_brokerage   = 150000
cash_savings        = 50000
home_equity         = 0
mortgage_balance    = 0
other_debt          = 0

[spouse_accounts]
trad_401k           = 0
trad_ira            = 0
roth_ira            = 0
roth_401k           = 0
hsa                 = 0

[goals]
annual_spending     = 80000
spending_reduce_age =
reduced_spending    =
goal_type           = 30

[assumptions]
stock_allocation_pct        = 60
stock_return_pct            = 7.0
bond_return_pct             = 4.0
inflation_rate_pct          = 3.0
filing_status               = MFJ
roth_conversion_strategy    = true

[non-qualified deferred compensation plan]
has_dqnc                    = false
dqnc_balance                = 0
dqnc_annual_deferral        = 0
current_salary              = 0
dqnc_dist_type              = 10yr
dqnc_dist_start_age         = 65

# ================================================================
[tax_brackets]
# ================================================================
# 2026 Federal Income Tax Brackets.
# Format for mfj_brackets / single_brackets:
#   rate_percent | upper_limit_dollars
# Use a very large number (999999999) for the top (unlimited) bracket.
# Edit these values to model future law changes or test scenarios.

mfj_brackets =
    10 | 24800
    12 | 100800
    22 | 211400
    24 | 403550
    32 | 512450
    35 | 768700
    37 | 999999999

single_brackets =
    10 | 12400
    12 | 50400
    22 | 105700
    24 | 201775
    32 | 256225
    35 | 640600
    37 | 999999999

# Standard deduction (2026 est.)
std_ded_mfj    = 31500
std_ded_single = 15750

# Long-term capital gains 0% threshold
ltcg_0_mfj    = 96950
ltcg_0_single = 48475
"""

# ─────────────────────────────────────────────────────────────
#  CONFIG PARSING
# ─────────────────────────────────────────────────────────────
def _calc_age(bday_str: str, default: int = 60) -> int:
    """Calculate current age from a MM/DD/YYYY birthday string."""
    if not bday_str:
        return default
    try:
        parts = bday_str.strip().split('/')
        m, d, y = int(parts[0]), int(parts[1]), int(parts[2])
        born  = date(y, m, d)
        today = date.today()
        age   = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        return max(0, age)
    except Exception:
        return default


def _parse_tax_brackets(config: 'configparser.ConfigParser') -> dict | None:
    """Parse [tax_brackets] section into JS-ready bracket arrays. Returns None if section absent."""
    if not config.has_section('tax_brackets'):
        return None

    def _parse_bracket_lines(raw: str) -> list[dict]:
        rows = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = [p.strip() for p in line.split('|')]
            if len(parts) == 2:
                try:
                    rate = float(parts[0]) / 100.0
                    upper = float(parts[1])
                    rows.append({'r': rate, 'to': upper})
                except ValueError:
                    pass
        return rows or None

    def _get(key, default=''):
        try:
            return config.get('tax_brackets', key).strip()
        except Exception:
            return default

    def _getf(key, default=0.0):
        raw = _get(key, '')
        try:
            return float(raw) if raw else float(default)
        except ValueError:
            return float(default)

    mfj_raw    = _get('mfj_brackets', '')
    single_raw = _get('single_brackets', '')
    mfj    = _parse_bracket_lines(mfj_raw)
    single = _parse_bracket_lines(single_raw)

    if not mfj and not single:
        return None

    return {
        'mfj':            mfj,
        'single':         single,
        'std_ded_mfj':    _getf('std_ded_mfj',    31500),
        'std_ded_single': _getf('std_ded_single',  15750),
        'ltcg_0_mfj':     _getf('ltcg_0_mfj',     96950),
        'ltcg_0_single':  _getf('ltcg_0_single',   48475),
    }


def parse_config(config_path: Path) -> dict:
    """Read retirement_input.txt and return a dict of all inputs."""
    config = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
    config.read(config_path, encoding='utf-8')

    def get(section, key, default=''):
        try:
            return config.get(section, key).strip()
        except (configparser.NoSectionError, configparser.NoOptionError):
            return str(default)

    def getf(section, key, default=0.0):
        raw = get(section, key, '')
        try:
            return float(raw) if raw else float(default)
        except ValueError:
            return float(default)

    def geti(section, key, default=0):
        return int(getf(section, key, default))

    def getb(section, key, default=False):
        raw = get(section, key, '').lower()
        return raw in ('true', 'yes', '1', 'on') if raw else default

    def get_contrib(section, key, default='0'):
        """Return contribution field as string ('max') or numeric string."""
        raw = get(section, key, default).lower().strip()
        if raw == 'max':
            return 'max'
        try:
            return str(float(raw))
        except ValueError:
            return '0'

    stop_age_raw = get('income', 'other_income_stop_age', '')
    other_stop   = int(float(stop_age_raw)) if stop_age_raw else 999

    sra_raw = get('goals', 'spending_reduce_age', '')
    sra     = int(float(sra_raw)) if sra_raw else 0
    rs_raw  = get('goals', 'reduced_spending', '')
    rs      = float(rs_raw) if rs_raw else 0.0

    bday1 = get('personal', 'birthday', '04/15/1965')
    bday2 = get('spouse',   'birthday', '06/22/1967')

    return {
        # Personal
        'name1':            get('personal', 'name', 'You'),
        'birthday1':        bday1,
        'age1':             _calc_age(bday1, default=60),
        'retAge1':          geti('personal', 'retirement_age', 65),
        'lifeExp1':         geti('personal', 'life_expectancy', 90),
        'hasSpouse':        getb('personal', 'has_spouse', False),
        # Spouse
        'name2':            get('spouse', 'name', 'Spouse'),
        'birthday2':        bday2,
        'age2':             _calc_age(bday2, default=58),
        'retAge2':          geti('spouse', 'retirement_age', 63),
        'lifeExp2':         geti('spouse', 'life_expectancy', 92),
        # Social Security
        'pia1':             getf('social_security', 'your_monthly_pia', 0),
        'ssAge1':           geti('social_security', 'your_claiming_age', 67),
        'pia2':             getf('social_security', 'spouse_monthly_pia', 0),
        'ssAge2':           geti('social_security', 'spouse_claiming_age', 67),
        # Current income (for bracket analysis)
        'yourIncome':       getf('income', 'your_annual_income', 0),
        'spouseIncome':     getf('income', 'spouse_annual_income', 0),
        # Income
        'pension1':         getf('income', 'your_annual_pension', 0),
        'pension2':         getf('income', 'spouse_annual_pension', 0),
        'pensionAge':       geti('income', 'pension_start_age', 65),
        'otherIncome':      getf('income', 'other_annual_income', 0),
        'otherIncomeStopAge': other_stop,
        # Pre-retirement contributions
        'contrib401k':      get_contrib('contributions', 'contrib_401k', 'max'),
        'contribRoth':      get_contrib('contributions', 'contrib_roth', 'max'),
        'contribTaxable':   getf('contributions', 'contrib_taxable', 0),
        'spouseContrib401k': get_contrib('contributions', 'spouse_contrib_401k', '0'),
        'spouseContribRoth': get_contrib('contributions', 'spouse_contrib_roth', '0'),
        # Accounts — yours
        'trad401k':         getf('accounts', 'trad_401k', 0),
        'tradIRA':          getf('accounts', 'trad_ira', 0),
        'roth401k':         getf('accounts', 'roth_401k', 0),
        'rothIRA':          getf('accounts', 'roth_ira', 0),
        'hsa':              getf('accounts', 'hsa', 0),
        'taxable':          getf('accounts', 'taxable_brokerage', 0),
        'cash':             getf('accounts', 'cash_savings', 0),
        'homeEquity':       getf('accounts', 'home_equity', 0),
        'mortgage':         getf('accounts', 'mortgage_balance', 0),
        'otherDebt':        getf('accounts', 'other_debt', 0),
        # Accounts — spouse
        'spouse401k':       getf('spouse_accounts', 'trad_401k', 0),
        'spouseTradIRA':    getf('spouse_accounts', 'trad_ira', 0),
        'spouseRothIRA':    getf('spouse_accounts', 'roth_ira', 0),
        'spouseRoth401k':   getf('spouse_accounts', 'roth_401k', 0),
        'spouseHSA':        getf('spouse_accounts', 'hsa', 0),
        # Goals
        'annualSpending':   getf('goals', 'annual_spending', 80000),
        'spendingReduceAge': sra,
        'reducedSpending':  rs,
        'goalType':         get('goals', 'goal_type', '30'),
        # Assumptions
        'stockAlloc':       geti('assumptions', 'stock_allocation_pct', 60),
        'stockReturn':      getf('assumptions', 'stock_return_pct', 7.0),
        'bondReturn':       getf('assumptions', 'bond_return_pct', 4.0),
        'inflationRate':    getf('assumptions', 'inflation_rate_pct', 3.0),
        'filingStatus':     get('assumptions', 'filing_status', 'MFJ'),
        'retirementState':  get('assumptions', 'retirement_state', 'CA').strip().upper(),
        'rothConversion':   getb('assumptions', 'roth_conversion_strategy', True),
        # NQDC plan
        'hasNqdc':       getb('non-qualified deferred compensation plan', 'has_dqnc', False),
        'nqdcBalance':   getf('non-qualified deferred compensation plan', 'dqnc_balance', 0),
        'nqdcDeferral':  getf('non-qualified deferred compensation plan', 'dqnc_annual_deferral', 0),
        'currentSalary':    getf('non-qualified deferred compensation plan', 'current_salary', 0),
        'nqdcDistType':  get('non-qualified deferred compensation plan', 'dqnc_dist_type', '10yr'),
        'nqdcStartAge':  geti('non-qualified deferred compensation plan', 'dqnc_dist_start_age', 65),
        # Tax brackets
        'tax_brackets':  _parse_tax_brackets(config),
    }


# ─────────────────────────────────────────────────────────────
#  CHART.JS (offline cache)
# ─────────────────────────────────────────────────────────────
def get_chartjs(script_dir: Path) -> str | None:
    """Return Chart.js content from local cache, download if needed."""
    cache = script_dir / CHARTJS_CACHE
    if cache.exists():
        print(f"  📦 Chart.js loaded from cache ({cache.name})")
        return cache.read_text(encoding='utf-8')
    # Do not attempt to download automatically to preserve fully-offline generation.
    print("  ⚠️  Chart.js cache not found (chart.min.js). Using CDN fallback in generated HTML.")
    return None


# ─────────────────────────────────────────────────────────────
#  HTML TEMPLATE
# ─────────────────────────────────────────────────────────────
# Markers replaced at generation time:
#   %%CHARTJS%%   → embedded <script> with Chart.js (or CDN fallback)
#   %%CONFIG%%    → <script id="rc"> with JSON data from .txt file
#   %%CFGFILE%%   → filename of the config used
#   %%GENERATED%% → generation timestamp

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Retirement Planner</title>
%%CHARTJS%%
%%CONFIG%%
<style>
:root{--p:#0d9488;--pd:#0f766e;--pl:#ccfbf1;--acc:#f59e0b;--dan:#ef4444;--ok:#22c55e;--tx:#1f2937;--tl:#6b7280;--bd:#e5e7eb;--bg:#f9fafb;}
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#d1d5db;color:var(--tx);min-height:100vh;}
.page-wrap{max-width:1440px;margin:0 auto;background:var(--bg);box-shadow:0 0 40px rgba(0,0,0,.15);min-height:100vh;}
/* ── Header ── */
.header{background:linear-gradient(135deg,#134e4a,var(--p));color:#fff;padding:1.6rem 2rem;text-align:center;}
.header h1{font-size:1.9rem;font-weight:700;margin-bottom:.3rem;}
.header p{opacity:.85;font-size:.9rem;}
.gen-banner{background:rgba(0,0,0,.25);border-radius:6px;padding:.35rem .75rem;font-size:.75rem;display:inline-block;margin-top:.5rem;letter-spacing:.03em;}
/* ── Tab Nav ── */
.tab-nav{background:#fff;border-bottom:2px solid var(--bd);display:flex;overflow-x:auto;padding:0 1rem;position:sticky;top:0;z-index:100;box-shadow:0 2px 6px rgba(0,0,0,.07);}
.tab-btn{padding:.85rem 1.3rem;border:none;background:none;cursor:pointer;font-size:.88rem;color:var(--tl);border-bottom:3px solid transparent;white-space:nowrap;transition:all .2s;font-weight:500;}
.tab-btn:hover{color:var(--p);}
.tab-btn.active{color:var(--p);border-bottom-color:var(--p);font-weight:600;}
/* ── Content ── */
.content{padding:1.6rem 2rem;}
.tab-panel{display:none;}.tab-panel.active{display:block;}
/* ── Two-column inputs layout ── */
.two-col{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:1.5rem;align-items:start;}
@media(max-width:960px){.two-col{grid-template-columns:1fr;}}
.section-label{font-size:1rem;font-weight:700;color:var(--pd);margin:1.2rem 0 .6rem;padding:.45rem .8rem;background:var(--pl);border-radius:8px;border-left:4px solid var(--p);}
/* ── Card ── */
.card{background:#fff;border-radius:12px;padding:1.35rem;margin-bottom:1.2rem;box-shadow:0 1px 3px rgba(0,0,0,.08);border:1px solid var(--bd);}
.card h2{font-size:.98rem;font-weight:600;color:var(--pd);margin-bottom:.9rem;padding-bottom:.45rem;border-bottom:2px solid var(--pl);display:flex;align-items:center;gap:.4rem;}
/* ── Grid ── */
.fg{display:grid;grid-template-columns:1fr 1fr;gap:.9rem;}
.fg3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:.9rem;}
.ff{grid-column:1/-1;}
@media(max-width:580px){.fg,.fg3{grid-template-columns:1fr;}}
/* ── Form ── */
.fm{display:flex;flex-direction:column;gap:.3rem;}
.fm label{font-size:.82rem;font-weight:500;}
.fm input,.fm select{padding:.55rem .7rem;border:1.5px solid var(--bd);border-radius:8px;font-size:.92rem;color:var(--tx);background:#fff;transition:border-color .2s;}
.fm input:focus,.fm select:focus{outline:none;border-color:var(--p);box-shadow:0 0 0 3px rgba(13,148,136,.12);}
.fm .hint{font-size:.74rem;color:var(--tl);}
.pfx{position:relative;}.pfx span{position:absolute;left:.7rem;top:50%;transform:translateY(-50%);color:var(--tl);font-size:.88rem;}
.pfx input{padding-left:1.5rem;}
/* ── Toggle ── */
.trow{display:flex;align-items:center;gap:.6rem;margin-bottom:.9rem;padding:.65rem;background:#f0fdfa;border-radius:8px;border:1px solid #99f6e4;}
.trow input[type=checkbox]{width:1.05rem;height:1.05rem;accent-color:var(--p);cursor:pointer;}
.trow label{font-weight:500;cursor:pointer;font-size:.88rem;color:#134e4a;}
/* ── Presets ── */
.presets{display:flex;gap:.45rem;flex-wrap:wrap;margin-bottom:.7rem;}
.pb{padding:.32rem .8rem;border:1.5px solid var(--bd);border-radius:20px;background:#fff;cursor:pointer;font-size:.8rem;font-weight:500;transition:all .2s;}
.pb:hover{border-color:var(--p);color:var(--p);}
.pb.active{background:var(--p);color:#fff;border-color:var(--p);}
/* ── Slider ── */
.srow{display:flex;align-items:center;gap:.9rem;}
.srow input[type=range]{flex:1;accent-color:var(--p);cursor:pointer;}
.sv{min-width:7rem;text-align:right;font-weight:600;color:var(--p);font-size:.95rem;}
/* ── Buttons ── */
.calc-btn{display:block;width:100%;padding:.9rem;background:var(--p);color:#fff;border:none;border-radius:10px;font-size:1.05rem;font-weight:600;cursor:pointer;transition:background .2s;margin-bottom:1.4rem;}
.calc-btn:hover{background:var(--pd);}
.nav-btns{display:flex;justify-content:space-between;margin-top:.9rem;}
.nb{padding:.5rem 1.3rem;border:1.5px solid var(--p);border-radius:8px;background:#fff;color:var(--p);cursor:pointer;font-size:.88rem;font-weight:500;transition:all .2s;}
.nb:hover,.nb.pri{background:var(--p);color:#fff;}
/* ── Metrics ── */
.mgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:.9rem;margin-bottom:1.3rem;}
@media(max-width:580px){.mgrid{grid-template-columns:1fr;}}
.mc{background:#fff;border-radius:12px;padding:1.15rem;text-align:center;border:1px solid var(--bd);box-shadow:0 1px 3px rgba(0,0,0,.06);}
.mc .ml{font-size:.72rem;color:var(--tl);margin-bottom:.25rem;text-transform:uppercase;letter-spacing:.05em;}
.mc .mv{font-size:1.65rem;font-weight:700;line-height:1.1;}
.mc .ms{font-size:.75rem;color:var(--tl);margin-top:.25rem;}
.mc.green .mv{color:var(--ok);}.mc.yellow .mv{color:var(--acc);}.mc.red .mv{color:var(--dan);}.mc.teal .mv{color:var(--p);}
/* ── Chart ── */
.ch{position:relative;height:290px;}
/* ── Strategy ── */
.strat{background:linear-gradient(135deg,#f0fdfa,#e6fffa);border:1px solid #5eead4;border-radius:10px;padding:1.15rem;margin-bottom:.9rem;}
.strat h3{color:var(--pd);margin-bottom:.65rem;font-size:.92rem;font-weight:600;}
.si{display:flex;align-items:flex-start;gap:.55rem;margin-bottom:.5rem;}
.si .ic{font-size:1.05rem;flex-shrink:0;line-height:1.45;}
.si p{font-size:.85rem;color:#134e4a;line-height:1.5;}
.si strong{color:var(--pd);}
/* ── Alert ── */
.al{padding:.7rem .95rem;border-radius:8px;margin-bottom:.9rem;font-size:.85rem;}
.al.info{background:#dbeafe;color:#1e40af;border:1px solid #bfdbfe;}
.al.warn{background:#fef3c7;color:#92400e;border:1px solid #fde68a;}
.al.err{background:#fee2e2;color:#991b1b;border:1px solid #fecaca;}
.al.ok{background:#dcfce7;color:#166534;border:1px solid #bbf7d0;}
/* ── Table ── */
.tw{overflow-x:auto;max-height:380px;overflow-y:auto;border-radius:8px;border:1px solid var(--bd);}
table{width:100%;border-collapse:collapse;font-size:.8rem;}
thead th{background:#f1f5f9;padding:.55rem .65rem;text-align:right;font-weight:600;color:var(--tl);font-size:.72rem;text-transform:uppercase;border-bottom:2px solid var(--bd);position:sticky;top:0;z-index:1;}
thead th:first-child{text-align:left;}
td{padding:.47rem .65rem;text-align:right;border-bottom:1px solid #f1f5f9;}
td:first-child{text-align:left;font-weight:500;}
tr:hover td{background:#f9fafb;}
tr.dep td{background:#fee2e2;}
tr.low td{background:#fef3c7;}
tr.hl td{background:#f0fdfa;}
/* ── Withdrawal chips ── */
.worder{display:flex;gap:.35rem;flex-wrap:wrap;align-items:center;margin:.45rem 0;}
.woi{display:flex;align-items:center;gap:.25rem;background:#fff;border:1.5px solid;border-radius:6px;padding:.27rem .55rem;font-size:.76rem;font-weight:500;}
.woi.tx{border-color:#60a5fa;color:#1d4ed8;}.woi.tr{border-color:#f59e0b;color:#92400e;}.woi.ro{border-color:#34d399;color:#065f46;}
.warr{color:var(--tl);}
/* ── SS preview ── */
.ssp{background:#f0fdfa;border-radius:8px;padding:.7rem;margin-top:.45rem;font-size:.83rem;}
.ssr{display:flex;justify-content:space-between;margin-bottom:.22rem;}
.ssr:last-child{margin-bottom:0;font-weight:600;color:var(--pd);border-top:1px solid #99f6e4;padding-top:.22rem;margin-top:.22rem;}
/* ── Misc ── */
.divider{height:1px;background:var(--bd);margin:.85rem 0;}
.slabel{font-size:.72rem;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--tl);margin-bottom:.65rem;margin-top:.2rem;}
.hidden{display:none!important;}
.cb-btn{background:none;border:1px solid var(--bd);border-radius:6px;padding:.38rem .85rem;font-size:.8rem;cursor:pointer;color:var(--tl);margin-bottom:.7rem;}
.cb-btn:hover{background:var(--bg);}
.cfg-note{background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:.65rem 1rem;font-size:.8rem;color:#92400e;margin-bottom:1.2rem;}
</style>
</head>
<body>
<div class="page-wrap">

<div class="header">
  <h1>🌿 Retirement Planner</h1>
  <p>Social Security optimization · Tax-efficient withdrawal sequencing · RMD management</p>
  <div class="gen-banner">Generated from <strong>%%CFGFILE%%</strong> on %%GENERATED%%</div>
</div>

<div class="tab-nav">
  <button class="tab-btn active" onclick="sw('inputs')">① Inputs</button>
  <button class="tab-btn" onclick="sw('results')">② Results</button>
</div>

<!-- ══════════════ TAB 1 — INPUTS ══════════════ -->
<div id="tab-inputs" class="tab-panel active">
  <div class="cfg-note">
    ✏️ Values are pre-loaded from <strong>%%CFGFILE%%</strong>. Adjust any field and click <strong>Calculate</strong> to update results.
    To make permanent changes, edit the .txt file and re-run <code>python retirement_planner.py</code>.
  </div>
  <div class="two-col">

    <!-- ═══ LEFT COLUMN: Personal + Income ═══ -->
    <div>
      <div class="section-label">👤 Personal</div>
      <div class="card">
        <h2>👤 Primary Person</h2>
        <div class="fg">
          <div class="fm"><label>Your Name</label><input id="name1"></div>
          <div class="fm"><label>Your Birthday</label>
            <input type="text" id="bday1" placeholder="MM/DD/YYYY" maxlength="10"
                   oninput="fmtBday(this);updAge('bday1','agehint1')">
            <span class="hint" id="agehint1">Enter birthday — age calculated automatically</span></div>
          <div class="fm"><label>Planned Retirement Age</label><input type="number" id="retAge1" min="50" max="80"></div>
          <div class="fm"><label>Life Expectancy</label><input type="number" id="lifeExp1" min="70" max="110">
            <span class="hint">Plan conservatively — 90–95 is recommended.</span></div>
        </div>
      </div>
      <div class="card">
        <div class="trow">
          <input type="checkbox" id="hasSpouse" onchange="toggleSpouse()">
          <label for="hasSpouse">Include a Spouse / Partner</label>
        </div>
        <div id="spouse-section" class="hidden">
          <h2>👤 Spouse / Partner</h2>
          <div class="fg">
            <div class="fm"><label>Spouse's Name</label><input id="name2"></div>
            <div class="fm"><label>Spouse's Birthday</label>
              <input type="text" id="bday2" placeholder="MM/DD/YYYY" maxlength="10"
                     oninput="fmtBday(this);updAge('bday2','agehint2')">
              <span class="hint" id="agehint2">Enter birthday — age calculated automatically</span></div>
            <div class="fm"><label>Spouse's Retirement Age</label><input type="number" id="retAge2" min="50" max="80"></div>
            <div class="fm"><label>Spouse's Life Expectancy</label><input type="number" id="lifeExp2" min="70" max="110"></div>
          </div>
        </div>
      </div>

      <div class="section-label">💰 Income</div>
      <div class="card">
        <h2>💼 Current Household Income</h2>
        <div class="al info">💡 Enter current gross income. This determines your <strong>working tax bracket</strong> for Roth conversion timing guidance.</div>
        <div class="fg">
          <div class="fm"><label>Your Current Annual Income</label>
            <div class="pfx"><span>$</span><input type="number" id="yourIncome" min="0" oninput="updateIncomeHint()"></div>
            <span class="hint">Gross salary, self-employment, or other earned income.</span></div>
          <div class="fm" id="spouse-income-grp"><label>Spouse's Current Annual Income</label>
            <div class="pfx"><span>$</span><input type="number" id="spouseIncome" min="0" oninput="updateIncomeHint()"></div>
            <span class="hint">Enter 0 if not working.</span></div>
        </div>
        <div id="income-bracket-hint" class="ssp" style="margin-top:.55rem;"></div>
      </div>
      <div class="card">
        <h2>🏛️ Social Security — <span id="ss1lbl">You</span></h2>
        <div class="al info">💡 Get your estimate at <strong>ssa.gov/myaccount</strong>. Enter the <em>monthly amount at FRA (age 67 if born 1960+)</em>.</div>
        <div class="fg">
          <div class="fm"><label>Monthly Benefit at FRA (your PIA)</label>
            <div class="pfx"><span>$</span><input type="number" id="pia1" min="0"></div>
            <span class="hint">Enter 0 if no SS record.</span></div>
          <div class="fm"><label>Planned Claiming Age</label>
            <select id="ssAge1" onchange="updateSS()">
              <option value="62">62 — Early (reduced)</option><option value="63">63</option><option value="64">64</option>
              <option value="65">65</option><option value="66">66</option><option value="67">67 — FRA</option>
              <option value="68">68 — Delayed (+8%)</option><option value="69">69 — Delayed (+16%)</option>
              <option value="70">70 — Maximum (+24%)</option>
            </select>
            <span class="hint">Delay to 70 = +8%/yr above FRA.</span></div>
        </div>
        <div id="ssp1" class="ssp"></div>
      </div>
      <div id="ss2-card" class="card hidden">
        <h2>🏛️ Social Security — <span id="ss2lbl">Spouse</span></h2>
        <div class="fg">
          <div class="fm"><label>Spouse Monthly Benefit at FRA (PIA)</label>
            <div class="pfx"><span>$</span><input type="number" id="pia2" min="0"></div>
            <span class="hint">Enter 0 if spouse relies on spousal benefit only.</span></div>
          <div class="fm"><label>Spouse Claiming Age</label>
            <select id="ssAge2" onchange="updateSS()">
              <option value="62">62 — Early</option><option value="63">63</option><option value="64">64</option>
              <option value="65">65</option><option value="66">66</option><option value="67">67 — FRA</option>
              <option value="68">68</option><option value="69">69</option><option value="70">70 — Max</option>
            </select></div>
        </div>
        <div id="ssp2" class="ssp"></div>
        <div class="al info" style="margin-top:.7rem;">💡 <strong>Spousal Benefit:</strong> If spouse's own benefit &lt; 50% of your PIA, the higher spousal amount applies automatically.</div>
      </div>
      <div class="card">
        <h2>💰 Other Income</h2>
        <div class="slabel">Pension</div>
        <div class="fg3">
          <div class="fm"><label>Your Annual Pension</label><div class="pfx"><span>$</span><input type="number" id="pension1" min="0"></div></div>
          <div class="fm" id="pension2g"><label>Spouse Annual Pension</label><div class="pfx"><span>$</span><input type="number" id="pension2" min="0"></div></div>
          <div class="fm"><label>Pension starts at age</label><input type="number" id="pensionAge" min="50" max="80"></div>
        </div>
        <div class="divider"></div>
        <div class="slabel">Part-Time / Rental / Other</div>
        <div class="fg">
          <div class="fm"><label>Annual Other Income</label><div class="pfx"><span>$</span><input type="number" id="otherIncome" min="0"></div></div>
          <div class="fm"><label>Stops at age (blank = forever)</label><input type="number" id="otherIncomeStopAge" min="60" max="100"></div>
        </div>
      </div>
      <div class="section-label">🏦 Accounts</div>
      <div class="al info">💡 Enter <strong>current balances today</strong>. Growth through retirement is modeled automatically.</div>
      <div class="card">
        <h2>📊 Tax-Deferred — Pre-Tax (401k / IRA)</h2>
        <p style="font-size:.8rem;color:var(--tl);margin-bottom:.85rem;">Withdrawals taxed as ordinary income. <strong>RMDs required starting age 73.</strong></p>
        <div class="fg">
          <div class="fm"><label>Traditional 401(k) / 403(b)</label><div class="pfx"><span>$</span><input type="number" id="trad401k" min="0"></div></div>
          <div class="fm"><label>Traditional IRA / SEP-IRA</label><div class="pfx"><span>$</span><input type="number" id="tradIRA" min="0"></div></div>
        </div>
        <div id="sp-trad" class="hidden" style="margin-top:.7rem;">
          <div class="divider"></div><div class="slabel">Spouse's Tax-Deferred</div>
          <div class="fg">
            <div class="fm"><label>Spouse 401(k)</label><div class="pfx"><span>$</span><input type="number" id="spouse401k" min="0"></div></div>
            <div class="fm"><label>Spouse Trad IRA</label><div class="pfx"><span>$</span><input type="number" id="spouseTradIRA" min="0"></div></div>
          </div>
        </div>
      </div>
      <div class="card">
        <h2>🌱 Tax-Free — Roth</h2>
        <p style="font-size:.8rem;color:var(--tl);margin-bottom:.85rem;">Qualified withdrawals <strong>completely tax-free</strong>. No RMDs. Best to draw last.</p>
        <div class="fg">
          <div class="fm"><label>Roth IRA</label><div class="pfx"><span>$</span><input type="number" id="rothIRA" min="0"></div></div>
          <div class="fm"><label>Roth 401(k)</label><div class="pfx"><span>$</span><input type="number" id="roth401k" min="0"></div></div>
        </div>
        <div id="sp-roth" class="hidden" style="margin-top:.7rem;">
          <div class="divider"></div><div class="slabel">Spouse's Roth</div>
          <div class="fg">
            <div class="fm"><label>Spouse Roth IRA</label><div class="pfx"><span>$</span><input type="number" id="spouseRothIRA" min="0"></div></div>
            <div class="fm"><label>Spouse Roth 401(k)</label><div class="pfx"><span>$</span><input type="number" id="spouseRoth401k" min="0"></div></div>
          </div>
        </div>
      </div>
      <div class="card">
        <h2>🏥 HSA &amp; Taxable</h2>
        <div class="fg3">
          <div class="fm"><label>Your HSA</label><div class="pfx"><span>$</span><input type="number" id="hsa" min="0"></div>
            <span class="hint">Triple tax advantage for medical.</span></div>
          <div class="fm" id="sp-hsa"><label>Spouse HSA</label><div class="pfx"><span>$</span><input type="number" id="spouseHSA" min="0"></div></div>
          <div class="fm"><label>Taxable Brokerage</label><div class="pfx"><span>$</span><input type="number" id="taxable" min="0"></div></div>
        </div>
        <div class="fg3" style="margin-top:.7rem;">
          <div class="fm"><label>Cash / Savings / CDs</label><div class="pfx"><span>$</span><input type="number" id="cash" min="0"></div></div>
          <div class="fm"><label>Home Equity (net worth only)</label><div class="pfx"><span>$</span><input type="number" id="homeEquity" min="0"></div></div>
        </div>
      </div>
      <div class="card">
        <h2>💳 Debts</h2>
        <div class="fg">
          <div class="fm"><label>Mortgage Balance</label><div class="pfx"><span>$</span><input type="number" id="mortgage" min="0"></div></div>
          <div class="fm"><label>Other Debts</label><div class="pfx"><span>$</span><input type="number" id="otherDebt" min="0"></div></div>
        </div>
      </div>
    </div><!-- end left column -->

    <!-- ═══ RIGHT COLUMN: Contributions + NQDC + Goals + Tax Brackets ═══ -->
    <div>
      <div class="section-label">📥 Pre-Retirement Contributions &amp; Planning</div>
      <div class="card" style="border:2px solid #10b981;">
        <h2 style="color:#047857;">📥 Pre-Retirement Contributions</h2>
        <div class="al" style="background:#d1fae5;color:#065f46;border:1px solid #6ee7b7;margin-bottom:.9rem;">
          Contributions are modeled from <strong>today until retirement age</strong>. Use <em>"max"</em> or a dollar amount.
          <br>2025 IRS limits: 401(k) $23,500 (+$7,500 catch-up 50-59/64+; +$11,250 super catch-up 60-63) · IRA/Roth $7,000 (+$1,000 catch-up 50+).
        </div>
        <div class="fg">
          <div class="fm"><label>Your 401(k) Annual Contribution</label>
            <input type="text" id="contrib401k" placeholder="max or dollar amount">
            <span class="hint" id="c401k-hint"></span></div>
          <div class="fm"><label>Your Roth IRA / Backdoor Roth</label>
            <input type="text" id="contribRoth" placeholder="max or dollar amount">
            <span class="hint" id="cRoth-hint">High earners: use Backdoor Roth (no income limit).</span></div>
        </div>
        <div class="fg" style="margin-top:.65rem;">
          <div class="fm"><label>Additional Taxable Savings / Year</label>
            <div class="pfx"><span>$</span><input type="number" id="contribTaxable" min="0" value="0"></div>
            <span class="hint">Savings to brokerage after maxing tax-advantaged accounts.</span></div>
        </div>
        <div id="sp-contrib" class="hidden" style="margin-top:.65rem;">
          <div class="divider"></div><div class="slabel">Spouse Contributions</div>
          <div class="fg">
            <div class="fm"><label>Spouse 401(k) Contribution</label>
              <input type="text" id="spouseContrib401k" placeholder="max or 0"></div>
            <div class="fm"><label>Spouse Roth IRA</label>
              <input type="text" id="spouseContribRoth" placeholder="max or 0"></div>
          </div>
        </div>
        <div id="accum-preview" class="ssp" style="margin-top:.75rem;"></div>
      </div>
      <div class="card" style="border:2px solid #6366f1;">
        <h2 style="color:#4f46e5;">🏢 Non-Qualified Deferred Compensation (NQDC)</h2>
        <div class="al" style="background:#ede9fe;color:#3730a3;border:1px solid #c4b5fd;margin-bottom:.9rem;">
          A <strong>Non-Qualified Deferred Compensation (NQDC)</strong> plan lets you defer a portion of salary or bonus before taxes, with distributions paid out in retirement. Unlike 401(k)s, NQDC has no IRS contribution cap — but balances are an <em>unsecured liability</em> of your employer.
        </div>
        <div class="trow" style="border-color:#a5b4fc;background:#eef2ff;">
          <input type="checkbox" id="hasNqdc" onchange="toggleNqdc()">
          <label for="hasNqdc" style="color:#3730a3;">I have a Non-Qualified Deferred Compensation (NQDC) plan</label>
        </div>
        <div id="nqdc-section" class="hidden">
          <div class="fg3">
            <div class="fm"><label>Current NQDC Balance</label>
              <div class="pfx"><span>$</span><input type="number" id="nqdcBalance" min="0" value="0"></div>
              <span class="hint">Total accumulated deferred balance today.</span></div>
            <div class="fm"><label>Annual Deferral Amount</label>
              <div class="pfx"><span>$</span><input type="number" id="nqdcDeferral" min="0" value="0"></div>
              <span class="hint">How much you defer per year (salary + bonus).</span></div>
            <div class="fm"><label>Current Annual Salary</label>
              <div class="pfx"><span>$</span><input type="number" id="currentSalary" min="0" value="0"></div>
              <span class="hint">Used to estimate any employer match on excess compensation.</span></div>
          </div>
          <div class="fg" style="margin-top:.7rem;">
            <div class="fm"><label>Distribution Schedule</label>
              <select id="nqdcDistType">
                <option value="lump">Lump Sum at retirement</option>
                <option value="5yr">5-Year installments</option>
                <option value="10yr" selected>10-Year installments</option>
              </select>
              <span class="hint">Longer installments = more bracket control.</span></div>
            <div class="fm"><label>Distribution Start Age</label>
              <input type="number" id="nqdcStartAge" min="50" max="80" value="65">
              <span class="hint">Usually equals your planned retirement age.</span></div>
          </div>
        </div>
      </div>

      <div class="section-label">🎯 Goals &amp; Assumptions</div>
      <div class="card">
        <h2>🎯 Spending Goal</h2>
        <div class="fg">
          <div class="fm"><label>Annual Retirement Spending (today's $)</label>
            <div class="pfx"><span>$</span><input type="number" id="annualSpending" min="0"></div>
            <span class="hint">Total household spend: taxes, travel, healthcare, etc.</span></div>
          <div class="fm"><label>Reduce Spending After Age (optional)</label>
            <input type="number" id="spendingReduceAge" min="65" max="100">
            <span class="hint">The "slow-go" phase — many spend ~20% less after 80.</span></div>
        </div>
        <div class="fg" style="margin-top:.7rem;">
          <div class="fm"><label>Reduced Amount (today's $)</label>
            <div class="pfx"><span>$</span><input type="number" id="reducedSpending" min="0"></div></div>
        </div>
      </div>
      <div class="card">
        <h2>🏁 Portfolio Duration Goal</h2>
        <div class="fm ff"><label>Goal Type</label>
          <select id="goalType" onchange="updateSWR()">
            <option value="30">30 years — 4.0% safe withdrawal rate</option>
            <option value="35">35 years — 3.7% safe withdrawal rate</option>
            <option value="40">40 years — 3.3% safe withdrawal rate</option>
            <option value="perpetual">Perpetual — preserve principal (3.0%)</option>
            <option value="custom">Custom duration</option>
          </select>
        </div>
        <div id="custom-yrs" class="hidden" style="margin-top:.7rem;">
          <div class="fm"><label>Custom Duration (years)</label><input type="number" id="customGoalYears" min="10" max="70" value="25"></div>
        </div>
        <div id="swr-disp" class="ssp" style="margin-top:.7rem;"></div>
      </div>
      <div class="card">
        <h2>📐 Asset Allocation</h2>
        <div class="presets">
          <button class="pb" onclick="setPreset(30)">Conservative 30/70</button>
          <button class="pb" onclick="setPreset(60)">Moderate 60/40</button>
          <button class="pb" onclick="setPreset(80)">Growth 80/20</button>
          <button class="pb" onclick="setPreset(100)">All-Stocks</button>
        </div>
        <div class="srow">
          <span style="font-size:.8rem;color:var(--tl);">Bonds</span>
          <input type="range" id="stockPct" min="0" max="100" step="5" oninput="updAlloc()">
          <span style="font-size:.8rem;color:var(--tl);">Stocks</span>
          <span class="sv" id="allocDisp"></span>
        </div>
      </div>
      <div class="card">
        <h2>📈 Return Assumptions</h2>
        <div class="al info">Nominal (before inflation) returns. Spending inflates each year. Stocks historical avg ~10%, conservative ~7%.</div>
        <div class="fg3">
          <div class="fm"><label>Stock Return %</label><div class="pfx"><span>%</span><input type="number" id="stockReturn" step="0.1" min="0" max="20"></div></div>
          <div class="fm"><label>Bond Return %</label><div class="pfx"><span>%</span><input type="number" id="bondReturn" step="0.1" min="0" max="15"></div></div>
          <div class="fm"><label>Inflation Rate %</label><div class="pfx"><span>%</span><input type="number" id="inflationRate" step="0.1" min="0" max="10"></div></div>
        </div>
        <div class="fg3" style="margin-top:.7rem;">
          <div class="fm"><label>Tax Filing Status</label>
            <select id="filingStatus">
              <option value="MFJ">Married Filing Jointly (MFJ)</option>
              <option value="Single">Single / Head of Household</option>
            </select>
            <span class="hint">Determines tax brackets and standard deduction.</span>
          </div>
          <div class="fm"><label>Retirement State</label>
            <select id="retirementState">
              <option value="CA">California (CA)</option>
              <option value="NY">New York (NY)</option>
              <option value="OR">Oregon (OR)</option>
              <option value="MN">Minnesota (MN)</option>
              <option value="CO">Colorado (CO) — Flat 4.4%</option>
              <option value="PA">Pennsylvania (PA) — Flat 3.07%</option>
              <option value="AZ">Arizona (AZ) — Flat 2.5%</option>
              <option value="FL">Florida (FL) — No Income Tax</option>
              <option value="TX">Texas (TX) — No Income Tax</option>
              <option value="NV">Nevada (NV) — No Income Tax</option>
              <option value="WA">Washington (WA) — No Income Tax</option>
              <option value="SD">South Dakota (SD) — No Income Tax</option>
              <option value="WY">Wyoming (WY) — No Income Tax</option>
              <option value="AK">Alaska (AK) — No Income Tax</option>
              <option value="OTHER">Other (No State Tax Applied)</option>
            </select>
            <span class="hint">State income tax applied to retirement withdrawals.</span>
          </div>
        </div>
        <div class="trow" style="margin-top:.7rem;">
          <input type="checkbox" id="rothConversion">
          <label for="rothConversion">Model Roth conversion strategy (convert trad → Roth in low-income years before RMDs)</label>
        </div>
      </div>

      <div class="section-label">📐 Tax Brackets (2026 Baseline)</div>
      <div class="card">
        <h2>✏️ Federal Income Tax Brackets</h2>
        <p style="color:var(--tl);font-size:.85rem;margin-bottom:.8rem;">Edit bracket upper limits and standard deductions below. Click <strong>Apply Brackets</strong> to re-run the simulation with your changes. Values are sourced from your input file.</p>
        <div class="tw" style="margin-bottom:.8rem;">
          <table style="width:100%;border-collapse:collapse;">
            <thead><tr style="background:var(--pl);">
              <th style="padding:.4rem .6rem;text-align:left;font-size:.82rem;">Rate</th>
              <th style="padding:.4rem .6rem;text-align:left;font-size:.82rem;">MFJ Upper Limit ($)</th>
              <th style="padding:.4rem .6rem;text-align:left;font-size:.82rem;">Single Upper Limit ($)</th>
            </tr></thead>
            <tbody id="bracket-edit-body"></tbody>
          </table>
        </div>
        <div class="fg3" style="margin-bottom:.7rem;">
          <div class="fm"><label>Std Deduction — MFJ ($)</label><input type="number" id="std-ded-mfj" min="0"></div>
          <div class="fm"><label>Std Deduction — Single ($)</label><input type="number" id="std-ded-single" min="0"></div>
          <div class="fm"><label>LTCG 0% Threshold — MFJ ($)</label><input type="number" id="ltcg-0-mfj" min="0"></div>
        </div>
        <div class="nav-btns" style="justify-content:flex-start;">
          <button class="nb pri" onclick="applyEditedBrackets()">✅ Apply Brackets &amp; Recalculate</button>
          <button class="nb" onclick="resetBracketsToDefault()" style="margin-left:.5rem;">↩ Reset to Defaults</button>
        </div>
      </div>
      <div class="card">
        <h2>📐 Bracket Inflation Projector</h2>
        <p style="color:var(--tl);font-size:.85rem;margin-bottom:1rem;">View inflation-adjusted brackets for a future year. Changing CPI here also re-runs your full simulation.</p>
        <div class="fg" style="align-items:flex-end;gap:1.2rem;flex-wrap:wrap;">
          <div class="fm">
            <label>CPI Inflation Rate %</label>
            <input type="number" id="tb-cpi" step="0.1" min="0" max="15" value="3.0" style="width:100px;">
          </div>
          <div class="fm">
            <label>Project To Year</label>
            <input type="number" id="tb-year" min="2026" max="2075" value="2035" style="width:110px;">
          </div>
          <div class="fm">
            <label>Filing Status</label>
            <select id="tb-filing" style="width:120px;">
              <option value="MFJ">Married (MFJ)</option>
              <option value="Single">Single</option>
            </select>
          </div>
          <button class="nb pri" onclick="renderTaxBracketTab()">Update Projection</button>
        </div>
        <div id="tb-output" style="margin-top:1.2rem;"></div>
      </div>
      <div class="card">
        <h2>🏥 IRMAA Medicare Surcharge Tiers</h2>
        <p style="color:var(--tl);font-size:.88rem;margin-bottom:.8rem;">Medicare Part B premiums increase for higher-income retirees based on MAGI from 2 years prior. Thresholds below are inflation-projected to the selected year.</p>
        <div id="tb-irmaa-output"></div>
      </div>
    </div><!-- end right column -->
  </div><!-- end two-col -->

  <div class="nav-btns" style="margin-top:1.2rem;justify-content:center;">
    <button class="nb pri" style="font-size:1.05rem;padding:.8rem 2.5rem;" onclick="calculate();">⚡ Calculate My Plan</button>
  </div>
</div>

<!-- ══════════════ TAB 2 — RESULTS ══════════════ -->
<div id="tab-results" class="tab-panel">
  <div id="res-placeholder" class="card" style="text-align:center;padding:2.5rem;">
    <div style="font-size:3rem;margin-bottom:.9rem;">📊</div>
    <h2 style="border:none;color:var(--tx);justify-content:center;">Calculating…</h2>
    <p style="color:var(--tl);margin-top:.5rem;">Loading your plan from <strong>%%CFGFILE%%</strong></p>
  </div>
  <div id="res-content" class="hidden">
    <div id="plan-alert"></div>
    <div id="accum-summary-card" class="card" style="border:2px solid #10b981;display:none;">
      <h2 style="color:#047857;">📈 Pre-Retirement Accumulation Phase</h2>
      <div id="accum-summary"></div>
      <div class="ch" style="height:220px;margin-top:.75rem;"><canvas id="accumChart"></canvas></div>
    </div>
    <div id="bracket-compare-card" class="card" style="display:none;">
      <h2>🔄 Working vs. Retirement Tax Bracket</h2>
      <div id="bracket-compare"></div>
    </div>
    <div class="mgrid">
      <div class="mc teal"><div class="ml">Projected Portfolio at Retirement</div><div class="mv" id="m-total">—</div><div class="ms" id="m-total-s"></div></div>
      <div class="mc" id="m-swa-c"><div class="ml">Safe Withdrawal / Year</div><div class="mv" id="m-swa">—</div><div class="ms" id="m-swa-s"></div></div>
      <div class="mc" id="m-lon-c"><div class="ml">Portfolio Longevity</div><div class="mv" id="m-lon">—</div><div class="ms" id="m-lon-s"></div></div>
    </div>
    <div class="card"><h2>🏛️ Social Security Summary</h2><div id="ss-sum"></div></div>
    <div id="nqdc-advice-card" class="card hidden" style="border:2px solid #6366f1;">
      <h2 style="color:#4f46e5;">🏢 NQDC Plan Analysis</h2>
      <div id="nqdc-advice"></div>
    </div>
    <div class="card">
      <h2>🎯 Recommended Withdrawal Strategy — Bucket Plan</h2>
      <div id="worder-disp"></div>
      <div id="bracket-guidance"></div>
      <div id="strat-content"></div>
    </div>
    <div class="card"><h2>📈 Portfolio Balance Over Time</h2><div class="ch"><canvas id="portChart"></canvas></div></div>
    <div class="card"><h2>💵 Annual Income Sources</h2><div class="ch"><canvas id="incChart"></canvas></div></div>

    <div class="card">
      <h2>🎲 Monte Carlo Risk Engine</h2>
      <details style="margin-bottom:1rem;">
        <summary style="cursor:pointer;font-weight:600;color:var(--p);font-size:.9rem;">ℹ️ How to read this analysis</summary>
        <div style="margin-top:.7rem;padding:.9rem;background:#f8fafc;border-radius:8px;border:1px solid var(--bd);font-size:.85rem;line-height:1.65;">
          <p style="margin:0 0 .6rem;"><strong>What is Monte Carlo analysis?</strong> Your deterministic plan uses a single average return each year. In reality, markets are volatile — some years gain 25%, others lose 20%. Monte Carlo runs <em>1,000+ simulations</em>, each with randomly varied annual returns drawn from a normal distribution centered on your target return. This stress-tests your plan against sequence-of-returns risk (bad years early in retirement are most damaging).</p>
          <p style="margin:0 0 .6rem;"><strong>The Survival Curve (main chart):</strong> The X-axis is your age; the Y-axis is the percentage of simulations where your portfolio still has money at that age. For example, if the curve shows <strong>82% at age 85</strong>, it means 820 out of 1,000 scenarios still had a positive balance at that age.</p>
          <div style="display:flex;gap:.6rem;flex-wrap:wrap;margin:.7rem 0;">
            <span style="background:#dcfce7;border:1px solid #86efac;border-radius:6px;padding:.3rem .7rem;font-size:.8rem;font-weight:600;color:#166534;">🟢 &gt;80% — Safe Zone</span>
            <span style="background:#fef9c3;border:1px solid #fde047;border-radius:6px;padding:.3rem .7rem;font-size:.8rem;font-weight:600;color:#854d0e;">🟡 50–80% — Caution Zone</span>
            <span style="background:#fee2e2;border:1px solid #fca5a5;border-radius:6px;padding:.3rem .7rem;font-size:.8rem;font-weight:600;color:#991b1b;">🔴 &lt;50% — At Risk Zone</span>
          </div>
          <p style="margin:0 0 .6rem;"><strong>Ending Wealth Percentiles:</strong> After all simulations complete, the 10th/50th/90th percentile values show the range of portfolio outcomes at your life expectancy. The 10th percentile represents a "bad luck" scenario; the 90th represents favorable returns throughout retirement.</p>
          <p style="margin:0;"><strong>Volatility input:</strong> Annual standard deviation (σ). A diversified 80/20 stock-bond portfolio typically has σ ≈ 10–14%. Higher σ = wider range of outcomes but same average — it spreads the survival curve rather than shifting it.</p>
        </div>
      </details>
      <div class="fg">
        <div class="fm"><label>Iterations</label><input type="number" id="mcIterations" min="100" value="5000"></div>
        <div class="fm"><label>Annual Volatility % (σ)</label><input type="number" id="mcVol" step="0.1" min="0" value="12"></div>
      </div>
      <div style="margin-top:.7rem;" class="nav-btns"><button class="nb pri" onclick="runMonteCarlo()">▶ Run Monte Carlo</button></div>
      <div id="mcSummary" class="ssp" style="margin-top:.7rem;"></div>
      <div style="position:relative;">
        <div class="ch" style="height:300px;margin-top:.75rem;"><canvas id="mcChart"></canvas></div>
        <div style="display:flex;gap:.5rem;justify-content:center;flex-wrap:wrap;margin-top:.5rem;font-size:.76rem;">
          <span style="display:flex;align-items:center;gap:.3rem;"><span style="width:14px;height:4px;background:#ff6b6b;display:inline-block;border-radius:2px;"></span>Survival probability (% of simulations solvent)</span>
          <span style="display:flex;align-items:center;gap:.3rem;"><span style="width:12px;height:12px;background:#dcfce7;border:1px solid #86efac;display:inline-block;border-radius:2px;"></span>&gt;80% safe</span>
          <span style="display:flex;align-items:center;gap:.3rem;"><span style="width:12px;height:12px;background:#fef9c3;border:1px solid #fde047;display:inline-block;border-radius:2px;"></span>50–80% caution</span>
          <span style="display:flex;align-items:center;gap:.3rem;"><span style="width:12px;height:12px;background:#fee2e2;border:1px solid #fca5a5;display:inline-block;border-radius:2px;"></span>&lt;50% at risk</span>
        </div>
      </div>
    </div>
    <div class="card">
      <h2>📋 Year-by-Year Projection</h2>
      <div style="display:flex;align-items:center;gap:1rem;flex-wrap:wrap;margin-bottom:.6rem;">
        <button class="cb-btn" onclick="toggleTbl()">Show / Hide Table</button>
        <label style="display:flex;align-items:center;gap:.4rem;font-size:.83rem;cursor:pointer;">
          <input type="checkbox" id="realDollarsToggle" onchange="rerenderTable()" style="width:16px;height:16px;">
          Show in <strong>today's dollars</strong> (inflation-adjusted)
        </label>
        <span id="dollarsModeNote" style="font-size:.77rem;color:var(--tl);"></span>
        <button class="cb-btn" onclick="exportTableToCSV()" style="margin-left:auto;">⬇ Export to CSV</button>
      </div>
      <div id="tbl-wrap">
        <div class="tw">
          <table><thead><tr>
            <th>Year</th><th>Age</th><th>SS Income</th><th>NQDC</th><th>Other Inc.</th>
            <th>Portfolio Draw</th><th>From Trad.</th><th>From Roth</th><th>RMD</th>
            <th>Tax Bracket</th><th>Fed. Tax</th><th>State Tax</th><th>IRMAA</th><th>End Balance</th>
          </tr></thead><tbody id="tbl-body"></tbody></table>
        </div>
      </div>
      <div id="tbl-dollar-note" style="font-size:.75rem;color:var(--tl);margin-top:.4rem;padding:.5rem .7rem;background:#fefce8;border:1px solid #fde047;border-radius:6px;display:none;"></div>
    </div>
    <div class="al info" style="font-size:.77rem;">⚠️ <strong>Disclaimer:</strong> Educational estimates only. Returns are not guaranteed. Consult a Certified Financial Planner (CFP) before making retirement decisions. Tax calculations are simplified.</div>
  </div>
</div>


</div><!-- .content -->

<script>
// ═══════════════════════════════════════════════════
//  CONSTANTS
// ═══════════════════════════════════════════════════
const RMD = {73:26.5,74:25.5,75:24.6,76:23.7,77:22.9,78:22.0,79:21.1,80:20.2,81:19.4,82:18.5,83:17.7,84:16.8,85:16.0,86:15.2,87:14.4,88:13.7,89:12.9,90:12.2,91:11.5,92:10.8,93:10.1,94:9.5,95:8.9,96:8.4,97:7.8,98:7.3,99:6.8,100:6.4,101:6.0,102:5.6,103:5.2,104:4.9,105:4.6};
const FRA = 67;
let pChart = null, iChart = null;

// ── 2026 Federal Tax Brackets (IRS published) — overridable from input file ──
let BRACKETS = {
  MFJ:    [{r:.10,to:24800},{r:.12,to:100800},{r:.22,to:211400},{r:.24,to:403550},{r:.32,to:512450},{r:.35,to:768700},{r:.37,to:1e9}],
  Single: [{r:.10,to:12400},{r:.12,to:50400},{r:.22,to:105700},{r:.24,to:201775},{r:.32,to:256225},{r:.35,to:640600},{r:.37,to:1e9}]
};
const BRACKETS_DEFAULT = JSON.parse(JSON.stringify(BRACKETS)); // deep-copy for reset
let STD_DED  = {MFJ:31500, Single:15750};   // 2026 est.
const STD_DED_DEFAULT = {...STD_DED};
let LTCG_0   = {MFJ:96950, Single:48475};   // 0% long-term cap gains up to these AGI levels
const LTCG_0_DEFAULT = {...LTCG_0};
const LTCG_15  = {MFJ:600050,Single:535000};  // 15% bracket above this = 20%
// IRMAA Medicare Part B surcharge triggers (MAGI, 2026 est.)
const IRMAA_TH = {MFJ:[218000,274000,345000,426000], Single:[109000,137000,173000,213000]};
// IRMAA annual Medicare Part B surcharges by tier (2026 est., per person per month × 12)
const IRMAA_SURCHARGE = [0, 816, 2040, 3264, 4488]; // tier 0–4 annual amounts (single person)

// CPI / indexing settings (baseline 2026 and CPI 3.0%)
const CPI_BASE_YEAR = 2026;
let CPI_RATE = 0.03; // 3.0% per year — user-adjustable via Tax Brackets tab
const HCARE_RATE = 0.04; // healthcare inflation used for HSA adjustments

// ── State Income Tax Data — 2026 baseline thresholds ──
// Each entry: { brackets:{MFJ:[],Single:[]}, stdDed:{MFJ,Single}, taxesSS:bool, flatRate:number|null, noTax:bool }
const STATE_TAX_DATA = {
  CA: {
    taxesSS: false,
    stdDed: {MFJ:9808, Single:4904},
    brackets: {
      Single: [{r:0.01,to:10412},{r:0.02,to:24684},{r:0.04,to:38959},{r:0.06,to:54081},{r:0.08,to:68350},{r:0.093,to:349137},{r:0.103,to:418961},{r:0.113,to:698271},{r:0.123,to:1e9}],
      MFJ:    [{r:0.01,to:20824},{r:0.02,to:49368},{r:0.04,to:77918},{r:0.06,to:108162},{r:0.08,to:136700},{r:0.093,to:698274},{r:0.103,to:837922},{r:0.113,to:1000000},{r:0.123,to:1e9}]
    }
  },
  NY: {
    taxesSS: false,
    stdDed: {MFJ:16050, Single:8000},
    brackets: {
      Single: [{r:0.04,to:17150},{r:0.045,to:23600},{r:0.0525,to:27900},{r:0.0585,to:161550},{r:0.0625,to:323200},{r:0.0685,to:2155350},{r:0.0965,to:5000000},{r:0.103,to:25000000},{r:0.109,to:1e9}],
      MFJ:    [{r:0.04,to:17150},{r:0.045,to:23600},{r:0.0525,to:27900},{r:0.0585,to:323200},{r:0.0625,to:2155350},{r:0.0685,to:5000000},{r:0.0965,to:25000000},{r:0.103,to:1e9}]
    }
  },
  OR: {
    taxesSS: false,
    stdDed: {MFJ:4865, Single:2420},
    brackets: {
      Single: [{r:0.0475,to:4050},{r:0.0675,to:10200},{r:0.0875,to:125000},{r:0.099,to:1e9}],
      MFJ:    [{r:0.0475,to:8100},{r:0.0675,to:20400},{r:0.0875,to:250000},{r:0.099,to:1e9}]
    }
  },
  MN: {
    taxesSS: true,
    stdDed: {MFJ:29150, Single:14575},
    brackets: {
      Single: [{r:0.0535,to:31690},{r:0.068,to:104090},{r:0.0785,to:193240},{r:0.0985,to:1e9}],
      MFJ:    [{r:0.0535,to:46330},{r:0.068,to:184040},{r:0.0785,to:321450},{r:0.0985,to:1e9}]
    }
  },
  CO:    { taxesSS: false, flatRate: 0.044,  stdDed: {MFJ:0,Single:0}, brackets: {} },
  PA:    { taxesSS: false, flatRate: 0.0307, stdDed: {MFJ:0,Single:0}, brackets: {} },
  AZ:    { taxesSS: false, flatRate: 0.025,  stdDed: {MFJ:0,Single:0}, brackets: {} },
  FL:    { noTax: true },
  TX:    { noTax: true },
  NV:    { noTax: true },
  WA:    { noTax: true },
  SD:    { noTax: true },
  WY:    { noTax: true },
  AK:    { noTax: true },
  OTHER: { noTax: true }
};

// Active retirement state — initialized from config; overridable via dropdown
let RETIREMENT_STATE = 'CA';

/** Compute marginal, effective tax, and bracket room using adjusted brackets/std deduction */
function computeTaxVars(agi, filing, multiplier=1){
  const std = Math.round((STD_DED[filing]||STD_DED.MFJ) * multiplier);
  const bs = (BRACKETS[filing]||BRACKETS.MFJ).map(b=>({r:b.r,to:Math.round(b.to*multiplier)}));
  const taxable = Math.max(0, agi - std);
  let marg = 0, tax=0, prev=0, room=0;
  for(const b of bs){
    if(taxable <= b.to){ tax += (taxable - prev) * b.r; marg = b.r; room = Math.max(0, b.to - taxable); break; }
    tax += (b.to - prev) * b.r; prev = b.to;
  }
  if(taxable > bs[bs.length-1].to) marg = bs[bs.length-1].r;
  const eff = agi>0? tax/agi : 0;
  return {margRate:marg, tax:Math.max(0,Math.round(tax)), effRate:eff, room:Math.round(room), stdDeduction:std};
}

/** Compute state income tax for the given AGI, filing status, and inflation multiplier */
function computeStateTax(agi, filing, multiplier=1, state=RETIREMENT_STATE){
  const sd = STATE_TAX_DATA[state] || STATE_TAX_DATA.OTHER;
  if(sd.noTax) return 0;
  const deduction = Math.round(((sd.stdDed||{})[filing] || (sd.stdDed||{}).MFJ || 0) * multiplier);
  const taxable = Math.max(0, agi - deduction);
  if(sd.flatRate) return Math.max(0, Math.round(taxable * sd.flatRate));
  const bs = ((sd.brackets||{})[filing] || (sd.brackets||{}).MFJ || []).map(b=>({r:b.r,to:Math.round(b.to*multiplier)}));
  let tax=0, prev=0;
  for(const b of bs){
    if(taxable <= b.to){ tax += (taxable - prev) * b.r; break; }
    tax += (b.to - prev) * b.r; prev = b.to;
  }
  return Math.max(0, Math.round(tax));
}

/**
 * Compute the federally taxable portion of Social Security income.
 * Implements IRS Section 86 tiered provisional income formula.
 * @param {number} ssInc   - Total SS income received this year
 * @param {number} otherIncome - All other AGI items (wages, pension, trad withdrawals, etc.)
 * @param {string} filing  - 'MFJ' or 'Single'
 */
function taxableSSAmt(ssInc, otherIncome, filing) {
  if (ssInc <= 0) return 0;
  const pi = otherIncome + ssInc * 0.5;   // provisional income
  const [low, high] = (filing === 'MFJ') ? [32000, 44000] : [25000, 34000];
  if (pi <= low)  return 0;
  if (pi <= high) return Math.min(ssInc * 0.50, (pi - low) * 0.50);
  // Above upper threshold: tier1 (lower band) + 85% on excess
  const tier1 = Math.min(ssInc * 0.50, (high - low) * 0.50);
  const tier2 = (pi - high) * 0.85;
  return Math.min(ssInc * 0.85, tier1 + tier2);
}

/** Evaluate conversion options for common target brackets (12%, 22%, and 24%).
 * Returns {amount, choice} where choice is '12%', '22%', or '24%'. */
function evalConversionOptions(provAGI, tradBal, filing, multiplier=1){
  const currentFed = computeTaxVars(provAGI, filing, multiplier).tax;
  const currentState = computeStateTax(Math.max(0, provAGI), filing, multiplier);
  const currentTotal = currentFed + currentState;
  const targets = [0.12, 0.22, 0.24];
  let best = {amount:0, choice:null, incTax:Infinity, options:[]};
  const std = Math.round((STD_DED[filing]||STD_DED.MFJ) * multiplier);
  const bs = (BRACKETS[filing]||BRACKETS.MFJ).map(b=>({r:b.r,to:Math.round(b.to*multiplier)}));
  for(const t of targets){
    // find bracket threshold 'to' for this rate
    let thresh = bs.find(b=>Math.abs(b.r - t) < 1e-9)?.to;
    if(thresh===undefined) { thresh = bs[bs.length-1].to; }
    // AGI needed to reach top of that bracket
    const needed = Math.max(0, thresh + std - provAGI);
    const convAmt = Math.min(tradBal, Math.floor(needed*0.99));
    if(convAmt<=0){ best.options.push({rate:t,amount:0,incTax:0}); continue; }
    const newFed = computeTaxVars(provAGI+convAmt, filing, multiplier).tax;
    const newState = computeStateTax(Math.max(0, provAGI+convAmt), filing, multiplier);
    const incTax = (newFed + newState) - currentTotal;
    best.options.push({rate:t,amount:convAmt,incTax});
    if(incTax < best.incTax){ best = {amount:convAmt, choice:Math.round(t*100)+'%', incTax, options:best.options}; }
  }
  return best;
}

/** Marginal federal tax rate given AGI and filing status */
function getMargRate(agi, filing) {
  const taxable = Math.max(0, agi - (STD_DED[filing]||STD_DED.MFJ));
  const bs = BRACKETS[filing]||BRACKETS.MFJ;
  for (const b of bs) { if (taxable <= b.to) return b.r; }
  return 0.37;
}
/** Effective tax rate (rough estimate) */
function getEffRate(agi, filing) {
  const taxable = Math.max(0, agi - (STD_DED[filing]||STD_DED.MFJ));
  const bs = BRACKETS[filing]||BRACKETS.MFJ;
  let tax=0, prev=0;
  for (const b of bs) {
    if (taxable <= b.to) { tax += (taxable-prev)*b.r; break; }
    tax += (b.to-prev)*b.r; prev=b.to;
  }
  return agi > 0 ? tax/agi : 0;
}
/** Dollars remaining in current bracket before stepping up */
function bracketRoom(agi, filing) {
  const taxable = Math.max(0, agi - (STD_DED[filing]||STD_DED.MFJ));
  const bs = BRACKETS[filing]||BRACKETS.MFJ;
  for (const b of bs) { if (taxable < b.to) return Math.max(0, b.to - taxable); }
  return 0;
}
/** IRMAA tier (0 = no surcharge, 1-4 = surcharge tiers) */
function getIRMAA(agi, filing) {
  const th = IRMAA_TH[filing]||IRMAA_TH.MFJ;
  for (let i=th.length-1;i>=0;i--) { if (agi>th[i]) return i+1; }
  return 0;
}
/** NQDC annual distribution based on balance and schedule */
function nqdcDist(balance, distType) {
  if (balance <= 0) return 0;
  if (distType==='lump') return balance;
  if (distType==='5yr')  return balance / 5;
  if (distType==='10yr') return balance / 10;
  return balance / 10;
}
/** Years of NQDC distributions */
function nqdcYears(distType) {
  if (distType==='lump') return 1;
  if (distType==='5yr')  return 5;
  return 10;
}

// ── 401k / IRA contribution limits (2025) ───────────────────
const K401_BASE = 23500;
const K401_CATCHUP = 7500;       // age 50–59 and 64+
const K401_SUPER = 11250;        // SECURE 2.0 super catch-up age 60–63
const IRA_BASE   = 7000;
const IRA_CATCHUP = 1000;        // age 50+

function get401kMax(age) {
  if (age>=60 && age<=63) return K401_BASE + K401_SUPER;   // $34,750
  if (age>=50)             return K401_BASE + K401_CATCHUP; // $31,000
  return K401_BASE;                                          // $23,500
}
function getIRAMax(age) { return age>=50 ? IRA_BASE+IRA_CATCHUP : IRA_BASE; }

/** Resolve a contribution value: 'max' → age-appropriate limit, else parse as number */
function resolveContrib(val, age, type) {
  const s = String(val||'0').trim().toLowerCase();
  if (s==='max') return type==='401k' ? get401kMax(age) : getIRAMax(age);
  return parseFloat(s)||0;
}

// ═══════════════════════════════════════════════════
//  HELPERS
// ═══════════════════════════════════════════════════
const $  = id => document.getElementById(id);
const fmt  = n  => '$' + Math.round(n).toLocaleString();
const fmtK = n  => { if(n>=1e6) return '$'+(n/1e6).toFixed(2)+'M'; if(n>=1e3) return '$'+Math.round(n/1e3)+'K'; return '$'+Math.round(n); };
const num  = id => parseFloat($(id)?.value)||0;
const int  = id => parseInt($(id)?.value)||0;

/** Calculate age from a MM/DD/YYYY string. Returns 0 if invalid. */
function calcAge(bdayStr) {
  if (!bdayStr) return 0;
  const p = bdayStr.trim().split('/');
  if (p.length !== 3) return 0;
  const m = parseInt(p[0]), d = parseInt(p[1]), y = parseInt(p[2]);
  if (!m||!d||!y||y<1900||y>2020) return 0;
  const today = new Date();
  let age = today.getFullYear() - y;
  if (today.getMonth()+1 < m || (today.getMonth()+1 === m && today.getDate() < d)) age--;
  return age > 0 ? age : 0;
}

/** Auto-insert slashes: 2 digits → "/" after month, 4 digits → "/" after day. */
function fmtBday(el) {
  let v = el.value.replace(/[^\d]/g, '');
  if (v.length > 2) v = v.slice(0,2) + '/' + v.slice(2);
  if (v.length > 5) v = v.slice(0,5) + '/' + v.slice(5,9);
  el.value = v;
}

/** Update the age hint below a birthday field. */
function updAge(bdayId, hintId) {
  const age = calcAge($(bdayId)?.value);
  const hint = $(hintId);
  if (!hint) return;
  if (age > 0) {
    hint.textContent = `Current age: ${age}`;
    hint.style.color = 'var(--p)';
    hint.style.fontWeight = '600';
  } else {
    hint.textContent = 'Enter birthday — age calculated automatically';
    hint.style.color = '';
    hint.style.fontWeight = '';
  }
}

// ═══════════════════════════════════════════════════
//  TAB NAV
// ═══════════════════════════════════════════════════
function sw(tab) {
  document.querySelectorAll('.tab-panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  $('tab-'+tab).classList.add('active');
  document.querySelector(`[onclick="sw('${tab}')"]`).classList.add('active');
  updateSS(); updateSWR();
}

// ═══════════════════════════════════════════════════
//  SPOUSE TOGGLE
// ═══════════════════════════════════════════════════
function toggleSpouse() {
  const on = $('hasSpouse').checked;
  ['spouse-section','ss2-card','sp-trad','sp-roth','sp-contrib'].forEach(id=>$(id)?.classList.toggle('hidden',!on));
  $('pension2g')?.classList.toggle('hidden',!on);
  $('sp-hsa')?.classList.toggle('hidden',!on);
  $('spouse-income-grp')?.classList.toggle('hidden',!on);
}
function toggleNqdc() {
  const on = $('hasNqdc').checked;
  $('nqdc-section')?.classList.toggle('hidden', !on);
}

// ═══════════════════════════════════════════════════
//  SOCIAL SECURITY
// ═══════════════════════════════════════════════════
function ssBenefit(pia, age) {
  if(pia<=0) return 0;
  if(age>=70) return pia*1.24;
  if(age>=FRA) return pia*(1+(age-FRA)*0.08);
  const mo=(FRA-age)*12;
  const red = mo<=36 ? mo*(5/9/100) : 36*(5/9/100)+(mo-36)*(5/12/100);
  return pia*(1-red);
}
function spouseBenefit(ownPia, workerPia, age) {
  const maxS = workerPia*0.5;
  const own  = ssBenefit(ownPia, age);
  let s;
  if(age>=FRA){ s=maxS; }
  else {
    const mo=(FRA-age)*12;
    const red = mo<=36 ? mo*(25/36/100) : 36*(25/36/100)+(mo-36)*(5/12/100);
    s=maxS*(1-red);
  }
  return Math.max(own, s);
}
function updateSS() {
  const pia1=num('pia1'), age1=int('ssAge1')||67;
  const b1=ssBenefit(pia1,age1), pct=pia1>0?(b1-pia1)/pia1*100:0;
  $('ssp1').innerHTML=`<div class="ssr"><span>PIA (benefit at FRA)</span><span>${fmt(pia1)}/mo</span></div>
    <div class="ssr"><span>Benefit at age ${age1}</span><span style="color:${pct>=0?'var(--ok)':'var(--dan)'}">${fmt(b1)}/mo (${pct>=0?'+':''}${pct.toFixed(1)}%)</span></div>
    <div class="ssr"><span>Annual SS benefit</span><span>${fmt(b1*12)}/yr</span></div>`;
  if($('hasSpouse').checked){
    const pia2=num('pia2'),age2=int('ssAge2')||67;
    const b2=spouseBenefit(pia2,pia1,age2);
    const isSp=pia2<pia1&&b2>ssBenefit(pia2,age2);
    $('ssp2').innerHTML=`<div class="ssr"><span>Spouse PIA</span><span>${fmt(pia2)}/mo</span></div>
      <div class="ssr"><span>Benefit at age ${age2}${isSp?' <em>(spousal applied)</em>':''}</span><span>${fmt(b2)}/mo</span></div>
      <div class="ssr"><span>Annual SS (spouse)</span><span>${fmt(b2*12)}/yr</span></div>`;
  }
}

// ═══════════════════════════════════════════════════
//  ALLOCATION
// ═══════════════════════════════════════════════════
function setPreset(s){
  $('stockPct').value=s; updAlloc();
  document.querySelectorAll('.pb').forEach(b=>b.classList.remove('active'));
  const map={30:'Conservative 30/70',60:'Moderate 60/40',80:'Growth 80/20',100:'All-Stocks'};
  document.querySelectorAll('.pb').forEach(b=>{if(b.textContent.trim()===map[s])b.classList.add('active');});
}
function updAlloc(){
  const s=int('stockPct');
  $('allocDisp').textContent=s+'% stocks / '+(100-s)+'% bonds';
  document.querySelectorAll('.pb').forEach(b=>b.classList.remove('active'));
}

// ═══════════════════════════════════════════════════
//  INCOME BRACKET HINT
// ═══════════════════════════════════════════════════
function updateIncomeHint(){
  const el=$('income-bracket-hint'); if(!el) return;
  const y=num('yourIncome'), s=num('spouseIncome');
  const total=y+s;
  if(total<=0){ el.innerHTML=''; return; }
  const filing=$('filingStatus')?.value||'MFJ';
  const marg=getMargRate(total,filing);
  const eff=getEffRate(total,filing);
  const room=bracketRoom(total,filing);
  el.innerHTML=`
    <div class="ssr"><span>Combined household income</span><span><strong>${fmt(total)}/yr</strong></span></div>
    <div class="ssr"><span>Current marginal tax bracket</span><span><strong style="color:${marg<=0.12?'var(--ok)':marg<=0.22?'var(--acc)':'var(--dan)'};">${(marg*100).toFixed(0)}%</strong></span></div>
    <div class="ssr"><span>Effective rate</span><span>${(eff*100).toFixed(1)}%</span></div>
    <div class="ssr"><span>Room left in current bracket</span><span>${fmt(room)}</span></div>`;
}

// ═══════════════════════════════════════════════════
//  SAFE WITHDRAWAL RATE
// ═══════════════════════════════════════════════════
function getSWR(){
  const g=$('goalType')?.value;
  if(g==='perpetual') return 0.03;
  if(g==='custom'){ const y=int('customGoalYears')||30; return y>=40?0.033:y>=30?0.040:y>=25?0.045:0.05; }
  const y=parseInt(g); return y>=40?0.033:y>=35?0.037:0.040;
}
function getGoalYrs(){
  const g=$('goalType')?.value;
  if(g==='perpetual') return 60;
  if(g==='custom') return int('customGoalYears')||30;
  return parseInt(g)||30;
}
function getTotalPort(){
  return num('trad401k')+num('tradIRA')+num('roth401k')+num('rothIRA')+
    num('hsa')+num('taxable')+num('cash')+
    num('spouse401k')+num('spouseTradIRA')+num('spouseRothIRA')+num('spouseRoth401k')+num('spouseHSA');
}
function updateSWR(){
  const g=$('goalType')?.value;
  if(!g) return;
  $('custom-yrs').classList.toggle('hidden',g!=='custom');
  const swr=getSWR(), total=getTotalPort();
  if($('swr-disp')) $('swr-disp').innerHTML=`<div class="ssr"><span>Safe Withdrawal Rate</span><span><strong>${(swr*100).toFixed(1)}%</strong></span></div>
    <div class="ssr"><span>Safe annual withdrawal from ${fmtK(total)}</span><span><strong style="color:var(--pd)">${fmtK(total*swr)}</strong></span></div>`;
}

// ═══════════════════════════════════════════════════
//  RMD
// ═══════════════════════════════════════════════════
function getRmd(bal,age){ if(age<73||bal<=0) return 0; const p=RMD[Math.min(age,105)]||4.6; return bal/p; }

// ═══════════════════════════════════════════════════
//  PROJECTION  (two-phase: accumulation + distribution)
// ═══════════════════════════════════════════════════
function project(inp){
  const { age1, retAge1, lifeExp1, hasSpouse, age2, retAge2, lifeExp2,
    pia1, ssAge1, pia2, ssAge2,
    pension1, pension2, pensionAge, otherIncome, otherIncomeStopAge,
    annualSpending, spendingReduceAge, reducedSpending,
    trad0, trad1, trad2, roth0, roth1, roth2,
    taxable0, cash0, hsa0, hsa1, hsa2,
    stockReturn, bondReturn, stockAlloc, inflationRate, goalYears,
    hasNqdc, nqdcBalance, nqdcDistType, nqdcStartAge, nqdcDeferral,
    filingStatus, rothConversion,
    contrib401k, contribRoth, contribTaxable, spouseContrib401k, spouseContribRoth
  } = inp;

  const pr  = (stockAlloc/100)*(stockReturn/100)+((100-stockAlloc)/100)*(bondReturn/100);
  const inf = inflationRate/100;
  const startYr = new Date().getFullYear();

  // ── PHASE 1: ACCUMULATION ──────────────────────────────────
  // Run from current age up to (but not including) retirement age
  let tb1 = trad1||0, tb2 = trad2||0, rb = roth0||0, xb = taxable0||0, cb = cash0||0, hb = hsa0||0;
  let tb = tb1 + tb2;
  let xbBasis = xb; // cost basis tracking for taxable brokerage (initial balance = basis)
  let nqdcBal = nqdcBalance||0;
  const accumRows = [];
  const yearsToRetire = Math.max(0, retAge1 - age1);

  for(let yr=0; yr<yearsToRetire; yr++){
    const a1 = age1 + yr;
    const a2 = hasSpouse ? age2 + yr : null;
    // Contributions this year
    const c401 = resolveContrib(contrib401k, a1, '401k');
    const cIRA = resolveContrib(contribRoth,  a1, 'ira');
    const cTax = parseFloat(contribTaxable)||0;
    const sc401 = hasSpouse ? resolveContrib(spouseContrib401k, a2!==null?a2:a1, '401k') : 0;
    const scIRA = hasSpouse ? resolveContrib(spouseContribRoth,  a2!==null?a2:a1, 'ira')  : 0;
    // NQDC accumulation (deferral grows the balance)
    const defAmt = parseFloat(nqdcDeferral)||0;
    if(hasNqdc && defAmt>0) nqdcBal += defAmt;
    // Add contributions then grow (taxable contributions add to cost basis)
    tb1 = (tb1 + c401) * (1 + pr);
    tb2 = (tb2 + sc401) * (1 + pr);
    tb = tb1 + tb2;
    rb = (rb + cIRA + scIRA) * (1 + pr);
    xbBasis += cTax; // contributions are basis; growth is not
    xb = (xb + cTax) * (1 + pr);
    hb = hb * (1 + pr * 0.8);
    cb = cb * (1 + Math.min(inf*0.9, 0.04));
    const tot = tb + rb + xb + cb + hb;
    accumRows.push({
      year: startYr+yr, age1: a1, age2: a2, phase:'accum',
      contrib: c401+cIRA+cTax+sc401+scIRA,
      tb, rb, xb, cb, hb, nqdcBal, tot
    });
  }

  // Retirement-start balances (may equal current if already retired)
  const retTrad1   = tb1;
  const retTrad2   = tb2;
  const retRoth    = rb;
  const retTaxable = xb;
  const retCash    = cb;
  const retHSA     = hb;
  const retNqdcBal = nqdcBal;
  const deterministicRetireStart = retTrad1 + retTrad2 + retRoth + retTaxable + retCash + retHSA;

  // Run accumulation Monte Carlo (10,000 iterations) to get p10/p50 bands
  const accumMC = accumRows.length > 0 ? runAccumMonteCarlo(inp, accumRows) : null;
  // Use median (p50) as the retirement starting balance for the distribution phase
  const retireStartBal = (accumMC && accumMC.finalP50) ? accumMC.finalP50 : deterministicRetireStart;
  // Scale each bucket proportionally to the median outcome
  const accumScaleFactor = deterministicRetireStart > 0 ? retireStartBal / deterministicRetireStart : 1;

  // ── PHASE 2: DISTRIBUTION ─────────────────────────────────
  tb1 = retTrad1 * accumScaleFactor; tb2 = retTrad2 * accumScaleFactor; tb = tb1 + tb2;
  rb = retRoth * accumScaleFactor; xb = retTaxable * accumScaleFactor;
  cb = retCash * accumScaleFactor; hb = retHSA * accumScaleFactor;
  // xbBasis carries over from accumulation phase (cost basis of taxable brokerage)
  nqdcBal=retNqdcBal;
  const magiHistory = [];
  const nqdcYrs    = nqdcYears(nqdcDistType);
  let nqdcYrsPaid  = 0;
  // Inflation-adjust spending to retirement date
  let spending = annualSpending * Math.pow(1+inf, yearsToRetire);
  const maxYrs = Math.max(goalYears, Math.max(lifeExp1, hasSpouse?lifeExp2:0) - retAge1);
  const retireRows = [];

  for(let yr=0; yr<=maxYrs; yr++){
    const year = startYr + yearsToRetire + yr;
    const mult = Math.pow(1 + CPI_RATE, Math.max(0, year - CPI_BASE_YEAR));
    const a1 = retAge1 + yr;
    const a2 = hasSpouse ? (age2 + (retAge1 - age1) + yr) : null;
    if(spendingReduceAge&&reducedSpending&&a1>=spendingReduceAge)
      spending = Math.min(spending, reducedSpending*Math.pow(1+inf, yearsToRetire+yr));
    const cola = Math.pow(1.025, yearsToRetire + yr);
    // Social Security
    let ss1=0, ss2=0;
    if(pia1>0 && a1>=ssAge1) ss1 = ssBenefit(pia1,ssAge1)*12*cola;
    if(hasSpouse && a2!==null && a2>=ssAge2)
      ss2 = (pia2>=pia1 ? ssBenefit(pia2,ssAge2) : spouseBenefit(pia2,pia1,ssAge2))*12*cola;
    const ssInc = ss1+ss2;
    const penInc = a1>=pensionAge ? (pension1+(hasSpouse?pension2:0))*Math.pow(1+inf, yearsToRetire+yr) : 0;
    const othInc = (!otherIncomeStopAge||a1<otherIncomeStopAge) ? otherIncome*Math.pow(1+inf, yearsToRetire+yr) : 0;
    // NQDC distribution
    let nqdcInc=0;
    if(hasNqdc && nqdcBal>0 && a1>=nqdcStartAge && nqdcYrsPaid<nqdcYrs){
      nqdcInc = nqdcDistType==='lump' ? nqdcBal : retNqdcBal/nqdcYrs;
      nqdcInc = Math.min(nqdcInc, nqdcBal);
      nqdcBal = Math.max(0, nqdcBal-nqdcInc);
      nqdcYrsPaid++;
    }
    const extInc   = ssInc+penInc+othInc+nqdcInc;
    // IRMAA surcharge — based on MAGI from 2 years prior (only once 2 years of history exist)
    // Computed here (before portNeed) because it uses only historical data, not current-year AGI
    const magiForIrmaa = magiHistory.length >= 2 ? magiHistory[magiHistory.length-2] : 0;
    const irmaaAdj = (()=>{ const th=(IRMAA_TH[filingStatus]||IRMAA_TH.MFJ); for(let i=th.length-1;i>=0;i--){ if(magiForIrmaa>Math.round(th[i]*mult)) return i+1; } return 0; })();
    // Count Medicare-eligible spouses (must be ≥65); surcharge is per person per year
    const medicarePersons = (a1>=65?1:0) + (hasSpouse&&a2!==null&&a2>=65?1:0);
    const irmaaSurcharge = Math.round((IRMAA_SURCHARGE[irmaaAdj]||0) * Math.max(medicarePersons,0) * mult);
    const portNeed = Math.max(0, spending - extInc + irmaaSurcharge);
    // Withdrawals — strict hierarchy: RMD -> Taxable -> Traditional (fill bracket room) -> HSA -> Cash -> Roth
    const rmd1 = getRmd(tb1, a1);
    const rmd2 = (hasSpouse && a2 !== null) ? getRmd(tb2, a2) : 0;
    const rmd = rmd1 + rmd2;
    let fTrad1 = Math.min(tb1, rmd1), fTrad2 = Math.min(tb2, rmd2);
    let fTrad = fTrad1 + fTrad2, fTax = 0, fRoth = 0, fCash = 0, fHSA = 0;
    let need = Math.max(0, portNeed - fTrad);
    // 1) Taxable brokerage first
    // Compute taxable gain fraction: only the gain portion (above cost basis) is taxable at LTCG rates
    const xbGainFrac = (xb > 0 && xb > xbBasis) ? Math.max(0, (xb - xbBasis) / xb) : 0;
    if(need > 0){ fTax = Math.min(xb, need); need -= fTax; }
    // Update cost basis proportionally after taxable withdrawal
    if(fTax > 0 && xb > 0){
      const basisWithdrawn = fTax * (xbBasis / xb);  // basis portion of withdrawal
      xbBasis = Math.max(0, xbBasis - basisWithdrawn);
    }
    // 2) Traditional — but only up to current bracket room to avoid pushing into higher bracket
    let extraTrad1 = 0, extraTrad2 = 0;
    if(need > 0){
      const _otherPT = nqdcInc + penInc + othInc + rmd + (fTax * xbGainFrac);
      const provAGI_beforeTrad = taxableSSAmt(ssInc, _otherPT, filingStatus||'MFJ') + _otherPT;
      const prov = computeTaxVars(provAGI_beforeTrad, filingStatus||'MFJ', mult);
      const targetBracketRate = 0.24; // Target 24% bracket for traditional withdrawals
      const brackets = BRACKETS[filingStatus || 'MFJ'];
      const targetBracket = brackets.find(b => b.r === targetBracketRate);
      const targetTop = targetBracket ? targetBracket.to * mult : prov.room;
      const room = Math.max(0, targetTop - provAGI_beforeTrad);
      const availTrad1 = Math.max(0, tb1 - fTrad1);
      const availTrad2 = Math.max(0, tb2 - fTrad2);
      const availTrad = availTrad1 + availTrad2;
      const takeTrad = Math.min(availTrad, need, room);
      if(takeTrad > 0){
        extraTrad1 = Math.min(availTrad1, Math.round(takeTrad * (availTrad1 / Math.max(availTrad, 1))));
        extraTrad2 = Math.min(availTrad2, takeTrad - extraTrad1);
        if(extraTrad1 + extraTrad2 < takeTrad){
          const remaining = takeTrad - (extraTrad1 + extraTrad2);
          if(availTrad1 - extraTrad1 >= remaining) extraTrad1 += remaining;
          else extraTrad2 += remaining;
        }
        fTrad += takeTrad; need -= takeTrad;
      }
    }
    // 3) HSA
    if(need > 0){ fHSA = Math.min(hb, need); need -= fHSA; }
    // 4) Cash
    if(need > 0){ fCash = Math.min(cb, need); need -= fCash; }
    // 5) Roth as last resort
    if(need > 0){ fRoth = Math.min(rb, need); need -= fRoth; }
    // Roth conversion: fill bracket if enabled
    let rothConv=0;
    if(rothConversion && tb>0 && fRoth===0){
      // Evaluate multiple bracket fill options and choose the least incremental tax cost
      const _otherPC = nqdcInc + penInc + othInc + rmd + (fTax * xbGainFrac);
      const provAGI = taxableSSAmt(ssInc, _otherPC, filingStatus||'MFJ') + _otherPC;
      const evals = evalConversionOptions(provAGI, tb, filingStatus||'MFJ', mult);
      if(evals && evals.amount>0){
        rothConv = evals.amount;
        const convShare = tb > 0 ? evals.amount / tb : 0;
        const convFrom1 = Math.min(Math.max(0, tb1 - fTrad1 - extraTrad1), Math.round((tb1 - fTrad1 - extraTrad1) * convShare));
        const convFrom2 = Math.min(Math.max(0, tb2 - fTrad2 - extraTrad2), evals.amount - convFrom1);
        tb1 = Math.max(0, tb1 - convFrom1);
        tb2 = Math.max(0, tb2 - convFrom2);
        tb = tb1 + tb2;
        rb += rothConv;
        // record chosen bracket label for display
        var chosenConvLabel = evals.choice;
      } else {
        var chosenConvLabel = null;
      }
    }
    // Growth — basis stays the same (growth is unrealized gain, not basis)
    tb1 = Math.max(0, tb1 - fTrad1 - extraTrad1) * (1 + pr);
    tb2 = Math.max(0, tb2 - fTrad2 - extraTrad2) * (1 + pr);
    tb = tb1 + tb2;
    rb = Math.max(0, rb - fRoth) * (1 + pr);
    xb = Math.max(0, xb - fTax) * (1 + pr);
    // xbBasis unchanged by growth (investment returns increase the unrealized gain, not basis)
    hb = Math.max(0, hb - fHSA) * (1 + pr * 0.8);
    cb = Math.max(0, cb - fCash) * (1 + Math.min(inf * 0.9, 0.04));
    // tot is computed AFTER tax payment below — do not compute here
    // Final AGI (Pass 2) — taxable brokerage: only the gain portion (xbGainFrac) is taxable at LTCG rates
    const _otherAGI = nqdcInc + penInc + othInc + fTrad + (fTax * xbGainFrac) + fHSA + rothConv;
    const _tssAmt   = taxableSSAmt(ssInc, _otherAGI, filingStatus||'MFJ');
    const agi = _tssAmt + _otherAGI;
    // Federal tax computed using CPI-indexed brackets
    const fed = computeTaxVars(agi, filingStatus||'MFJ', mult);
    // State tax — some states don't tax Social Security; exclude SS from state AGI for those states
    const _stateData = STATE_TAX_DATA[RETIREMENT_STATE] || STATE_TAX_DATA.OTHER;
    const stateTaxableAGI = _stateData.taxesSS ? agi : agi - _tssAmt;
    const stateTax = computeStateTax(stateTaxableAGI, filingStatus||'MFJ', mult);
    const ltcgRate = agi<(LTCG_0[filingStatus||'MFJ']||96700)?0:agi<(LTCG_15[filingStatus||'MFJ']||583750)?0.15:0.20;
    // Pay taxes from portfolio — cash first (most liquid), then taxable, then traditional
    // This reflects that taxes are a real cash expense that must come from somewhere
    let taxDue = fed.tax + stateTax;
    const taxFromCash = Math.min(cb, taxDue); cb -= taxFromCash; taxDue -= taxFromCash;
    if(taxDue > 0 && xb > 0){
      const taxFromXb = Math.min(xb, taxDue);
      if(xb > 0) xbBasis = Math.max(0, xbBasis - taxFromXb * (xbBasis / xb));
      xb -= taxFromXb; taxDue -= taxFromXb;
    }
    if(taxDue > 0){
      const tbNow = tb1 + tb2;
      const taxFromTrad = Math.min(tbNow, taxDue);
      if(tbNow > 0){
        tb1 = Math.max(0, tb1 - taxFromTrad * (tb1 / tbNow));
        tb2 = Math.max(0, tb2 - taxFromTrad * (tb2 / tbNow));
        tb = tb1 + tb2;
      }
    }
    const tot = tb + rb + xb + cb + hb;
    // --- Validation checks (NaN, negatives, tax integrity) ---
    const warnings = [];
    const checkNums = {agi, federalTax:fed.tax, stateTax, tot, tb, rb, xb, cb, hb, fTrad, fTax, fRoth, fCash, fHSA, rmd, rothConv};
    for(const k in checkNums){ const v = checkNums[k]; if(!Number.isFinite(v) || isNaN(v)){ warnings.push(`${k} invalid: ${v}`); } }
    if(tb < -0.5) warnings.push(`Traditional balance negative: ${Math.round(tb)}`);
    if(rb < -0.5) warnings.push(`Roth balance negative: ${Math.round(rb)}`);
    if(xb < -0.5) warnings.push(`Taxable balance negative: ${Math.round(xb)}`);
    if(cb < -0.5) warnings.push(`Cash balance negative: ${Math.round(cb)}`);
    if(hb < -0.5) warnings.push(`HSA balance negative: ${Math.round(hb)}`);
    if(fed.tax < 0) warnings.push(`Federal tax negative: ${fed.tax}`);
    if(stateTax < 0) warnings.push(`State tax negative: ${stateTax}`);
    // (redundant federal recompute removed)
    if(warnings.length>0) console.warn('Year', year, 'warnings:', warnings);

    retireRows.push({year:year, age1:a1, age2,
      ssInc, penInc, othInc, nqdcInc, extInc, spending,
      portNeed, fTrad, fTax, fRoth, fCash, fHSA, rothConv,
      actual:fTrad+fTax+fRoth+fCash+fHSA,
      rmd, tb, rb, xb, cb, hb, tot,
      agi, margRate:fed.margRate, room:fed.room, irmaa:irmaaAdj, irmaaSurcharge, ltcgRate,
      federalTax:fed.tax, stateTax, provAGI: (()=>{ const _o=nqdcInc+penInc+othInc+rmd+fHSA; return taxableSSAmt(ssInc,_o,filingStatus||'MFJ')+_o; })(), convChoice: chosenConvLabel||null,
      warnings
    });
    // record MAGI history for IRMAA lookback
    magiHistory.push(agi);
    spending *= (1+inf);
    if(a1>lifeExp1 && (!hasSpouse||a2===null||a2>lifeExp2)) break;
  }

  return { accumRows, retireRows, retireStartBal, deterministicRetireStart, yearsToRetire, accumMC };
}

// ═══════════════════════════════════════════════════
//  CALCULATE
// ═══════════════════════════════════════════════════
function calculate(){
  const hs=$('hasSpouse').checked;
  const a1=calcAge($('bday1').value)||60;
  const a2=hs?(calcAge($('bday2').value)||58):a1;
  const filing=$('filingStatus')?.value||'MFJ';
  RETIREMENT_STATE = $('retirementState')?.value || RETIREMENT_STATE;
  const inp={
    name1:$('name1').value||'You', name2:$('name2').value||'Spouse',
    age1:a1, retAge1:int('retAge1')||65, lifeExp1:int('lifeExp1')||90,
    hasSpouse:hs,
    age2:a2, retAge2:hs?int('retAge2'):int('retAge1'), lifeExp2:hs?(int('lifeExp2')||92):(int('lifeExp1')||90),
    pia1:num('pia1'), ssAge1:int('ssAge1')||67,
    pia2:hs?num('pia2'):0, ssAge2:hs?(int('ssAge2')||67):67,
    pension1:num('pension1'), pension2:hs?num('pension2'):0, pensionAge:int('pensionAge')||65,
    otherIncome:num('otherIncome'), otherIncomeStopAge:int('otherIncomeStopAge')||999,
    annualSpending:num('annualSpending'), spendingReduceAge:int('spendingReduceAge')||0, reducedSpending:num('reducedSpending')||0,
    // Current balances (pre-accumulation)
    trad0:num('trad401k')+num('tradIRA')+(hs?num('spouse401k')+num('spouseTradIRA'):0),
    trad1:num('trad401k')+num('tradIRA'),
    trad2:hs?(num('spouse401k')+num('spouseTradIRA')):0,
    roth0:num('rothIRA')+num('roth401k')+(hs?num('spouseRothIRA')+num('spouseRoth401k'):0),
    roth1:num('rothIRA')+num('roth401k'),
    roth2:hs?(num('spouseRothIRA')+num('spouseRoth401k')):0,
    taxable0:num('taxable'), cash0:num('cash'), hsa0:num('hsa')+(hs?num('spouseHSA'):0),
    hsa1:num('hsa'), hsa2:hs?num('spouseHSA'):0,
    stockReturn:num('stockReturn')||7, bondReturn:num('bondReturn')||4,
    stockAlloc:int('stockPct')||60, inflationRate:num('inflationRate')||3,
    filingStatus:filing,
    retirementState:RETIREMENT_STATE,
    rothConversion:$('rothConversion').checked,
    goalYears:getGoalYrs(), swr:getSWR(),
    // Current income
    yourIncome:num('yourIncome'), spouseIncome:num('spouseIncome'),
    // Pre-retirement contributions
    contrib401k:$('contrib401k')?.value||'0',
    contribRoth:$('contribRoth')?.value||'0',
    contribTaxable:num('contribTaxable'),
    spouseContrib401k:hs?($('spouseContrib401k')?.value||'0'):'0',
    spouseContribRoth:hs?($('spouseContribRoth')?.value||'0'):'0',
    // NQDC
    hasNqdc:$('hasNqdc').checked,
    nqdcBalance:num('nqdcBalance'),
    nqdcDeferral:num('nqdcDeferral'),
    currentSalary:num('currentSalary'),
    nqdcDistType:$('nqdcDistType')?.value||'10yr',
    nqdcStartAge:int('nqdcStartAge')||65,
  };
  const proj=project(inp);
  // expose last projection + inputs for Monte Carlo analysis
  window._lastProj = proj;
  window._lastInp = inp;
  renderResults(proj,inp);
  sw('results');
}

// ═══════════════════════════════════════════════════
//  RENDER
// ═══════════════════════════════════════════════════
let aChart = null;

function renderResults(projResult, inp){
  $('res-placeholder').classList.add('hidden');
  $('res-content').classList.remove('hidden');
  const { accumRows, retireRows: proj, retireStartBal, deterministicRetireStart, yearsToRetire, accumMC } = projResult;
  const total = retireStartBal;
  const currentTotal = (inp.trad0||0)+(inp.roth0||0)+(inp.taxable0||0)+(inp.cash0||0)+(inp.hsa0||0);
  const debt=num('mortgage')+num('otherDebt');
  const swa=total*inp.swr;
  const dep=proj.find(r=>r.tot<=0);
  const last=proj[proj.length-1];

  // ── Accumulation summary ──────────────────────────────────
  const accumCard=$('accum-summary-card');
  if(accumCard) accumCard.style.display = yearsToRetire>0 ? '' : 'none';
  if(yearsToRetire>0 && accumRows.length>0){
    const lastAccum = accumRows[accumRows.length-1];
    const totalContribs = accumRows.reduce((s,r)=>s+(r.contrib||0),0);
    const growth = (deterministicRetireStart||total) - currentTotal - totalContribs;
    const p50bal = accumMC ? accumMC.finalP50 : (deterministicRetireStart||total);
    const p10bal = accumMC ? accumMC.finalP10 : null;
    const cols = accumMC ? 'repeat(4,1fr)' : 'repeat(3,1fr)';
    let asum=`<div style="display:grid;grid-template-columns:${cols};gap:.75rem;margin-bottom:.75rem;">
      <div style="background:#f0fdf4;border-radius:8px;padding:.75rem;text-align:center;border:1px solid #86efac;">
        <div style="font-size:.7rem;color:#166534;text-transform:uppercase;font-weight:600;">Today's Portfolio</div>
        <div style="font-size:1.3rem;font-weight:700;color:#166534;">${fmtK(currentTotal)}</div></div>
      <div style="background:#f0fdf4;border-radius:8px;padding:.75rem;text-align:center;border:1px solid #86efac;">
        <div style="font-size:.7rem;color:#166534;text-transform:uppercase;font-weight:600;">Total Contributions</div>
        <div style="font-size:1.3rem;font-weight:700;color:#166534;">${fmtK(totalContribs)}</div>
        <div style="font-size:.7rem;color:#166534;">over ${yearsToRetire} years</div></div>
      <div style="background:#dbeafe;border-radius:8px;padding:.75rem;text-align:center;border:1px solid #93c5fd;">
        <div style="font-size:.7rem;color:#1e40af;text-transform:uppercase;font-weight:600;">p50 Median at ${inp.retAge1} ★</div>
        <div style="font-size:1.3rem;font-weight:700;color:#1d4ed8;">${fmtK(p50bal)}</div>
        <div style="font-size:.7rem;color:#1e40af;">used for retirement phase</div></div>`;
    if(accumMC && p10bal!=null) asum+=`
      <div style="background:#fee2e2;border-radius:8px;padding:.75rem;text-align:center;border:1px solid #fca5a5;">
        <div style="font-size:.7rem;color:#991b1b;text-transform:uppercase;font-weight:600;">p10 Pessimistic at ${inp.retAge1}</div>
        <div style="font-size:1.3rem;font-weight:700;color:#b91c1c;">${fmtK(p10bal)}</div>
        <div style="font-size:.7rem;color:#991b1b;">10% chance worse than this</div></div>`;
    asum+=`</div>`;
    if(accumMC) asum+=`<div style="font-size:.75rem;color:#475569;background:#f8fafc;border-radius:6px;padding:.5rem .75rem;margin-bottom:.5rem;">Monte Carlo accumulation: 10,000 iterations · 12% volatility · seeded for reproducibility. The <strong>p50 median</strong> is used as your retirement starting balance.</div>`;
    $('accum-summary').innerHTML=asum;
    // Accumulation chart
    if(aChart) aChart.destroy();
    const actx=$('accumChart')?.getContext('2d');
    if(actx){
      const accumDatasets=[
        {label:'Deterministic',data:accumRows.map(r=>r.tot),borderColor:'#10b981',backgroundColor:'rgba(16,185,129,.10)',fill:true,tension:.3,borderWidth:2,pointRadius:2},
        {label:'Traditional',data:accumRows.map(r=>r.tb),borderColor:'#f59e0b',borderDash:[4,3],fill:false,tension:.3,borderWidth:1.5,pointRadius:0},
        {label:'Roth',data:accumRows.map(r=>r.rb),borderColor:'#22c55e',borderDash:[4,3],fill:false,tension:.3,borderWidth:1.5,pointRadius:0}
      ];
      if(accumMC){
        accumDatasets.push({label:'p50 Median',data:accumMC.p50,borderColor:'#3b82f6',backgroundColor:'rgba(59,130,246,.06)',fill:false,tension:.3,borderWidth:2,pointRadius:0});
        accumDatasets.push({label:'p10 Pessimistic',data:accumMC.p10,borderColor:'#ef4444',backgroundColor:'rgba(239,68,68,.04)',fill:false,tension:.3,borderWidth:1.5,pointRadius:0,borderDash:[5,4]});
      }
      aChart=new Chart(actx,{type:'line',data:{
        labels:accumRows.map(r=>`${r.year} (${r.age1})`),
        datasets:accumDatasets},options:{responsive:true,maintainAspectRatio:false,
          plugins:{legend:{position:'top',labels:{boxWidth:12,font:{size:11}}},
            tooltip:{callbacks:{label:c=>` ${c.dataset.label}: ${fmtK(c.raw)}`}}},
          scales:{y:{ticks:{callback:v=>fmtK(v),font:{size:10}},grid:{color:'#f1f5f9'}},
            x:{ticks:{maxRotation:45,font:{size:9},maxTicksLimit:12},grid:{display:false}}}}});
    }
  }

  // ── Working vs Retirement bracket comparison ──────────────
  const bcCard=$('bracket-compare-card');
  const workIncome=(inp.yourIncome||0)+(inp.spouseIncome||0);
  if(bcCard) bcCard.style.display = workIncome>0 && proj.length>0 ? '' : 'none';
  if(workIncome>0 && proj.length>0){
    const filing=inp.filingStatus||'MFJ';
    const wMarg=getMargRate(workIncome,filing);
    const yr0ret=proj[0];
    const rMarg=yr0ret.margRate;
    const diff=wMarg-rMarg;
    let advice, adviceColor;
    if(diff>0.05){
      advice=`✅ <strong>Strong case to defer Roth conversions.</strong> Your working bracket (${(wMarg*100).toFixed(0)}%) is higher than your projected retirement bracket (${(rMarg*100).toFixed(0)}%). Let tax-deferred money grow — convert Traditional→Roth <em>after</em> retiring, in the low-income window before SS and RMDs begin.`;
      adviceColor='#dcfce7';
    } else if(diff<-0.03){
      advice=`⚠️ <strong>Consider converting to Roth now.</strong> Your retirement bracket (${(rMarg*100).toFixed(0)}%) may be higher than your working bracket (${(wMarg*100).toFixed(0)}%) due to RMDs or SS income. Pay taxes at today's lower rate by doing Roth conversions while still employed.`;
      adviceColor='#fef3c7';
    } else {
      advice=`ℹ️ <strong>Brackets are similar.</strong> Working bracket (${(wMarg*100).toFixed(0)}%) ≈ projected retirement bracket (${(rMarg*100).toFixed(0)}%). Focus on maxing contributions; convert Roth opportunistically in any low-income years.`;
      adviceColor='#dbeafe';
    }
    $('bracket-compare').innerHTML=`
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:.75rem;margin-bottom:.9rem;">
        <div style="background:#fff;border-radius:8px;padding:.85rem;border:1px solid var(--bd);text-align:center;">
          <div style="font-size:.7rem;color:var(--tl);text-transform:uppercase;font-weight:600;">NOW — Working Bracket</div>
          <div style="font-size:1.6rem;font-weight:700;color:${wMarg<=0.22?'var(--ok)':'var(--dan)'};">${(wMarg*100).toFixed(0)}%</div>
          <div style="font-size:.75rem;color:var(--tl);">Household income ${fmt(workIncome)}</div>
        </div>
        <div style="background:#fff;border-radius:8px;padding:.85rem;border:1px solid var(--bd);text-align:center;">
          <div style="font-size:.7rem;color:var(--tl);text-transform:uppercase;font-weight:600;">RETIREMENT — Year 1 Bracket</div>
          <div style="font-size:1.6rem;font-weight:700;color:${rMarg<=0.22?'var(--ok)':'var(--dan)'};">${(rMarg*100).toFixed(0)}%</div>
          <div style="font-size:.75rem;color:var(--tl);">Est. AGI ${fmt(yr0ret.agi)} at age ${yr0ret.age1}</div>
        </div>
      </div>
      <div style="background:${adviceColor};border-radius:8px;padding:.85rem;font-size:.87rem;line-height:1.55;">${advice}</div>`;
  }

  // Metrics (now based on retirement-start portfolio)
  $('m-total').textContent=fmtK(total);
  $('m-total-s').textContent=debt>0?`Net worth after debt: ${fmtK(total-debt)}`:(yearsToRetire>0?`Projected from ${fmtK(currentTotal)} today`:'Net investable assets');
  $('m-swa').textContent=fmtK(swa);
  $('m-swa-s').textContent=`${(inp.swr*100).toFixed(1)}% SWR — need: ${fmtK(inp.annualSpending)}/yr`;
  const swc=$('m-swa-c'); swc.className='mc'+(swa>=inp.annualSpending?' green':swa>=inp.annualSpending*.85?' yellow':' red');
  $('m-lon').textContent=dep?'Age '+dep.age1:(inp.goalYears>=59?'Forever':last.age1+'+');
  $('m-lon-s').textContent=dep?'Portfolio depleted':'Portfolio survives your plan ✓';
  $('m-lon-c').className='mc'+(dep?' red':' green');
  // Alert
  const alEl=$('plan-alert');
  if(!dep&&swa>=inp.annualSpending){
    alEl.innerHTML=`<div class="al ok">✅ <strong>Your plan looks solid.</strong> Projected retirement portfolio of ${fmtK(total)} sustains spending for ${inp.goalYears>=59?'the long term':inp.goalYears+' years'} at a ${(inp.swr*100).toFixed(1)}% withdrawal rate.</div>`;
  } else if(!dep){
    alEl.innerHTML=`<div class="al warn">⚠️ <strong>Plan is workable but tight.</strong> Consider reducing spending by ${fmtK(inp.annualSpending-swa)}/yr, delaying SS to 70, or working a bit longer.</div>`;
  } else {
    alEl.innerHTML=`<div class="al err">🚨 <strong>Depletion risk at age ${dep.age1}.</strong> Reduce spending, delay SS to 70, or increase portfolio before retiring.</div>`;
  }
  // SS Summary
  const b1=ssBenefit(inp.pia1,inp.ssAge1);
  const b2=inp.hasSpouse?spouseBenefit(inp.pia2,inp.pia1,inp.ssAge2):0;
  let shtml=`<div class="ssp">
    <div class="ssr"><span>${inp.name1}'s SS (claiming age ${inp.ssAge1})</span><span>${fmt(b1)}/mo = ${fmt(b1*12)}/yr</span></div>`;
  if(inp.hasSpouse) shtml+=`<div class="ssr"><span>${inp.name2}'s SS (claiming age ${inp.ssAge2})</span><span>${fmt(b2)}/mo = ${fmt(b2*12)}/yr</span></div>`;
  shtml+=`<div class="ssr"><span>Total household SS income (at FRA)</span><span>${fmt((b1+b2)*12)}/yr</span></div></div>`;
  if(inp.ssAge1<70&&inp.pia1>0){
    const extra=(ssBenefit(inp.pia1,70)-b1)*12;
    shtml+=`<div class="al info" style="margin-top:.65rem;">💡 Delaying ${inp.name1}'s SS to 70 adds <strong>${fmtK(extra)}/yr</strong>. Break-even vs. age ${inp.ssAge1} is ~12–14 years after claiming.</div>`;
  }
  $('ss-sum').innerHTML=shtml;
  // Withdrawal order
  $('worder-disp').innerHTML=`<div style="margin-bottom:.9rem;">
    <p style="font-size:.82rem;color:var(--tl);margin-bottom:.4rem;">Optimal order (first → last):</p>
    <div class="worder">
      ${inp.hasNqdc?'<div class="woi" style="border-color:#6366f1;color:#4f46e5;">🏢 NQDC</div><span class="warr">→</span>':'' }
      <div class="woi tx">📈 Taxable</div><span class="warr">→</span>
      <div class="woi tr">🏦 Traditional 401k/IRA</div><span class="warr">→</span>
      <div class="woi ro">🌱 Roth</div>
    </div>
    <p style="font-size:.75rem;color:var(--tl);margin-top:.3rem;">RMDs from traditional accounts are taken first (mandatory at age 73).</p></div>`;

  // Tax bracket guidance (Year 1 of retirement)
  const yr0 = proj[0];
  if(yr0){
    const filing=inp.filingStatus||'MFJ';
    const agi0=yr0.agi;
    const marg0=yr0.margRate;
    const room0=yr0.room;
    const irmaa0=yr0.irmaa;
    const ltcg0=yr0.ltcgRate;
    const sd=STD_DED[filing]||30000;
    const ltcgThresh=LTCG_0[filing]||96700;
    const irmaaThresh=(IRMAA_TH[filing]||IRMAA_TH.MFJ)[0];
    let bhtml=`<div class="strat" style="background:linear-gradient(135deg,#faf5ff,#ede9fe);border-color:#a78bfa;">
      <h3 style="color:#4f46e5;">📊 Year 1 Tax Bracket Analysis (Age ${yr0.age1})</h3>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:.6rem;margin-bottom:.75rem;">
        <div style="background:#fff;border-radius:8px;padding:.7rem;border:1px solid #ddd8fe;">
          <div style="font-size:.7rem;color:#6b7280;text-transform:uppercase;letter-spacing:.05em;">Est. AGI</div>
          <div style="font-size:1.3rem;font-weight:700;color:#4f46e5;">${fmt(agi0)}</div>
          <div style="font-size:.72rem;color:#6b7280;">Std. deduction: ${fmt(sd)}</div>
        </div>
        <div style="background:#fff;border-radius:8px;padding:.7rem;border:1px solid #ddd8fe;">
          <div style="font-size:.7rem;color:#6b7280;text-transform:uppercase;letter-spacing:.05em;">Marginal Rate</div>
          <div style="font-size:1.3rem;font-weight:700;color:${marg0<=0.12?'var(--ok)':marg0<=0.22?'var(--acc)':'var(--dan)'};">${(marg0*100).toFixed(0)}%</div>
          <div style="font-size:.72rem;color:#6b7280;">Room left in bracket: ${fmt(room0)}</div>
        </div>
      </div>`;

    // Per-bucket guidance
    bhtml+=`<div class="si"><span class="ic">🏦</span><p><strong>Traditional 401k/IRA this year:</strong> Drawing <strong>${fmt(yr0.fTrad)}</strong>. `;
    if(room0>1000) bhtml+=`You have <strong>${fmt(room0)}</strong> of room left in the ${(marg0*100).toFixed(0)}% bracket — consider drawing additional ${fmt(Math.min(room0,total*0.05))} more for Roth conversion before bumping up.`;
    else bhtml+=`You are near the top of your tax bracket. Be careful — additional withdrawals will be taxed at <strong>${((marg0+0.02)*100).toFixed(0)}%+</strong>.`;
    bhtml+=`</p></div>`;

    bhtml+=`<div class="si"><span class="ic">📈</span><p><strong>Taxable account this year:</strong> Drawing <strong>${fmt(yr0.fTax)}</strong>. `;
    if(ltcg0===0) bhtml+=`✅ Your income qualifies for <strong>0% long-term capital gains tax</strong> (AGI below ${fmt(ltcgThresh)}). Harvest gains tax-free now.`;
    else bhtml+=`Capital gains rate: <strong>${(ltcg0*100).toFixed(0)}%</strong>. Consider tax-loss harvesting to offset.`;
    bhtml+=`</p></div>`;

    bhtml+=`<div class="si"><span class="ic">🌱</span><p><strong>Roth this year:</strong> Drawing <strong>${fmt(yr0.fRoth)}</strong>. `;
    if(yr0.fRoth===0) bhtml+=`None needed — good. Roth continues to grow tax-free.`;
    else bhtml+=`Only draw Roth when other buckets are exhausted. Every dollar left in Roth compounds tax-free.`;
    bhtml+=`</p></div>`;

    if(irmaa0>0) bhtml+=`<div class="si"><span class="ic">⚠️</span><p><strong>IRMAA Alert:</strong> Your estimated AGI of ${fmt(agi0)} triggers a <strong>Medicare Part B premium surcharge (Tier ${irmaa0})</strong>. IRMAA threshold for ${filing} starts at ${fmt(irmaaThresh)}. Consider reducing traditional withdrawals or shifting income to control your AGI.</p></div>`;
    else if(agi0>irmaaThresh*0.85) bhtml+=`<div class="si"><span class="ic">💡</span><p><strong>IRMAA Watch:</strong> You are ${fmt(irmaaThresh-agi0)} away from the Medicare IRMAA surcharge threshold (${fmt(irmaaThresh)} for ${filing}). Keep AGI below this to avoid extra Part B premiums.</p></div>`;

    bhtml+=`</div>`;
    $('bracket-guidance').innerHTML=bhtml;
  }
  // Strategy
  const projTrad = proj.length>0 ? proj[0].tb : total;
  const projRoth = proj.length>0 ? proj[0].rb : 0;
  const projHSA  = proj.length>0 ? proj[0].hb : 0;
  const yearsToRmd = Math.max(0, 73-(inp.retAge1));
  const yearsToSS  = Math.max(0, inp.ssAge1-inp.retAge1);
  let sh=`<div class="strat"><h3>Your Personalized Strategy</h3>`;
  if(yearsToRetire>0){
    const accumSummary = accumMC
      ? `Median (p50): <strong>${fmtK(accumMC.finalP50)}</strong> · Pessimistic (p10): <strong>${fmtK(accumMC.finalP10)}</strong>. Retirement phase uses the median.`
      : `Projected: <strong>${fmtK(total)}</strong>.`;
    sh+=`<div class="si"><span class="ic">⏳</span><p><strong>Accumulation phase (${yearsToRetire} years, 10K Monte Carlo):</strong> Portfolio grows from <strong>${fmtK(currentTotal)}</strong> today. ${accumSummary}</p></div>`;
  }
  if(inp.rothConversion && projTrad>0 && yearsToRmd>3) sh+=`<div class="si"><span class="ic">🔄</span><p><strong>Roth Conversion Window:</strong> ~${Math.min(yearsToRmd,yearsToSS)} years before SS + RMDs compress your bracket. Convert <strong>${fmtK(Math.min(projTrad*.1,30000))}–${fmtK(Math.min(projTrad*.2,60000))}/yr</strong> Traditional→Roth to reduce future taxable RMDs.</p></div>`;
  if(inp.ssAge1<70) sh+=`<div class="si"><span class="ic">🏛️</span><p><strong>SS timing:</strong> Claiming at ${inp.ssAge1}. If you have good health and other income to bridge the gap, delaying to 70 adds ~8%/yr and maximizes lifetime benefits — and survivor protection for spouse.</p></div>`;
  if(projTrad>0) sh+=`<div class="si"><span class="ic">📅</span><p><strong>RMDs start at 73:</strong> Projected ${fmtK(projTrad)} in traditional accounts at retirement — first RMD ≈ <strong>${fmtK(projTrad/26.5)}/yr</strong>. Withdraw strategically to manage future bracket.</p></div>`;
  sh+=`<div class="si"><span class="ic">🌱</span><p><strong>Roth last:</strong> Projected Roth balance ${fmtK(projRoth)} at retirement — draw last. Tax-free growth, no RMDs, best for legacy or late-life expenses.</p></div>`;
  if(projHSA>0) sh+=`<div class="si"><span class="ic">🏥</span><p><strong>HSA (${fmtK(projHSA)} at retirement):</strong> Reserve for medical costs — triple tax-free. After 65, any use is taxed like a 401(k), so save it for healthcare in your 70s–80s.</p></div>`;
  sh+=`</div>`;
  $('strat-content').innerHTML=sh;
  // NQDC Advisor
  renderNqdcAdvice(inp, proj);
  renderPortChart(accumRows, proj, inp);
  renderIncChart(proj, inp);
  renderTable(accumRows, proj, inp);
}

function renderNqdcAdvice(inp, proj) {
  const card=$('nqdc-advice-card');
  const el=$('nqdc-advice');
  if(!card||!el) return;

  card.classList.remove('hidden');

  const filing=inp.filingStatus||'MFJ';
  const salary=inp.currentSalary||0;
  // IRS 401(a)(17) compensation limit for 2025
  const irsCompLimit=345000;

  let html='';

  if(!inp.hasNqdc){
    // Participation guidance for someone not yet enrolled
    html+=`<div class="al" style="background:#ede9fe;color:#3730a3;border:1px solid #a5b4fc;">
      <strong>You are not currently enrolled in an NQDC plan.</strong> Here's whether it makes strategic sense for you.
    </div>`;

    if(salary>0){
      const currentMarg=getMargRate(salary,filing);
      html+=`<div class="strat" style="background:linear-gradient(135deg,#faf5ff,#ede9fe);border-color:#a78bfa;">
        <h3 style="color:#4f46e5;">Should You Participate in an NQDC Plan?</h3>
        <div class="si"><span class="ic">📉</span><p><strong>Tax deferral benefit:</strong> Deferring salary or bonus reduces your taxable income today. At your current <strong>${(currentMarg*100).toFixed(0)}% marginal rate</strong>, each $10,000 deferred saves <strong>${fmt(10000*currentMarg)}</strong> in federal taxes now — you pay ordinary income tax when distributed in retirement instead.</p></div>
        <div class="si"><span class="ic">✅</span><p><strong>Best case for NQDC:</strong> Your current bracket (${(currentMarg*100).toFixed(0)}%) is meaningfully higher than your projected retirement bracket. The wider the gap, the more valuable deferral becomes.</p></div>
        <div class="si"><span class="ic">📅</span><p><strong>If you enroll:</strong> Elect <strong>10-year installments</strong> starting at retirement. This spreads distributions over a decade, keeping annual ordinary income lower and reducing IRMAA Medicare surcharge risk.</p></div>
        <div class="si"><span class="ic">⚠️</span><p><strong>Key risk — unsecured liability:</strong> NQDC balances are a general unsecured obligation of your employer. If your employer becomes insolvent, you become an unsecured creditor. Size your NQDC participation relative to your confidence in your employer's long-term financial health.</p></div>
        <div class="si"><span class="ic">💡</span><p><strong>Max tax-advantaged accounts first:</strong> Always max 401(k) and HSA before deferring into NQDC, since those have ERISA protections and are shielded from employer creditors.</p></div>
      </div>`;
    } else {
      html+=`<div class="al info">Enter your <strong>Current Annual Salary</strong> in the Accounts → NQDC section to get a personalized participation recommendation.</div>`;
    }
  } else {
    // Currently enrolled — show distribution analysis
    const distYrs=nqdcYears(inp.nqdcDistType);
    const annualDist=inp.nqdcDistType==='lump'?inp.nqdcBalance:inp.nqdcBalance/distYrs;
    // Find first year with NQDC distribution in projection
    const nqdcRows=proj.filter(r=>r.nqdcInc>0);

    html+=`<div class="strat" style="background:linear-gradient(135deg,#faf5ff,#ede9fe);border-color:#a78bfa;">
      <h3 style="color:#4f46e5;">NQDC Distribution Plan</h3>
      <div class="si"><span class="ic">💰</span><p><strong>Balance:</strong> ${fmt(inp.nqdcBalance)} | <strong>Schedule:</strong> ${inp.nqdcDistType==='lump'?'Lump Sum':inp.nqdcDistType+' installments'} starting at age ${inp.nqdcStartAge} | <strong>Annual payout:</strong> ${fmt(annualDist)}/yr for ${distYrs===1?'1 year (lump)':distYrs+' years'}</p></div>`;

    if(nqdcRows.length>0){
      const firstNqdc=nqdcRows[0];
      const agiWithNqdc=firstNqdc.agi;
      const margWithNqdc=getMargRate(agiWithNqdc,filing);
      const irmaaWithNqdc=getIRMAA(agiWithNqdc,filing);
      html+=`<div class="si"><span class="ic">📊</span><p><strong>Distribution year ${firstNqdc.year} (Age ${firstNqdc.age1}):</strong> NQDC adds <strong>${fmt(annualDist)}/yr</strong> of ordinary income, pushing estimated AGI to <strong>${fmt(agiWithNqdc)}</strong> at the <strong>${(margWithNqdc*100).toFixed(0)}% bracket</strong>.`;
      if(irmaaWithNqdc>0) html+=` ⚠️ This triggers <strong>IRMAA Medicare surcharge Tier ${irmaaWithNqdc}</strong>.`;
      html+=`</p></div>`;
    }

    // Distribution timing advice
    if(inp.nqdcDistType==='lump'){
      html+=`<div class="si"><span class="ic">⚠️</span><p><strong>Lump sum warning:</strong> Taking the full ${fmt(inp.nqdcBalance)} in one year will very likely push you into the top tax bracket. Consider switching to 10-year installments if your election window is still open — this could save <em>tens of thousands</em> in taxes.</p></div>`;
    }

    html+=`<div class="si"><span class="ic">💡</span><p><strong>Timing strategy:</strong> Coordinate NQDC distributions with your SS claiming age and Roth conversions. Drawing NQDC during the <em>gap years</em> before SS starts keeps AGI lower and maximizes bracket-filling opportunities. For ${filing} filers, IRMAA kicks in at ${fmt((IRMAA_TH[filing]||IRMAA_TH.MFJ)[0])} — structure payouts to stay below that threshold.</p></div>`;

    html+=`<div class="si"><span class="ic">⚠️</span><p><strong>Unsecured liability reminder:</strong> Your NQDC balance is a general unsecured obligation of your employer. Monitor your employer's financial health and consider whether your total exposure is within your risk tolerance.</p></div>`;

    html+=`</div>`;
  }

  el.innerHTML=html;
}

function renderPortChart(accumRows, retireRows, inp){
  if(pChart) pChart.destroy();
  const ctx=$('portChart').getContext('2d');
  const allRows = [...accumRows, ...retireRows];
  const labels  = allRows.map(r=>`${r.year} (${r.age1})`);
  // Vertical line annotation at retirement
  const retireIdx = accumRows.length;
  pChart=new Chart(ctx,{type:'line',data:{labels,datasets:[
    {label:'Total Portfolio',
      data:allRows.map(r=>Math.max(0,r.tot)),
      borderColor:allRows.map((_,i)=>i<retireIdx?'#10b981':'#0d9488'),
      segment:{borderColor:ctx2=>ctx2.p0DataIndex<retireIdx?'#10b981':'#0d9488',
        backgroundColor:ctx2=>ctx2.p0DataIndex<retireIdx?'rgba(16,185,129,.08)':'rgba(13,148,136,.08)'},
      backgroundColor:'rgba(13,148,136,.08)',fill:true,tension:.3,borderWidth:2.5,pointRadius:1.5},
    {label:'Traditional',data:allRows.map(r=>Math.max(0,r.tb)),borderColor:'#f59e0b',borderDash:[4,3],fill:false,tension:.3,borderWidth:1.5,pointRadius:0},
    {label:'Roth',data:allRows.map(r=>Math.max(0,r.rb)),borderColor:'#22c55e',borderDash:[4,3],fill:false,tension:.3,borderWidth:1.5,pointRadius:0},
    {label:'Taxable+Cash',data:allRows.map(r=>Math.max(0,r.xb+r.cb)),borderColor:'#60a5fa',borderDash:[4,3],fill:false,tension:.3,borderWidth:1.5,pointRadius:0}
  ]},options:{responsive:true,maintainAspectRatio:false,
    plugins:{legend:{position:'top',labels:{boxWidth:12,font:{size:11}}},
      tooltip:{callbacks:{
        label:c=>` ${c.dataset.label}: ${fmtK(c.raw)}`,
        afterTitle:tips=>{const i=tips[0]?.dataIndex; return i===retireIdx?['── RETIREMENT START ──']:[];}
      }}},
    scales:{y:{ticks:{callback:v=>fmtK(v),font:{size:10}},grid:{color:'#f1f5f9'}},
      x:{ticks:{maxRotation:45,font:{size:9},maxTicksLimit:18},grid:{color:ctx2=>{
        const tick=ctx2.tick?.value; return tick===retireIdx?'rgba(239,68,68,.35)':'#f1f5f9';
      }}}}}});
}

function renderIncChart(proj,inp){
  if(iChart) iChart.destroy();
  const ctx=$('incChart').getContext('2d');
  const sl=proj.slice(0,Math.min(35,proj.length));
  iChart=new Chart(ctx,{type:'bar',data:{labels:sl.map(r=>r.age1),datasets:[
    {label:'Social Security',data:sl.map(r=>r.ssInc),backgroundColor:'#0d9488',stack:'s'},
    {label:'Pension / Other',data:sl.map(r=>r.penInc+r.othInc),backgroundColor:'#5eead4',stack:'s'},
    {label:'NQDC',data:sl.map(r=>r.nqdcInc||0),backgroundColor:'#818cf8',stack:'s'},
    {label:'Portfolio Draw',data:sl.map(r=>r.actual),backgroundColor:'#fcd34d',stack:'s'}
  ]},options:{responsive:true,maintainAspectRatio:false,
    plugins:{legend:{position:'top',labels:{boxWidth:12,font:{size:11}}},tooltip:{callbacks:{label:c=>` ${c.dataset.label}: ${fmtK(c.raw)}`}}},
    scales:{x:{stacked:true,title:{display:true,text:'Age',font:{size:11}},ticks:{font:{size:10}}},
      y:{stacked:true,ticks:{callback:v=>fmtK(v),font:{size:10}},grid:{color:'#f1f5f9'}}}}});
}

// ═══════════════════════════════════════════════════
//  MONTE CARLO
// ═══════════════════════════════════════════════════
let mcChart = null;
function randNormal(mean=0, sd=1){
  // Box-Muller
  let u=0,v=0; while(u===0) u=Math.random(); while(v===0) v=Math.random();
  const z=Math.sqrt(-2.0*Math.log(u))*Math.cos(2*Math.PI*v);
  return z*sd + mean;
}
function mulberry32(seed){
  return function(){
    seed |= 0; seed = seed + 0x6D2B79F5 | 0;
    let t = Math.imul(seed ^ seed >>> 15, 1 | seed);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}
function runAccumMonteCarlo(inp, accumRows){
  const ITER = 10000;
  const pr   = (inp.stockAlloc/100)*(inp.stockReturn/100) + ((100-inp.stockAlloc)/100)*(inp.bondReturn/100);
  const sd   = (parseFloat(inp.mcAccumVol)||12) / 100;
  const years = accumRows.length;
  if(years === 0) return null;
  const rng = mulberry32(20260416);
  function seededNormal(mean, stddev){
    let u, v;
    do { u = rng(); } while(u === 0);
    do { v = rng(); } while(v === 0);
    return Math.sqrt(-2*Math.log(u))*Math.cos(2*Math.PI*v)*stddev + mean;
  }
  // Collect total portfolio value at end of each year across all iterations
  const yearEnds = Array.from({length: years}, () => []);
  for(let it=0; it<ITER; it++){
    let tb = (inp.trad1||0)+(inp.trad2||0);
    let rb = inp.roth0||0;
    let xb = inp.taxable0||0;
    let cb = inp.cash0||0;
    let hb = inp.hsa0||0;
    for(let yr=0; yr<years; yr++){
      const row = accumRows[yr];
      const r = seededNormal(pr, sd);
      const contrib = row.contrib||0;
      // Scale buckets proportionally using deterministic ratios from this year's row
      const prevTot = tb+rb+xb+cb+hb;
      const newTot = (prevTot + contrib) * (1 + r);
      const base = row.tot || 1;
      tb = (row.tb/base)*newTot;
      rb = (row.rb/base)*newTot;
      xb = (row.xb/base)*newTot;
      cb = (row.cb/base)*newTot;
      hb = (row.hb/base)*newTot;
      yearEnds[yr].push(newTot);
    }
  }
  const p10=[]; const p50=[];
  for(let yr=0; yr<years; yr++){
    const sorted = yearEnds[yr].slice().sort((a,b)=>a-b);
    p10.push(sorted[Math.floor(0.10*(ITER-1))]);
    p50.push(sorted[Math.floor(0.50*(ITER-1))]);
  }
  return { p10, p50, finalP50: p50[p50.length-1], finalP10: p10[p10.length-1] };
}

function runMonteCarlo(){
  const last = window._lastProj; const inp = window._lastInp;
  if(!last || !inp){ alert('Run Calculate first to produce a deterministic plan.'); return; }
  const iterations = parseInt($('mcIterations').value)||1000;
  const volPct = parseFloat($('mcVol').value)||12;
  const retireRows = last.retireRows;
  const startBal = last.retireStartBal || 0;
  if(!retireRows || retireRows.length===0){ alert('No retirement projection available for Monte Carlo.'); return; }

  // mean return per year (nominal) used in deterministic projection
  const mean = ( (inp.stockAlloc/100)*(inp.stockReturn/100) + ((100-inp.stockAlloc)/100)*(inp.bondReturn/100) );
  const sd = volPct/100;
  const years = retireRows.length;

  const survivalCounts = new Array(years).fill(0);
  const ending = [];

  for(let it=0; it<iterations; it++){
    let bal = startBal;
    let survived = true;
    for(let y=0;y<years;y++){
      const r = randNormal(mean, sd);
      bal = bal * (1 + r);
      const draw = Math.max(0, retireRows[y].actual || 0);
      bal -= draw;
      if(bal>0) survivalCounts[y]++;
      if(bal<=0){ survived = false; // portfolio depleted this year
        // mark remaining years as zero survival (counts not incremented)
        break;
      }
    }
    ending.push(Math.max(0, bal));
  }

  // Compute survival percent over time and percentiles of ending wealth
  const survivalPct = survivalCounts.map(c=> (c/iterations)*100 );
  ending.sort((a,b)=>a-b);
  const pct = (p)=> ending[Math.floor((p/100)*(ending.length-1))] || 0;
  const p10 = pct(10), p50 = pct(50), p90 = pct(90);

  // ages must be defined before summary text references it
  const ages = retireRows.map(r=>r.age1);

  // Summary
  const survProb = survivalPct[survivalPct.length-1]||0;
  const zoneColor = survProb>=80?'#166534':survProb>=50?'#854d0e':'#991b1b';
  const zoneBg    = survProb>=80?'#dcfce7':survProb>=50?'#fef9c3':'#fee2e2';
  const zoneTxt   = survProb>=80?'Safe Zone ✅':survProb>=50?'Caution Zone ⚠️':'At Risk Zone 🔴';
  $('mcSummary').innerHTML = `
    <div style="background:${zoneBg};border:1.5px solid;border-color:${zoneColor}40;border-radius:10px;padding:.8rem 1rem;margin-bottom:.7rem;">
      <div style="font-size:.8rem;color:${zoneColor};font-weight:600;">${zoneTxt}</div>
      <div style="font-size:1.5rem;font-weight:800;color:${zoneColor};">${survProb.toFixed(1)}% survival probability</div>
      <div style="font-size:.78rem;color:var(--tl);margin-top:.2rem;">${Math.round(survProb/100*iterations)} of ${iterations} simulations still solvent at the end of your projection (age ${ages[ages.length-1]||'?'})</div>
    </div>
    <div style="margin-top:.5rem;display:grid;grid-template-columns:repeat(3,1fr);gap:.5rem;">
      <div style="background:#fff;border-radius:8px;padding:.6rem;border:1px solid var(--bd);text-align:center;">
        <div style="font-size:.72rem;color:var(--tl);">10th percentile ending wealth</div>
        <div style="font-size:1.1rem;font-weight:700;color:#ef4444;">${fmtK(p10)}</div>
        <div style="font-size:.68rem;color:var(--tl);">Bad luck scenario</div>
      </div>
      <div style="background:#fff;border-radius:8px;padding:.6rem;border:1px solid var(--bd);text-align:center;">
        <div style="font-size:.72rem;color:var(--tl);">Median ending wealth</div>
        <div style="font-size:1.1rem;font-weight:700;color:#4f46e5;">${fmtK(p50)}</div>
        <div style="font-size:.68rem;color:var(--tl);">50th percentile</div>
      </div>
      <div style="background:#fff;border-radius:8px;padding:.6rem;border:1px solid var(--bd);text-align:center;">
        <div style="font-size:.72rem;color:var(--tl);">90th percentile ending wealth</div>
        <div style="font-size:1.1rem;font-weight:700;color:#166534;">${fmtK(p90)}</div>
        <div style="font-size:.68rem;color:var(--tl);">Favorable scenario</div>
      </div>
    </div>`;

  // Render survival over age chart with color-zone background bands
  if(mcChart) mcChart.destroy();
  const ctx = $('mcChart')?.getContext('2d');
  if(ctx){
    const zoneBgPlugin = {
      id:'zoneBg',
      beforeDraw(chart){
        const {ctx:c,chartArea:{top,bottom,left,right},scales:{y}} = chart;
        if(!y) return;
        const toY = p => y.getPixelForValue(p);
        c.save();
        c.fillStyle='rgba(220,252,231,0.45)'; c.fillRect(left,toY(100),right-left,toY(80)-toY(100));
        c.fillStyle='rgba(254,249,195,0.55)'; c.fillRect(left,toY(80),right-left,toY(50)-toY(80));
        c.fillStyle='rgba(254,226,226,0.45)'; c.fillRect(left,toY(50),right-left,toY(0)-toY(50));
        c.restore();
      }
    };
    mcChart = new Chart(ctx, {
      type: 'line',
      data: {
        labels: ages,
        datasets: [{ label: 'Survival %', data: survivalPct, borderColor: '#ff6b6b', backgroundColor:'rgba(255,107,107,0.12)', fill:true, tension:.25, pointRadius:0, borderWidth:2 }]
      },
      options:{
        responsive:true, maintainAspectRatio:false,
        scales:{
          y:{min:0,max:100,ticks:{callback:v=>v+'%'},grid:{color:'rgba(0,0,0,.05)'}},
          x:{grid:{display:false}}
        },
        plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>`${c.parsed.y.toFixed(1)}% of simulations solvent`}}}
      },
      plugins:[zoneBgPlugin]
    });
  }
}

function rerenderTable(){
  const last = window._lastProj;
  if(!last || !window._lastInp) return;
  renderTable(last.accumRows||[], last.retireRows||[], window._lastInp);
}

function renderTable(accumRows, retireRows, inp){
  const tbody=$('tbl-body'); tbody.innerHTML='';
  // Real-dollars toggle: deflate nominal values back to today's purchasing power
  const useReal = $('realDollarsToggle')?.checked || false;
  const startYr  = new Date().getFullYear();
  const inf      = (inp.inflationRate||3)/100;
  // Helper: deflate a nominal value in simulation year `simYear` back to today's dollars
  const deflate  = (v, simYear) => useReal ? v / Math.pow(1+inf, simYear - startYr) : v;
  const fmtD     = (v, yr) => fmt(deflate(v, yr));
  const fmtKD    = (v, yr) => fmtK(deflate(v, yr));

  // Update note banner
  const noteEl = $('tbl-dollar-note');
  if(noteEl){
    if(useReal){
      noteEl.style.display='block';
      // Find first retirement year SS to show the conversion
      const yr0 = retireRows.find(r=>r.ssInc>0);
      const ssNote = yr0 ? ` For example, SS income of ${fmtK(yr0.ssInc)} nominal in year ${yr0.year} equals ${fmtK(deflate(yr0.ssInc, yr0.year))} in today's dollars — matching your ssa.gov estimate.` : '';
      noteEl.innerHTML = `📅 <strong>Showing in today's dollars</strong> (all nominal amounts divided by cumulative inflation). This matches what ssa.gov shows for your SS benefit.${ssNote}`;
    } else {
      noteEl.style.display='block';
      noteEl.innerHTML = `💡 <strong>Showing nominal (future) dollars.</strong> All amounts reflect actual dollar amounts in that future year after inflation. ssa.gov shows SS benefits in <em>today's dollars</em> — toggle above to see comparable values. Your SS check in nominal dollars will be larger than ssa.gov shows because of annual COLA increases.`;
    }
  }

  // Update table header to have "Phase" column
  const thead=document.querySelector('#tbl-wrap thead tr');
  if(thead && accumRows.length>0 && !thead.querySelector('.ph-col')){
    const th=document.createElement('th'); th.className='ph-col'; th.textContent='Phase';
    thead.insertBefore(th, thead.firstChild);
  }
  const initTot = (inp.trad0||0)+(inp.roth0||0)+(inp.taxable0||0)+(inp.cash0||0)+(inp.hsa0||0);

  // Accumulation rows
  if(accumRows.length>0){
    accumRows.forEach((r,i)=>{
      const tr=document.createElement('tr');
      tr.style.background=i%2===0?'#f0fdf4':'#dcfce7';
      const age=inp.hasSpouse?`${r.age1}/${r.age2}`:r.age1;
      const phaseCell=accumRows.length>0?`<td style="color:#047857;font-weight:600;font-size:.75rem;">ACCUM</td>`:'';
      tr.innerHTML=`${phaseCell}<td>${r.year}</td><td>${age}</td>
        <td>—</td><td>—</td><td>Contrib: ${fmtK(r.contrib||0)}</td>
        <td>—</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td>
        <td><strong>${fmtK(r.tot)}</strong></td>`;
      tbody.appendChild(tr);
    });
    // Separator row
    const sep=document.createElement('tr');
    const colCount=accumRows.length>0?15:14;
    sep.innerHTML=`<td colspan="${colCount}" style="background:#064e3b;color:#a7f3d0;font-size:.75rem;font-weight:700;text-align:center;padding:.4rem;">▼ RETIREMENT STARTS — Age ${inp.retAge1} ▼</td>`;
    tbody.appendChild(sep);
  }

  // Retirement rows
  const retTot = retireRows.length>0 ? retireRows[0].tot+retireRows[0].actual : 0;
  retireRows.forEach((r,i)=>{
    const tr=document.createElement('tr');
    const pct=r.tot/(retTot||1);
    if(r.tot<=0) tr.className='dep';
    else if(pct<0.25) tr.className='low';
    else if(i===0) tr.className='hl';
    const age=inp.hasSpouse?`${r.age1}/${r.age2}`:r.age1;
    const bracketColor=r.margRate<=0.12?'#166534':r.margRate<=0.22?'#92400e':'#991b1b';
    const irmaaCell=r.irmaa>0?`<span style="color:#991b1b;font-weight:600;">Tier ${r.irmaa}</span>`:'—';
    const phaseCell=accumRows.length>0?`<td style="color:#6b7280;font-size:.75rem;">RETIRE</td>`:'';
    const rothConvCell=r.rothConv>0?`<span style="color:#7c3aed;font-size:.72rem;">+${fmtKD(r.rothConv,r.year)} conv</span>`:'';
    const convChoiceCell=r.convChoice?` <div style="font-size:.72rem;color:#7c3aed;">(${r.convChoice})</div>`:'';
    const stateTaxCell = r.stateTax>0 ? `<span style="color:#7c3aed;">${fmtD(r.stateTax,r.year)}</span>` : '—';
    tr.innerHTML=`${phaseCell}<td>${r.year}</td><td>${age}</td><td>${fmtD(r.ssInc,r.year)}</td>
      <td>${r.nqdcInc>0?`<strong style="color:#4f46e5;">${fmtD(r.nqdcInc,r.year)}</strong>`:'—'}</td>
      <td>${fmtD(r.penInc+r.othInc,r.year)}</td><td>${fmtD(r.actual,r.year)}</td><td>${fmtD(r.fTrad,r.year)}${rothConvCell}</td>
      <td>${fmtD(r.fRoth,r.year)}${convChoiceCell}</td><td>${r.rmd>0?fmtD(r.rmd,r.year):'—'}</td>
      <td><strong style="color:${bracketColor};">${(r.margRate*100).toFixed(0)}%</strong></td>
      <td>${fmtD(r.federalTax,r.year)}</td>
      <td>${stateTaxCell}</td>
      <td>${irmaaCell}</td>
      <td><strong>${fmtKD(r.tot,r.year)}</strong></td>`;
    tbody.appendChild(tr);
  });
}

function toggleTbl(){ $('tbl-wrap').classList.toggle('hidden'); }

function exportTableToCSV(){
  const table = document.querySelector('#tbl-wrap table');
  if(!table) return;
  const rows = Array.from(table.querySelectorAll('tr'));
  const csvLines = rows.map(tr =>
    Array.from(tr.querySelectorAll('th,td')).map(cell => {
      const val = cell.textContent.replace(/[\r\n]+/g,' ').trim();
      return '"' + val.replace(/"/g,'""') + '"';
    }).join(',')
  );
  const csv = csvLines.join('\r\n');
  const blob = new Blob([csv], {type:'text/csv;charset=utf-8;'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'retirement_projection.csv';
  a.click();
  URL.revokeObjectURL(url);
}

// ═══════════════════════════════════════════════════
//  APPLY CONFIG FROM PYTHON
// ═══════════════════════════════════════════════════
function applyConfig(c){
  const sv=(id,v)=>{ const e=$(id); if(e&&v!==undefined&&v!==null&&v!=='') e.value=v; };
  const sc=(id,v)=>{ const e=$(id); if(e) e.checked=!!v; };
  sv('name1',c.name1); sv('bday1',c.birthday1); sv('retAge1',c.retAge1); sv('lifeExp1',c.lifeExp1);
  sc('hasSpouse',c.hasSpouse);
  sv('name2',c.name2); sv('bday2',c.birthday2); sv('retAge2',c.retAge2); sv('lifeExp2',c.lifeExp2);
  updAge('bday1','agehint1'); updAge('bday2','agehint2');
  sv('pia1',c.pia1); sv('ssAge1',c.ssAge1); sv('pia2',c.pia2); sv('ssAge2',c.ssAge2);
  sv('pension1',c.pension1); sv('pension2',c.pension2); sv('pensionAge',c.pensionAge);
  sv('yourIncome',c.yourIncome); sv('spouseIncome',c.spouseIncome);
  sv('otherIncome',c.otherIncome);
  if(c.otherIncomeStopAge&&c.otherIncomeStopAge<999) sv('otherIncomeStopAge',c.otherIncomeStopAge);
  // Contributions
  if($('contrib401k')) $('contrib401k').value=c.contrib401k||'max';
  if($('contribRoth'))  $('contribRoth').value=c.contribRoth||'max';
  sv('contribTaxable',c.contribTaxable);
  if($('spouseContrib401k')) $('spouseContrib401k').value=c.spouseContrib401k||'0';
  if($('spouseContribRoth'))  $('spouseContribRoth').value=c.spouseContribRoth||'0';
  sv('trad401k',c.trad401k); sv('tradIRA',c.tradIRA); sv('roth401k',c.roth401k); sv('rothIRA',c.rothIRA);
  sv('hsa',c.hsa); sv('taxable',c.taxable); sv('cash',c.cash);
  sv('homeEquity',c.homeEquity); sv('mortgage',c.mortgage); sv('otherDebt',c.otherDebt);
  sv('spouse401k',c.spouse401k); sv('spouseTradIRA',c.spouseTradIRA);
  sv('spouseRothIRA',c.spouseRothIRA); sv('spouseRoth401k',c.spouseRoth401k); sv('spouseHSA',c.spouseHSA);
  sv('annualSpending',c.annualSpending);
  if(c.spendingReduceAge) sv('spendingReduceAge',c.spendingReduceAge);
  if(c.reducedSpending) sv('reducedSpending',c.reducedSpending);
  // Goal type
  const gt=$('goalType');
  if(gt){
    const opts=Array.from(gt.options).map(o=>o.value);
    const g=String(c.goalType);
    if(opts.includes(g)) gt.value=g;
    else if(!isNaN(parseFloat(g))){ gt.value='custom'; sv('customGoalYears',parseFloat(g)); }
  }
  sv('stockPct',c.stockAlloc); sv('stockReturn',c.stockReturn);
  sv('bondReturn',c.bondReturn); sv('inflationRate',c.inflationRate);
  if($('filingStatus')) $('filingStatus').value=c.filingStatus||'MFJ';
  if($('retirementState') && c.retirementState){ $('retirementState').value=c.retirementState; RETIREMENT_STATE=c.retirementState; }
  sc('rothConversion',c.rothConversion);
  // NQDC
  sc('hasNqdc',c.hasNqdc);
  sv('nqdcBalance',c.nqdcBalance); sv('nqdcDeferral',c.nqdcDeferral);
  sv('currentSalary',c.currentSalary);
  if($('nqdcDistType')) $('nqdcDistType').value=c.nqdcDistType||'10yr';
  sv('nqdcStartAge',c.nqdcStartAge);
  toggleNqdc();
  // Load tax brackets from config if provided
  if(c.tax_brackets){
    const tb=c.tax_brackets;
    if(tb.mfj)    BRACKETS.MFJ    = tb.mfj;
    if(tb.single) BRACKETS.Single = tb.single;
    if(tb.std_ded_mfj)    STD_DED.MFJ    = tb.std_ded_mfj;
    if(tb.std_ded_single) STD_DED.Single = tb.std_ded_single;
    if(tb.ltcg_0_mfj)    LTCG_0.MFJ    = tb.ltcg_0_mfj;
    if(tb.ltcg_0_single) LTCG_0.Single = tb.ltcg_0_single;
  }
  populateBracketTable();
  // Trigger UI
  toggleSpouse(); updateSS(); updateSWR(); updAlloc(); updateIncomeHint();
  // Highlight matching preset
  const s=c.stockAlloc;
  const map={30:'Conservative 30/70',60:'Moderate 60/40',80:'Growth 80/20',100:'All-Stocks'};
  if(map[s]) document.querySelectorAll('.pb').forEach(b=>{if(b.textContent.trim()===map[s])b.classList.add('active');});
}

// ═══════════════════════════════════════════════════
//  BRACKET EDITOR
// ═══════════════════════════════════════════════════
function populateBracketTable(){
  const tbody=$('bracket-edit-body');
  if(!tbody)return;
  tbody.innerHTML='';
  const mfj=BRACKETS.MFJ, single=BRACKETS.Single;
  const n=Math.max(mfj.length,single.length);
  for(let i=0;i<n;i++){
    const bm=mfj[i]||{r:0,to:0};
    const bs=single[i]||{r:0,to:0};
    const ratePct=Math.round(bm.r*100);
    const mfjTo=bm.to>=1e8?'':bm.to;
    const singTo=bs.to>=1e8?'':bs.to;
    const tr=document.createElement('tr');
    tr.style.borderBottom='1px solid var(--bd)';
    tr.innerHTML=`
      <td style="padding:.35rem .6rem;font-weight:600;color:var(--p);font-size:.88rem;">${ratePct}%</td>
      <td style="padding:.35rem .4rem;"><input type="number" id="br-mfj-${i}" value="${mfjTo}" min="0"
        placeholder="no limit" style="width:100%;max-width:160px;font-size:.85rem;"></td>
      <td style="padding:.35rem .4rem;"><input type="number" id="br-sin-${i}" value="${singTo}" min="0"
        placeholder="no limit" style="width:100%;max-width:160px;font-size:.85rem;"></td>`;
    tbody.appendChild(tr);
  }
  // Standard deduction + LTCG
  if($('std-ded-mfj'))    $('std-ded-mfj').value    = STD_DED.MFJ;
  if($('std-ded-single')) $('std-ded-single').value  = STD_DED.Single;
  if($('ltcg-0-mfj'))     $('ltcg-0-mfj').value     = LTCG_0.MFJ;
}

function applyEditedBrackets(){
  const tbody=$('bracket-edit-body');
  if(!tbody)return;
  const rows=tbody.querySelectorAll('tr');
  const newMFJ=[], newSingle=[];
  rows.forEach((tr,i)=>{
    const rm=BRACKETS.MFJ[i]||BRACKETS.Single[i];
    const rate=rm?rm.r:0;
    const mfjVal=parseFloat($('br-mfj-'+i)?.value)||1e9;
    const sinVal=parseFloat($('br-sin-'+i)?.value)||1e9;
    newMFJ.push({r:rate,to:mfjVal});
    newSingle.push({r:rate,to:sinVal});
  });
  BRACKETS.MFJ    = newMFJ;
  BRACKETS.Single = newSingle;
  STD_DED.MFJ    = parseFloat($('std-ded-mfj')?.value)    || STD_DED.MFJ;
  STD_DED.Single = parseFloat($('std-ded-single')?.value) || STD_DED.Single;
  LTCG_0.MFJ     = parseFloat($('ltcg-0-mfj')?.value)    || LTCG_0.MFJ;
  calculate();
}

function resetBracketsToDefault(){
  BRACKETS = JSON.parse(JSON.stringify(BRACKETS_DEFAULT));
  STD_DED  = {...STD_DED_DEFAULT};
  LTCG_0   = {...LTCG_0_DEFAULT};
  populateBracketTable();
  calculate();
}

// ═══════════════════════════════════════════════════
//  INIT
// ═══════════════════════════════════════════════════
window.onload = function(){
  // Load config injected by Python
  const cfgEl=document.getElementById('retirement-config');
  if(cfgEl){
    try{
      const cfg=JSON.parse(cfgEl.textContent);
      applyConfig(cfg);
      calculate(); // Auto-calculate on load
    } catch(e){ console.warn('Config parse error:',e); }
  } else {
    populateBracketTable(); toggleSpouse(); updateSS(); updateSWR(); updAlloc();
  }
  // Live birthday → age hint updates
  ['bday1','bday2'].forEach(id=>{
    const el=$(id); if(el) el.addEventListener('input',()=>{
      const hintId=id==='bday1'?'agehint1':'agehint2';
      updAge(id,hintId);
    });
  });
  // Live updates
  ['pia1','pia2','ssAge1','ssAge2'].forEach(id=>{
    const el=$(id); if(el) el.addEventListener('input',updateSS);
  });
  ['trad401k','tradIRA','roth401k','rothIRA','hsa','taxable','cash',
   'spouse401k','spouseTradIRA','spouseRothIRA','spouseRoth401k','spouseHSA',
   'goalType','customGoalYears','annualSpending'].forEach(id=>{
    const el=$(id); if(el) el.addEventListener('input',updateSWR);
  });
  // Initialize tax bracket projector
  renderTaxBracketTab();
};

// ═══════════════════════════════════════════════════
//  TAX BRACKETS TAB
// ═══════════════════════════════════════════════════
function renderTaxBracketTab(){
  // Read user inputs from the tab controls
  const userCpi  = parseFloat($('tb-cpi')?.value) || 3.0;
  const targetYr = parseInt($('tb-year')?.value)  || 2035;
  const filing   = $('tb-filing')?.value           || 'MFJ';

  // Update global CPI_RATE so re-running simulation picks up new rate
  CPI_RATE = userCpi / 100;
  // Also sync the inflationRate input if present
  if($('inflationRate')) $('inflationRate').value = userCpi;

  const yearsOut = Math.max(0, targetYr - CPI_BASE_YEAR);
  const mult     = Math.pow(1 + CPI_RATE, yearsOut);

  const baseBrackets = (BRACKETS[filing]||BRACKETS.MFJ);  // 2026 baseline
  const adjBrackets  = baseBrackets.map(b=>({r:b.r, to: b.r===0.37 ? null : Math.round(b.to*mult)}));
  const baseStd      = STD_DED[filing]||STD_DED.MFJ;      // 2026 baseline std ded
  const adjStd       = Math.round(baseStd * mult);

  // Build combined baseline + projected bracket table
  const pct = v => (v*100).toFixed(0)+'%';
  const isProjected = yearsOut > 0;
  let rows = '';
  let basePrev = 0, adjPrev = 0;
  for(let i=0;i<adjBrackets.length;i++){
    const b    = adjBrackets[i];
    const bb   = baseBrackets[i];
    const baseHi = bb.r===0.37 ? null : bb.to;
    const adjHi  = b.to;
    const color = b.r<=0.12?'#166534':b.r<=0.22?'#1e40af':b.r<=0.24?'#92400e':b.r<=0.32?'#7c2d12':'#831843';
    const bg    = b.r<=0.12?'#dcfce7':b.r<=0.22?'#dbeafe':b.r<=0.24?'#fef3c7':b.r<=0.32?'#fee2e2':'#fdf4ff';
    const baseHiStr = baseHi ? fmt(baseHi) : '∞';
    const adjHiStr  = adjHi  ? fmt(adjHi)  : '∞';
    const changed = isProjected && adjHi !== baseHi;
    rows += `<tr style="background:${bg};">
      <td style="font-weight:700;color:${color};font-size:1rem;">${pct(b.r)}</td>
      <td style="color:var(--tl);">${basePrev===0?'$0':fmt(basePrev)}</td>
      <td>${baseHiStr}</td>
      ${isProjected ? `
      <td style="color:var(--tl);border-left:2px solid #e2e8f0;">${adjPrev===0?'$0':fmt(adjPrev)}</td>
      <td style="font-weight:${changed?'700':'400'};color:${changed?color:'inherit'};">${adjHiStr}${changed?` <span style="font-size:.7rem;color:#059669;">▲</span>`:''}</td>` : ''}
    </tr>`;
    if(baseHi) basePrev = baseHi;
    if(adjHi)  adjPrev  = adjHi;
  }

  const out = $('tb-output');
  if(!out) return;
  out.innerHTML = `
    <div style="display:flex;gap:1rem;margin-bottom:.8rem;flex-wrap:wrap;">
      <div style="background:#f8fafc;border:1px solid #cbd5e1;border-radius:8px;padding:.6rem 1rem;flex:1;min-width:160px;">
        <div style="font-size:.75rem;color:var(--tl);">Standard Deduction — ${CPI_BASE_YEAR} Baseline (${filing})</div>
        <div style="font-size:1.3rem;font-weight:700;color:var(--tx);">${fmt(baseStd)}</div>
        <div style="font-size:.72rem;color:var(--tl);">Current baseline year</div>
      </div>
      ${isProjected ? `
      <div style="background:#f0fdf4;border:1px solid #86efac;border-radius:8px;padding:.6rem 1rem;flex:1;min-width:160px;">
        <div style="font-size:.75rem;color:#166534;">Standard Deduction — ${targetYr} Projected (${filing})</div>
        <div style="font-size:1.3rem;font-weight:700;color:#15803d;">${fmt(adjStd)}</div>
        <div style="font-size:.72rem;color:#4ade80;">+${fmt(adjStd-baseStd)} vs. baseline (${userCpi}% CPI × ${yearsOut} yrs)</div>
      </div>` : ''}
      <div style="background:#eff6ff;border:1px solid #93c5fd;border-radius:8px;padding:.6rem 1rem;flex:1;min-width:160px;">
        <div style="font-size:.75rem;color:#1e40af;">CPI Multiplier (${CPI_BASE_YEAR}→${targetYr})</div>
        <div style="font-size:1.3rem;font-weight:700;color:#1d4ed8;">×${mult.toFixed(3)}</div>
        <div style="font-size:.72rem;color:#60a5fa;">${yearsOut} year${yearsOut!==1?'s':''} of ${userCpi}% compounding</div>
      </div>
    </div>
    <div class="tw">
    <table>
      <thead>
        <tr>
          <th rowspan="2">Rate</th>
          <th colspan="2" style="text-align:center;background:#f1f5f9;border-bottom:1px solid #e2e8f0;">📅 ${CPI_BASE_YEAR} Baseline</th>
          ${isProjected ? `<th colspan="2" style="text-align:center;background:#f0fdf4;border-bottom:1px solid #86efac;border-left:2px solid #e2e8f0;">🔮 ${targetYr} Projected</th>` : ''}
        </tr>
        <tr>
          <th style="background:#f1f5f9;">Taxable Income From</th>
          <th style="background:#f1f5f9;">Up To</th>
          ${isProjected ? `<th style="background:#f0fdf4;border-left:2px solid #e2e8f0;">Taxable Income From</th><th style="background:#f0fdf4;">Up To</th>` : ''}
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
    </div>
    <p style="font-size:.75rem;color:var(--tl);margin-top:.6rem;">
      ⚠️ ${isProjected ? `Projected thresholds assume ${userCpi}% annual CPI from the ${CPI_BASE_YEAR} baseline. Actual IRS brackets are published each October and may differ.` : `Showing ${CPI_BASE_YEAR} baseline only — select a future year to see projected values.`} These figures are for planning purposes only.
    </p>`;

  // IRMAA table — baseline + projected
  const irmaaNames  = ['No Surcharge','Tier 1','Tier 2','Tier 3','Tier 4'];
  const irmaaSurcharges = IRMAA_SURCHARGE;
  const irmaaDesc   = ['Standard premium only','Moderate income','Higher income','High income','Highest income'];
  const baseIrmaaTh = IRMAA_TH[filing]||IRMAA_TH.MFJ;
  const irmaaRows   = baseIrmaaTh.map((th,i)=>{
    const adjTh      = Math.round(th * mult);
    const surcharge  = irmaaSurcharges[i+1]||0;
    const thChanged  = isProjected && adjTh !== th;
    return `<tr>
      <td style="font-weight:600;color:#1e40af;">${irmaaNames[i+1]}</td>
      <td style="background:#f1f5f9;">${fmt(th)}+</td>
      ${isProjected ? `<td style="font-weight:${thChanged?'700':'400'};color:${thChanged?'#059669':'inherit'};border-left:2px solid #e2e8f0;">${fmt(adjTh)}+${thChanged?` <span style="font-size:.7rem;">▲</span>`:''}</td>` : ''}
      <td style="color:#dc2626;font-weight:600;">+${fmt(surcharge)}/yr per person</td>
    </tr>`;
  }).join('');
  const baseNoSurcharge = fmt(baseIrmaaTh[0]);
  const adjNoSurcharge  = isProjected ? fmt(Math.round(baseIrmaaTh[0]*mult)) : null;
  const irmaaOut = $('tb-irmaa-output');
  if(irmaaOut) irmaaOut.innerHTML = `
    <div class="tw">
    <table>
      <thead>
        <tr>
          <th rowspan="2">Tier</th>
          <th style="background:#f1f5f9;text-align:center;">📅 ${CPI_BASE_YEAR} Baseline<br><span style="font-weight:400;font-size:.75rem;">MAGI Threshold (${filing})</span></th>
          ${isProjected ? `<th style="background:#f0fdf4;text-align:center;border-left:2px solid #e2e8f0;">🔮 ${targetYr} Projected<br><span style="font-weight:400;font-size:.75rem;">MAGI Threshold (${filing})</span></th>` : ''}
          <th>Annual Surcharge</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td style="font-weight:600;color:#166534;">No Surcharge</td>
          <td style="background:#f1f5f9;">Below ${baseNoSurcharge}</td>
          ${isProjected ? `<td style="border-left:2px solid #e2e8f0;">Below ${adjNoSurcharge}</td>` : ''}
          <td style="color:#166534;">$0</td>
        </tr>
        ${irmaaRows}
      </tbody>
    </table>
    </div>
    <p style="font-size:.75rem;color:var(--tl);margin-top:.6rem;">IRMAA is applied based on MAGI from <strong>2 years prior</strong>. Plan withdrawals and Roth conversions to stay below the first threshold. Surcharge amounts shown are per person per year (not per couple).</p>`;

  // Trigger recalculate with updated CPI
  if(typeof calculate === 'function') calculate();
}
</script>
</div><!-- .page-wrap -->
</body>
</html>"""

# ─────────────────────────────────────────────────────────────
#  HTML GENERATION
# ─────────────────────────────────────────────────────────────
def generate_html(inputs: dict, chartjs: str | None, config_file: str, generated: str) -> str:
    config_json   = json.dumps(inputs, indent=2)
    config_script = f'<script id="retirement-config" type="application/json">\n{config_json}\n</script>'

    if chartjs:
        chart_script = f'<script>\n{chartjs}\n</script>'
    else:
        chart_script = (
            '<!-- Chart.js CDN fallback (requires internet) -->\n'
            '<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>'
        )

    html = HTML_TEMPLATE
    html = html.replace('%%CHARTJS%%',   chart_script)
    html = html.replace('%%CONFIG%%',    config_script)
    html = html.replace('%%CFGFILE%%',   config_file)
    html = html.replace('%%GENERATED%%', generated)
    return html


# ─────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────
def main():
    # Determine config file
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('retirement_input.txt')
    script_dir  = Path(__file__).parent

    # Create default config if missing
    if not config_path.exists():
        config_path.write_text(DEFAULT_CONFIG, encoding='utf-8')
        print(f"\n✅ Created default config: {config_path}")
        print("   Edit it with your financial details, then run this script again.\n")
        return

    print(f"\n📄 Reading config: {config_path}")
    inputs = parse_config(config_path)

    # Summary of parsed values
    total = (inputs['trad401k'] + inputs['tradIRA'] + inputs['roth401k'] + inputs['rothIRA'] +
             inputs['hsa'] + inputs['taxable'] + inputs['cash'] +
             inputs['spouse401k'] + inputs['spouseTradIRA'] + inputs['spouseRothIRA'] +
             inputs['spouseRoth401k'] + inputs['spouseHSA'])
    print(f"   👤 {inputs['name1']} (b. {inputs['birthday1']}, age {inputs['age1']})"
          + (f"  +  {inputs['name2']} (b. {inputs['birthday2']}, age {inputs['age2']})" if inputs['hasSpouse'] else ''))
    print(f"   💰 Total portfolio: ${total:,.0f}")
    print(f"   🎯 Goal: sustain {inputs['goalType']} years | Spending: ${inputs['annualSpending']:,.0f}/yr")

    # Chart.js
    print("\n  Checking Chart.js...")
    chartjs = get_chartjs(script_dir)

    # Generate HTML
    generated = datetime.now().strftime('%Y-%m-%d %H:%M')
    html      = generate_html(inputs, chartjs, config_path.name, generated)

    # Write output
    output_path = script_dir / 'retirement_report.html'
    output_path.write_text(html, encoding='utf-8')
    size_kb = output_path.stat().st_size // 1024
    print(f"\n✅ Generated: {output_path} ({size_kb} KB)")

    # Open in browser
    uri = output_path.absolute().as_uri()
    print(f"🌐 Opening in browser: {uri}")
    try:
        webbrowser.open(uri)
    except Exception:
        print("   (Could not auto-open browser — open the file manually.)")

    print("\n─────────────────────────────────────────────")
    print(" Done! Edit retirement_input.txt and re-run")
    print(" this script to refresh your report.")
    print("─────────────────────────────────────────────\n")


if __name__ == '__main__':
    main()
