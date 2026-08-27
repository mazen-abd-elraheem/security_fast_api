"""
SecureTrack - Payroll Formulas Service
Replicates all Excel formulas server-side for instant computation.
"""
from datetime import date, datetime
from calendar import monthrange


# Daily rate lookup by classification (from Excel BH formula)
DAILY_RATE_MAP = {
    "مشرف 12": 2500 / 30,
    "مشرف 8": 2000 / 30,
    "فرد 8": 1500 / 30,
    "فرد 12": 2000 / 30,
    "فرد 6": 1350 / 30,
    "فرد 4": 25,
    "ليدى": 1500 / 30,
    "مدير": 6000 / 30,
    "نائب": 2500 / 30,
    "جارد": 1500 / 30,
}

# Annual increase (BJ) base amounts per classification
ANNUAL_INCREASE_BASE = {
    "مشرف 12": 500,
    "مشرف 8": 500,
    "فرد 8": 500,
    "فرد 12": 500,
    "فرد 6": 500,
    "فرد 4": 500,
    "ليدى": 500,
    "مدير": 500,
    "نائب": 500,
    "جارد": 800,
}

# Incentive (BT) rates per classification per 30 days
INCENTIVE_RATE = {
    "مشرف 12": 1550,
    "مشرف 8": 1150,
    "فرد 8": 1100,
    "فرد 12": 1100,
    "فرد 6": 1100,
    "فرد 4": 1100,
    "ليدى": 1100,
    "مدير": 1550,
    "نائب": 1550,
    "جارد": 1700,
}

# Increase 2025 (BU) rates per classification per 30 days
INCREASE_2025_RATE = {
    "مشرف 12": 900,
    "مشرف 8": 900,
    "فرد 8": 600,
    "فرد 12": 600,
    "فرد 6": 450,
    "فرد 4": 300,
    "ليدى": 600,
    "مدير": 1500,
    "نائب": 1450,
    "جارد": 600,
}

# Bonus (CB) rate per 30 days
BONUS_RATE = {
    "فرد 6": 1300,
    "فرد 4": 1200,
}
BONUS_RATE_DEFAULT = 1750


# Egyptian Tax Brackets
TAX_BRACKETS = [
    (40000, 0.0),
    (55000, 0.10),
    (70000, 0.15),
    (200000, 0.20),
    (400000, 0.225),
    (600000, 0.25),
]


def calc_daily_rate(classification, config_override=None):
    """Get daily rate from classification. Config override takes priority."""
    if config_override and classification in config_override:
        return config_override[classification].get("daily_rate", 0)
    return DAILY_RATE_MAP.get(classification, 2000 / 30)


def calc_work_days(hire_date_str, term_date_str, year, month):
    """Calculate working days (AW column)."""
    _, days_in_month = monthrange(year, month)
    month_start = date(year, month, 1)
    month_end = date(year, month, days_in_month)

    if term_date_str:
        try:
            term_date = datetime.strptime(str(term_date_str)[:10], "%Y-%m-%d").date()
            if term_date < month_start:
                return 0
            if term_date <= month_end:
                return (term_date - month_start).days + 1
        except Exception:
            pass

    if hire_date_str:
        try:
            h = datetime.strptime(str(hire_date_str)[:10], "%Y-%m-%d").date()
            if h >= month_start and h <= month_end:
                return (month_end - h).days + 1
        except Exception:
            pass

    return 30


def calc_operational_days(work_days, absent_exc, absent_unexc, overtime,
                          rest_allow, late, deduction, rest, annual_lv, sick_lv,
                          term_reason):
    """BG formula: operational working days after deductions."""
    base = work_days - absent_exc - absent_unexc * 3 + overtime + rest_allow - late * 0.5 - deduction
    if term_reason in ("انقطاع", "استقاله فوريه"):
        base = base - rest - annual_lv
    if sick_lv > 2:
        base -= (sick_lv - 2) * 0.5
    return max(base, 0)


def calc_annual_tax(annual_gross):
    """Egyptian progressive income tax."""
    if annual_gross <= 40000:
        return 0.0

    tax = 0.0
    remaining = annual_gross
    prev = 0

    brackets = [
        (40000, 0.0),
        (55000, 0.10),
        (70000, 0.15),
        (200000, 0.20),
        (400000, 0.225),
        (600000, 0.25),
        (1200000, 0.25),
    ]

    for limit, rate in brackets:
        bracket_amount = min(remaining, limit - prev)
        if bracket_amount <= 0:
            break
        tax += max(bracket_amount, 0) * rate
        remaining -= bracket_amount
        prev = limit

    if annual_gross > 1200000:
        tax += (annual_gross - 1200000) * 0.275

    return tax


def compute_row(user, attendance_data, advance_amount, config_overrides, year, month, serial_no):
    """Compute all columns for one employee row. Returns dict of all fields."""
    cls = user.get("classification", "") or ""
    hire = user.get("hire_date", "")
    term = user.get("termination_date", "")
    term_reason = user.get("termination_reason", "")
    ins = user.get("insurance_status", "none") or "none"

    # Attendance - 4 weeks (from attendance_data or defaults to 0)
    weeks = attendance_data or [{} for _ in range(4)]
    while len(weeks) < 4:
        weeks.append({})

    def wk(i, key):
        return float(weeks[i].get(key, 0) or 0)

    # Section 3: Monthly totals (sum of 4 weeks)
    t_absent_exc = sum(wk(i, "absent_excused") for i in range(4))
    t_absent_unexc = sum(wk(i, "absent_unexcused") for i in range(4))
    t_overtime = sum(wk(i, "overtime") for i in range(4))
    t_rest_allow = sum(wk(i, "rest_allowance") for i in range(4))
    t_late = sum(wk(i, "late") for i in range(4))
    t_deduction = sum(wk(i, "deduction") for i in range(4))
    t_rest = sum(wk(i, "rest") for i in range(4))
    t_annual_lv = sum(wk(i, "annual_leave") for i in range(4))
    t_sick_lv = sum(wk(i, "sick_leave") for i in range(4))

    # AW: work days
    work_days = calc_work_days(hire, term, year, month)

    # BG: operational days
    op_days = calc_operational_days(work_days, t_absent_exc, t_absent_unexc, t_overtime,
                                    t_rest_allow, t_late, t_deduction, t_rest,
                                    t_annual_lv, t_sick_lv, term_reason)

    # BH: daily rate
    dr = calc_daily_rate(cls, config_overrides)

    # BI: salary from ops
    salary_ops = op_days * dr

    # BJ: annual increase current year
    ai_base = ANNUAL_INCREASE_BASE.get(cls, 500)
    annual_inc_current = (ai_base / 30) * op_days

    # BK: annual increase prev years (uses monthly_salary * percentage)
    monthly_sal = dr * 30
    pct = 0.3  # default
    if config_overrides and cls in config_overrides:
        pct = config_overrides[cls].get("annual_increase_pct", 0.3)
    annual_inc_prev = ((monthly_sal * pct) * op_days) / 30

    # BL: gross salary
    gross = salary_ops + annual_inc_current + annual_inc_prev

    # BN: advance deduction (from cash_advance table)
    adv_ded = float(advance_amount or 0)

    # BO: insurance share
    ins_share = float(user.get("employee_insurance", 0) or 0)

    # Tax calculation (CI-CR)
    ci_monthly_sal = dr * 30
    cj_actual = salary_ops + annual_inc_current + annual_inc_prev

    # BT: incentive
    inc_rate = INCENTIVE_RATE.get(cls, 1100)
    bt_incentive = (inc_rate / 30) * op_days

    # BU: increase 2025
    bu_rate = INCREASE_2025_RATE.get(cls, 600)
    bu_increase = (bu_rate / 30) * op_days

    # BX: total incentive
    bv_bonus_manual = 0  # manual entry
    bw_bonus_ded = 0  # manual entry
    bx_total_incentive = bt_incentive + bu_increase + bv_bonus_manual - bw_bonus_ded

    # BY: payroll amount (from user config)
    by_payroll = float(user.get("payroll_amount", 0) or 0)

    # CK: allowances
    ck_allowances = bx_total_incentive

    # CL: total income
    cl_total_income = cj_actual + ck_allowances

    # CM: employee insurance
    cm_ins = ins_share

    # CN: annual personal exemption
    cn_exemption = 20000 / 12

    # CO: net after insurance
    co_net = cl_total_income - cm_ins - cn_exemption

    # CP: annual taxable
    cp_annual = co_net * 12

    # CQ: annual tax
    cq_tax = calc_annual_tax(max(cp_annual, 0))

    # CR: monthly tax
    cr_monthly_tax = cq_tax / 12

    # BP: tax deduction = CR
    bp_tax = cr_monthly_tax

    # BQ: net salary
    bq_net = round(salary_ops + annual_inc_current + annual_inc_prev - adv_ded - ins_share - bp_tax, 0)

    # BS: salary diff
    bs_diff = bq_net - by_payroll

    # CA: total salary diff + incentive
    ca_total = round(bx_total_incentive + bs_diff, 0)

    # CB: bonus rounded
    cb_rate = BONUS_RATE.get(cls, BONUS_RATE_DEFAULT)
    cb_bonus = round((cb_rate / 30) * op_days, 0)

    # CC: grand incentive
    cc_grand = ca_total + cb_bonus

    return {
        "employee_code": user.get("employee_code", ""),
        "serial_no": serial_no,
        "classification": cls,
        "shift_time": user.get("shift_time", ""),
        "supervisor_name": user.get("supervisor_name", ""),
        "site_name": user.get("site_name", ""),
        "hire_date": str(hire)[:10] if hire else "",
        "termination_date": str(term)[:10] if term else "",
        "uniform_status": user.get("uniform_status", ""),
        "termination_reason": term_reason,
        "insurance_status": ins,
        "employee_name": user.get("name", ""),
        # Week data
        "w1_absent_excused": wk(0, "absent_excused"), "w1_absent_unexcused": wk(0, "absent_unexcused"),
        "w1_overtime": wk(0, "overtime"), "w1_rest_allowance": wk(0, "rest_allowance"),
        "w1_late": wk(0, "late"), "w1_deduction": wk(0, "deduction"),
        "w1_rest": wk(0, "rest"), "w1_annual_leave": wk(0, "annual_leave"), "w1_sick_leave": wk(0, "sick_leave"),
        "w2_absent_excused": wk(1, "absent_excused"), "w2_absent_unexcused": wk(1, "absent_unexcused"),
        "w2_overtime": wk(1, "overtime"), "w2_rest_allowance": wk(1, "rest_allowance"),
        "w2_late": wk(1, "late"), "w2_deduction": wk(1, "deduction"),
        "w2_rest": wk(1, "rest"), "w2_annual_leave": wk(1, "annual_leave"), "w2_sick_leave": wk(1, "sick_leave"),
        "w3_absent_excused": wk(2, "absent_excused"), "w3_absent_unexcused": wk(2, "absent_unexcused"),
        "w3_overtime": wk(2, "overtime"), "w3_rest_allowance": wk(2, "rest_allowance"),
        "w3_late": wk(2, "late"), "w3_deduction": wk(2, "deduction"),
        "w3_rest": wk(2, "rest"), "w3_annual_leave": wk(2, "annual_leave"), "w3_sick_leave": wk(2, "sick_leave"),
        "w4_absent_excused": wk(3, "absent_excused"), "w4_absent_unexcused": wk(3, "absent_unexcused"),
        "w4_overtime": wk(3, "overtime"), "w4_rest_allowance": wk(3, "rest_allowance"),
        "w4_late": wk(3, "late"), "w4_deduction": wk(3, "deduction"),
        "w4_rest": wk(3, "rest"), "w4_annual_leave": wk(3, "annual_leave"), "w4_sick_leave": wk(3, "sick_leave"),
        # Totals
        "total_work_days": work_days, "total_absent_excused": t_absent_exc,
        "total_absent_unexcused": t_absent_unexc, "total_overtime": t_overtime,
        "total_rest_allowance": t_rest_allow, "total_late": t_late,
        "total_deduction": t_deduction, "total_rest": t_rest,
        "total_annual_leave": t_annual_lv, "total_sick_leave": t_sick_lv,
        # Salary
        "operational_days": round(op_days, 2), "daily_rate": round(dr, 4),
        "salary_from_ops": round(salary_ops, 2), "annual_increase_current": round(annual_inc_current, 2),
        "annual_increase_prev": round(annual_inc_prev, 2), "gross_salary": round(gross, 2),
        "manual_deduction": 0, "advance_deduction": adv_ded,
        "insurance_share": ins_share, "tax_deduction": round(bp_tax, 2),
        "net_salary": bq_net,
        # Bonus
        "other_deductions": 0, "salary_diff": round(bs_diff, 2),
        "incentive": round(bt_incentive, 2), "increase_2025": round(bu_increase, 2),
        "bonus": 0, "bonus_deduction": 0,
        "total_incentive": round(bx_total_incentive, 2), "payroll_amount": by_payroll,
        "cash_payment": 0, "total_salary_diff_incentive": ca_total,
        "bonus_rounded": cb_bonus, "grand_incentive": cc_grand,
        # Bank
        "transfer_name": user.get("name", ""), "incentive_transfer_name": user.get("name", ""),
        "bank_account_1": user.get("bank_account", ""), "bank_account_2": user.get("bank_account", ""),
        "transfer_method": "كويتي باي رول",
        # Tax
        "monthly_salary": round(ci_monthly_sal, 2), "actual_salary": round(cj_actual, 2),
        "allowances": round(ck_allowances, 2), "total_income": round(cl_total_income, 2),
        "employee_insurance": cm_ins, "annual_personal_exemption": round(cn_exemption, 2),
        "net_after_insurance": round(co_net, 2), "annual_taxable": round(cp_annual, 2),
        "annual_tax": round(cq_tax, 2), "monthly_tax": round(cr_monthly_tax, 2),
    }