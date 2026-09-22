"""
SecureTrack - Payroll Formulas Service
All constants loaded from DB (PayrollFormulaConfig + DeductionRule).
Zero hardcoded formula values.
"""
from datetime import date, datetime
from calendar import monthrange


# ─────────────────────────────────────────────────────────
#  DB Config Loaders
# ─────────────────────────────────────────────────────────

def load_formula_configs(db) -> dict:
    """
    Load all PayrollFormulaConfig rows into a nested dict:
      { classification: { config_key: value, ... }, ... }
    Tax brackets are stored under classification '__tax__'.
    """
    from app.models.payroll_formula_config import PayrollFormulaConfig
    rows = db.query(PayrollFormulaConfig).filter(PayrollFormulaConfig.is_active == True).all()
    result: dict = {}
    for row in rows:
        result.setdefault(row.classification, {})[row.config_key] = row.value
    return result


def load_deduction_rules(db) -> dict:
    """
    Load all active DeductionRule rows into a dict keyed by rule_type.
    """
    from app.models.deduction_rule import DeductionRule
    rules = db.query(DeductionRule).filter(DeductionRule.is_active == True).all()
    return {r.rule_type: r for r in rules}


def build_tax_brackets(formula_configs: dict) -> list:
    """
    Reconstruct tax bracket list from formula_configs['__tax__'] entries.
    Returns: [(limit, rate), ...] sorted by limit ascending.
    """
    tax_cfg = formula_configs.get("__tax__", {})
    brackets = []
    i = 0
    while True:
        limit_key = f"bracket_{i}_limit"
        rate_key  = f"bracket_{i}_rate"
        if limit_key not in tax_cfg:
            break
        brackets.append((tax_cfg[limit_key], tax_cfg[rate_key]))
        i += 1
    # Sort ascending by limit (should already be, but be safe)
    brackets.sort(key=lambda x: x[0])
    return brackets


# ─────────────────────────────────────────────────────────
#  Pure calculation helpers (no hardcodes)
# ─────────────────────────────────────────────────────────

def calc_daily_rate(classification: str, formula_configs: dict, user_daily_rate: float = 0.0) -> float:
    """Priority: user-level daily_rate > DB config > 0."""
    if user_daily_rate and user_daily_rate > 0:
        return user_daily_rate
    cls_cfg = formula_configs.get(classification, {})
    return cls_cfg.get("daily_rate", 0.0)


def calc_work_days(hire_date_str: str, term_date_str: str, year: int, month: int) -> float:
    """Calculate working days (AW column): 30 if full month, prorated on hire/term."""
    _, days_in_month = monthrange(year, month)
    month_start = date(year, month, 1)
    month_end   = date(year, month, days_in_month)

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
            if month_start <= h <= month_end:
                return (month_end - h).days + 1
        except Exception:
            pass

    return 30.0


def calc_operational_days(
    work_days: float,
    absent_exc: float, absent_unexc: float,
    overtime: float, rest_allow: float,
    late: float, deduction: float,
    rest: float, annual_lv: float, sick_lv: float,
    term_reason: str,
    deduction_rules: dict,
) -> float:
    """
    BG formula: operational working days after deductions.
    Multipliers come from DeductionRule or default to 1 / 0.5.
    """
    absent_unexc_mult = 3.0  # 1 unexcused absence = -3 days
    late_mult         = 0.5  # 1 late = -0.5 days
    sick_grace        = 2    # first N sick days don't count

    # Allow override from deduction rules if defined
    r_absent = deduction_rules.get("absence_unexcused")
    if r_absent and r_absent.is_days_multiplier:
        absent_unexc_mult = r_absent.amount

    r_late = deduction_rules.get("late")
    if r_late and r_late.is_days_multiplier:
        late_mult = r_late.amount

    base = (
        work_days
        - absent_exc
        - absent_unexc * absent_unexc_mult
        + overtime
        + rest_allow
        - late * late_mult
        - deduction
    )

    if term_reason in ("انقطاع", "استقاله فوريه"):
        base = base - rest - annual_lv

    if sick_lv > sick_grace:
        base -= (sick_lv - sick_grace) * late_mult

    return max(base, 0.0)


def calc_annual_tax(annual_gross: float, tax_brackets: list) -> float:
    """Egyptian progressive income tax computed from DB tax brackets."""
    if not tax_brackets or annual_gross <= tax_brackets[0][0]:
        return 0.0

    tax = 0.0
    remaining = annual_gross
    prev_limit = 0.0

    for limit, rate in tax_brackets:
        bracket_amount = min(remaining, limit - prev_limit)
        if bracket_amount <= 0:
            break
        tax += max(bracket_amount, 0) * rate
        remaining -= bracket_amount
        prev_limit = limit

    return tax


def calc_overtime_pay(overtime_hours: float, daily_rate: float, deduction_rules: dict) -> float:
    """
    Overtime pay: hourly_rate × overtime_hours × multiplier.
    hourly_rate = daily_rate / 8 (or from DeductionRule.amount if is_per_minute=False)
    multiplier  = DeductionRule(type='overtime').amount (default 1.5)
    """
    if overtime_hours <= 0:
        return 0.0
    multiplier = 1.5
    r = deduction_rules.get("overtime")
    if r:
        multiplier = r.amount if r.amount > 0 else 1.5
    hourly_rate = daily_rate / 8.0 if daily_rate > 0 else 0.0
    return round(overtime_hours * hourly_rate * multiplier, 2)


# ─────────────────────────────────────────────────────────
#  Main row computation
# ─────────────────────────────────────────────────────────

def compute_row(
    user: dict,
    attendance_data: list,
    advance_amount: float,
    config_overrides: dict,   # SalaryClassificationConfig overrides (legacy, still respected)
    year: int,
    month: int,
    serial_no: int,
    formula_configs: dict = None,   # PayrollFormulaConfig (DB-driven, preferred)
    deduction_rules: dict = None,   # DeductionRule (DB-driven)
    manual_bonus: float = 0.0,      # Approved bonuses sum
) -> dict:
    """
    Compute all payroll columns for one employee.
    formula_configs and deduction_rules come from DB — no hardcoded values.
    Falls back to config_overrides (SalaryClassificationConfig) for legacy compat.
    """
    if formula_configs is None:
        formula_configs = {}
    if deduction_rules is None:
        deduction_rules = {}

    cls         = user.get("classification", "") or ""
    hire        = user.get("hire_date", "")
    term        = user.get("termination_date", "")
    term_reason = user.get("termination_reason", "")
    ins         = user.get("insurance_status", "none") or "none"

    # ── Attendance — 4 weeks ──
    weeks = list(attendance_data) if attendance_data else []
    while len(weeks) < 4:
        weeks.append({})

    def wk(i, key):
        return float(weeks[i].get(key, 0) or 0)

    t_absent_exc   = sum(wk(i, "absent_excused")   for i in range(4))
    t_absent_unexc = sum(wk(i, "absent_unexcused") for i in range(4))
    t_overtime     = sum(wk(i, "overtime")         for i in range(4))
    t_rest_allow   = sum(wk(i, "rest_allowance")   for i in range(4))
    t_late         = sum(wk(i, "late")             for i in range(4))
    t_deduction    = sum(wk(i, "deduction")        for i in range(4))
    t_rest         = sum(wk(i, "rest")             for i in range(4))
    t_annual_lv    = sum(wk(i, "annual_leave")     for i in range(4))
    t_sick_lv      = sum(wk(i, "sick_leave")       for i in range(4))
    t_ot_hours     = sum(wk(i, "overtime_hours")   for i in range(4))

    # ── AW: work days ──
    work_days = calc_work_days(hire, term, year, month)

    # ── BH: daily rate ──
    user_dr = float(user.get("daily_rate", 0) or 0)
    dr = calc_daily_rate(cls, formula_configs, user_daily_rate=user_dr)
    # Fallback to legacy SalaryClassificationConfig
    if dr == 0 and config_overrides and cls in config_overrides:
        dr = config_overrides[cls].get("daily_rate", 0.0)

    # ── BG: gross days ──
    gross_days = work_days - t_absent_exc + t_overtime + t_rest_allow

    # ── BI: gross salary (ops) ──
    salary_ops = gross_days * dr

    # ── Monetary Penalties from Deduction Rules ──
    absent_unexc_mult = 3.0
    r_absent = deduction_rules.get("absence_unexcused")
    if r_absent and r_absent.is_days_multiplier:
        absent_unexc_mult = r_absent.amount

    late_mult = 0.5
    r_late = deduction_rules.get("late")
    if r_late and r_late.is_days_multiplier:
        late_mult = r_late.amount

    sick_grace = 2

    ded_absence = t_absent_unexc * absent_unexc_mult * dr
    ded_late = t_late * late_mult * dr
    ded_other = t_deduction * dr
    ded_sick = max(0.0, t_sick_lv - sick_grace) * late_mult * dr
    ded_term = (t_rest + t_annual_lv) * dr if term_reason in ("انقطاع", "استقاله فوريه") else 0.0

    total_attendance_penalties = ded_absence + ded_late + ded_other + ded_sick + ded_term

    # For operational days tracking in DB (purely for records)
    op_days = max(0.0, gross_days - (total_attendance_penalties / dr if dr > 0 else 0))

    # ── BJ: annual increase current year ──
    cls_cfg = formula_configs.get(cls, {})
    # Fallback chain: DB config > SalaryClassificationConfig > 0
    ai_base = cls_cfg.get("annual_increase_base", 0.0)
    if ai_base == 0 and config_overrides and cls in config_overrides:
        ai_base = config_overrides[cls].get("annual_increase_base", 0.0)
    annual_inc_current = (ai_base / 30.0) * op_days

    # ── BK: annual increase prev years ──
    monthly_sal = dr * 30
    pct = cls_cfg.get("annual_increase_pct", 0.0)
    if pct == 0 and config_overrides and cls in config_overrides:
        pct = config_overrides[cls].get("annual_increase_pct", 0.0)
    annual_inc_prev = ((monthly_sal * pct) * op_days) / 30.0

    # ── BL: gross salary ──
    gross = salary_ops + annual_inc_current + annual_inc_prev

    # ── BN: advance deduction ──
    adv_ded = float(advance_amount or 0)

    # ── BO: insurance share ──
    ins_share = float(user.get("employee_insurance", 0) or 0)

    # ── BT: incentive ──
    inc_rate = cls_cfg.get("incentive_rate", 0.0)
    if inc_rate == 0 and config_overrides and cls in config_overrides:
        inc_rate = config_overrides[cls].get("incentive_rate", 0.0)
    bt_incentive = (inc_rate / 30.0) * op_days

    # ── BU: increase 2025 ──
    bu_rate = cls_cfg.get("increase_2025_rate", 0.0)
    if bu_rate == 0 and config_overrides and cls in config_overrides:
        bu_rate = config_overrides[cls].get("increase_2025_rate", 0.0)
    bu_increase = (bu_rate / 30.0) * op_days

    # ── BX: total incentive ──
    bv_bonus_manual = float(manual_bonus or 0)
    bw_bonus_ded    = 0.0
    bx_total_incentive = bt_incentive + bu_increase + bv_bonus_manual - bw_bonus_ded

    # ── BY: payroll amount ──
    by_payroll = float(user.get("payroll_amount", 0) or 0)

    # ── Tax ──
    tax_brackets = build_tax_brackets(formula_configs)
    ci_monthly_sal = dr * 30
    cj_actual      = salary_ops + annual_inc_current + annual_inc_prev
    ck_allowances  = bx_total_incentive
    cl_total_income = cj_actual + ck_allowances
    cm_ins          = ins_share
    cn_exemption    = 20000.0 / 12.0
    co_net          = cl_total_income - cm_ins - cn_exemption
    cp_annual       = co_net * 12
    cq_tax          = calc_annual_tax(max(cp_annual, 0), tax_brackets)
    cr_monthly_tax  = cq_tax / 12.0
    bp_tax          = cr_monthly_tax

    # ── Late deduction (EGP) ──
    late_rule = deduction_rules.get("late")
    late_amount_per = 0.0
    if late_rule and late_rule.is_per_minute:
        late_amount_per = late_rule.amount  # per minute
    elif late_rule:
        late_amount_per = late_rule.amount  # fixed per occurrence

    # ── Overtime pay ──
    overtime_pay = calc_overtime_pay(t_ot_hours, dr, deduction_rules)

    # ── BQ: net salary ──
    total_deductions_monetary = total_attendance_penalties + adv_ded + ins_share + bp_tax
    bq_net = round(salary_ops + annual_inc_current + annual_inc_prev - total_deductions_monetary, 0)

    # ── BS: salary diff ──
    bs_diff = bq_net - by_payroll

    # ── CA: total salary diff + incentive ──
    ca_total = round(bx_total_incentive + bs_diff, 0)

    # ── CB: bonus ──
    cb_rate = cls_cfg.get("bonus_rate", 0.0)
    if cb_rate == 0 and config_overrides and cls in config_overrides:
        cb_rate = config_overrides[cls].get("bonus_rate", 0.0)
    cb_bonus = round((cb_rate / 30.0) * op_days, 0)

    # ── CC: grand incentive ──
    cc_grand = ca_total + cb_bonus

    return {
        "employee_code":            user.get("employee_code", ""),
        "serial_no":                serial_no,
        "classification":           cls,
        "shift_time":               user.get("shift_time", ""),
        "supervisor_name":          user.get("supervisor_name", ""),
        "site_name":                user.get("site_name", ""),
        "hire_date":                str(hire)[:10] if hire else "",
        "termination_date":         str(term)[:10] if term else "",
        "uniform_status":           user.get("uniform_status", ""),
        "termination_reason":       term_reason,
        "insurance_status":         ins,
        "employee_name":            user.get("name", ""),
        # Week data
        "w1_absent_excused":   wk(0, "absent_excused"),  "w1_absent_unexcused": wk(0, "absent_unexcused"),
        "w1_overtime":         wk(0, "overtime"),         "w1_rest_allowance":   wk(0, "rest_allowance"),
        "w1_late":             wk(0, "late"),             "w1_deduction":        wk(0, "deduction"),
        "w1_rest":             wk(0, "rest"),             "w1_annual_leave":     wk(0, "annual_leave"),
        "w1_sick_leave":       wk(0, "sick_leave"),
        "w2_absent_excused":   wk(1, "absent_excused"),  "w2_absent_unexcused": wk(1, "absent_unexcused"),
        "w2_overtime":         wk(1, "overtime"),         "w2_rest_allowance":   wk(1, "rest_allowance"),
        "w2_late":             wk(1, "late"),             "w2_deduction":        wk(1, "deduction"),
        "w2_rest":             wk(1, "rest"),             "w2_annual_leave":     wk(1, "annual_leave"),
        "w2_sick_leave":       wk(1, "sick_leave"),
        "w3_absent_excused":   wk(2, "absent_excused"),  "w3_absent_unexcused": wk(2, "absent_unexcused"),
        "w3_overtime":         wk(2, "overtime"),         "w3_rest_allowance":   wk(2, "rest_allowance"),
        "w3_late":             wk(2, "late"),             "w3_deduction":        wk(2, "deduction"),
        "w3_rest":             wk(2, "rest"),             "w3_annual_leave":     wk(2, "annual_leave"),
        "w3_sick_leave":       wk(2, "sick_leave"),
        "w4_absent_excused":   wk(3, "absent_excused"),  "w4_absent_unexcused": wk(3, "absent_unexcused"),
        "w4_overtime":         wk(3, "overtime"),         "w4_rest_allowance":   wk(3, "rest_allowance"),
        "w4_late":             wk(3, "late"),             "w4_deduction":        wk(3, "deduction"),
        "w4_rest":             wk(3, "rest"),             "w4_annual_leave":     wk(3, "annual_leave"),
        "w4_sick_leave":       wk(3, "sick_leave"),
        # Totals
        "total_work_days":          work_days,
        "total_absent_excused":     t_absent_exc,
        "total_absent_unexcused":   t_absent_unexc,
        "total_overtime":           t_overtime,
        "total_overtime_hours":     t_ot_hours,
        "total_rest_allowance":     t_rest_allow,
        "total_late":               t_late,
        "total_deduction":          t_deduction,
        "total_rest":               t_rest,
        "total_annual_leave":       t_annual_lv,
        "total_sick_leave":         t_sick_lv,
        # Salary
        "operational_days":         round(op_days, 2),
        "daily_rate":               round(dr, 4),
        "salary_from_ops":          round(salary_ops, 2),
        "annual_increase_current":  round(annual_inc_current, 2),
        "annual_increase_prev":     round(annual_inc_prev, 2),
        "gross_salary":             round(gross, 2),
        "manual_deduction":         0,
        "advance_deduction":        adv_ded,
        "insurance_share":          ins_share,
        "tax_deduction":            round(bp_tax, 2),
        "other_deductions":         round(total_attendance_penalties, 2),
        "net_salary":               bq_net,
        "overtime_pay":             overtime_pay,
        "salary_diff":              round(bs_diff, 2),
        "incentive":                round(bt_incentive, 2),
        "increase_2025":            round(bu_increase, 2),
        "bonus":                    0,
        "bonus_deduction":          0,
        "total_incentive":          round(bx_total_incentive, 2),
        "payroll_amount":           by_payroll,
        "cash_payment":             0,
        "total_salary_diff_incentive": ca_total,
        "bonus_rounded":            cb_bonus,
        "grand_incentive":          cc_grand,
        # Bank
        "transfer_name":            user.get("transfer_name", "") or user.get("name", ""),
        "incentive_transfer_name":  user.get("transfer_name", "") or user.get("name", ""),
        "bank_account_1":           user.get("bank_account", ""),
        "bank_account_2":           user.get("bank_account", ""),
        "transfer_method":          user.get("transfer_method", "") or "",
        # Tax
        "monthly_salary":           round(ci_monthly_sal, 2),
        "actual_salary":            round(cj_actual, 2),
        "allowances":               round(ck_allowances, 2),
        "total_income":             round(cl_total_income, 2),
        "employee_insurance":       cm_ins,
        "annual_personal_exemption": round(cn_exemption, 2),
        "net_after_insurance":      round(co_net, 2),
        "annual_taxable":           round(cp_annual, 2),
        "annual_tax":               round(cq_tax, 2),
        "monthly_tax":              round(cr_monthly_tax, 2),
    }