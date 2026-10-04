# -*- coding: utf-8 -*-
"""Server-side data for the Management Dashboard (Command Rail / Doha Sand / Modern).

Every number below is computed straight from the models the verification PDF confirmed
for this database - no duplicate ledger of "the numbers", no cached/mock arrays. Where
the PDF flagged a field as free-text, unstored or missing, the caution text on that
measure says so instead of quietly presenting a partial number as a solid one.
"""
import json
from copy import deepcopy
from datetime import timedelta

from odoo import api, fields, models

UNIT = "real.estate.subproperty"
MOVE = "account.move"
MOVE_LINE = "account.move.line"
CONTRACT = "property.contract"
CHEQUE = "nn.pdc.cheque"
CHQ_HIST = "cheq.history"
BUDGET_LINE = "crossovered.budget.lines"
ACCOUNT = "account.account"

CONFIG_KEY = "nn_management_dashboard.thresholds"

# Same defaults as the design prototype: amber/red per measure, all "high is bad".
DEFAULT_THRESHOLDS = {
    "arrears": {"dir": "high", "amber": 30, "red": 60},
    "cheques": {"dir": "high", "amber": 5, "red": 12},
    "leases": {"dir": "high", "amber": 10, "red": 20},
    "vacant": {"dir": "high", "amber": 10, "red": 25},
    "below": {"dir": "high", "amber": 15, "red": 30},
    "cost": {"dir": "high", "amber": 5, "red": 15},
    "dues": {"dir": "high", "amber": 2000000, "red": 5000000},
    "unearned": {"dir": "high", "amber": 500000, "red": 1000000},
}

BOUNCE_STATES = ("returned", "returned_cash", "returned_bank", "returned_legal")
ROUTE_LABEL = {
    "returned": "Returned",
    "returned_cash": "Returned (cash pay)",
    "returned_bank": "Returned (bank transfer)",
    "returned_legal": "Returned (legal action)",
}


class _Scoped:
    """Search as the user, so the selected companies decide which records count, then
    read the hits with sudo so related records (analytic accounts, partners, units)
    belonging to the same company never raise an access error during traversal."""

    def __init__(self, model):
        self._model = model

    def search(self, *args, **kwargs):
        return self._model.search(*args, **kwargs).sudo()

    def __getattr__(self, name):
        return getattr(self._model, name)


def _r(v, nd=2):
    return round(v or 0.0, nd)


class NnManagementDashboard(models.AbstractModel):
    _name = "nn.management.dashboard"
    _description = "Management Dashboard Data Provider"

    def _scoped(self, model):
        return _Scoped(self.env[model])

    def _own_companies(self):
        # Units and properties are not reliably limited by a record rule in this database
        # (the Sub Property multi-company rule can be archived), so filter explicitly.
        return [("company_id", "in", self.env.companies.ids)]

    # ------------------------------------------------------------ thresholds
    @api.model
    def get_thresholds(self):
        raw = self.env["ir.config_parameter"].sudo().get_param(CONFIG_KEY)
        out = deepcopy(DEFAULT_THRESHOLDS)
        if raw:
            try:
                saved = json.loads(raw)
            except ValueError:
                saved = {}
            for key, val in saved.items():
                if key in out and isinstance(val, dict):
                    for f in ("dir", "amber", "red"):
                        if f in val:
                            out[key][f] = val[f]
        return out

    @api.model
    def set_thresholds(self, thresholds):
        self.env["ir.config_parameter"].sudo().set_param(CONFIG_KEY, json.dumps(thresholds or {}))
        return self.get_thresholds()

    @api.model
    def reset_thresholds(self):
        self.env["ir.config_parameter"].sudo().set_param(CONFIG_KEY, "")
        return self.get_thresholds()

    def _status(self, key, value, thresholds):
        t = thresholds.get(key) or DEFAULT_THRESHOLDS[key]
        amber, red, high = t.get("amber", 0), t.get("red", 0), t.get("dir", "high") == "high"
        if high:
            return "crit" if value >= red else "warn" if value >= amber else "good"
        return "crit" if value <= red else "warn" if value <= amber else "good"

    def _action(self, name, model, domain):
        out = {"name": name, "model": model, "domain": domain}
        if model == MOVE_LINE:
            # The stock Journal Items list loads analytic accounts, which another company's
            # analytic rule can block; this list leaves them out.
            view = self.env.ref("nn_management_dashboard.view_nmd_move_line_list", raise_if_not_found=False)
            out["view_id"] = view.id if view else False
        return out

    # ------------------------------------------------------------ main entry
    @api.model
    def get_dashboard(self, date_from, date_to, fwd_from, fwd_to, all_time=False):
        ctx = {
            "all": bool(all_time),
            "from": fields.Date.from_string(date_from) if date_from else None,
            "to": fields.Date.from_string(date_to) if date_to else None,
            "fwd_from": fields.Date.from_string(fwd_from) if fwd_from else None,
            "fwd_to": fields.Date.from_string(fwd_to) if fwd_to else None,
            "today": fields.Date.context_today(self),
        }
        thresholds = self.get_thresholds()

        builders = [
            ("arrears", "01", "Rent Arrears", self._m_arrears),
            ("cheques", "02", "Bounced Cheques", self._m_cheques),
            ("leases", "03", "Leases Nearing Expiry", self._m_leases),
            ("vacant", "04", "Vacant Units", self._m_vacant),
            ("below", "05", "Leases Below Target Rent", self._m_below),
            ("cost", "06", "Cost Overview", self._m_cost),
            ("dues", "07", "Unpaid Dues", self._m_dues),
            ("unearned", "08", "Unearned Income", self._m_unearned),
        ]
        measures, details = [], {}
        for key, idx, name, fn in builders:
            out = fn(ctx)
            detail = out.pop("detail")
            stat = out.pop("stat")
            out.update({"key": key, "idx": idx, "name": name,
                        "status": self._status(key, stat, thresholds)})
            measures.append(out)
            details[key] = detail
        return {"measures": measures, "details": details, "thresholds": thresholds,
                "as_of": fields.Date.to_string(ctx["today"])}

    # ------------------------------------------------------------ 1. rent arrears
    def _m_arrears(self, ctx):
        Move = self._scoped(MOVE)
        base = [("move_type", "=", "out_invoice"), ("state", "=", "posted"),
                ("amount_residual", ">", 0.01)]
        all_open = Move.search(base)
        # "All time" still means past due: invoices billed ahead (due after today) are
        # owed but not late, and are reported separately as "Billed ahead, not yet due".
        window = all_open.filtered(
            lambda m: m.invoice_date_due and m.invoice_date_due <= ctx["today"]) if ctx["all"] else all_open.filtered(
            lambda m: m.invoice_date_due and ctx["from"] <= m.invoice_date_due <= ctx["to"])

        win_c, win_v = len(window), sum(window.mapped("amount_residual"))
        total_c, total_v = len(all_open), sum(all_open.mapped("amount_residual"))
        with_prop = all_open.filtered(lambda m: m.property_id)

        buckets = [("0-30 days", 0, 0.0), ("31-60 days", 0, 0.0),
                   ("61-90 days", 0, 0.0), ("90+ days", 0, 0.0)]

        def bucket_i(days):
            return 0 if days <= 30 else 1 if days <= 60 else 2 if days <= 90 else 3

        bcounts = [0, 0, 0, 0]
        bvalues = [0.0, 0.0, 0.0, 0.0]
        for m in window:
            days = (ctx["today"] - m.invoice_date_due).days if m.invoice_date_due else 0
            i = bucket_i(max(days, 0))
            bcounts[i] += 1
            bvalues[i] += m.amount_residual
        bars_age = [[buckets[i][0], bcounts[i], _r(bvalues[i])] for i in range(4) if bcounts[i]]

        by_prop = {}
        for m in window:
            label = m.property_id.name or "No property"
            by_prop[label] = by_prop.get(label, 0.0) + m.amount_residual
        bars_prop = sorted(([k, 0, _r(v)] for k, v in by_prop.items()),
                           key=lambda r: r[2], reverse=True)[:10]

        rows = sorted(window, key=lambda m: m.invoice_date_due or ctx["today"])
        table = [[
            fields.Date.to_string(m.invoice_date_due) if m.invoice_date_due else "-",
            (ctx["today"] - m.invoice_date_due).days if m.invoice_date_due else 0,
            m.property_id.name or "-", m.subproperty_id.display_name or "-",
            m.partner_id.display_name or "-", _r(m.amount_residual),
        ] for m in rows]

        pct_tagged = round(len(with_prop) / total_c * 100.0, 1) if total_c else 0.0
        cav = ["Coverage, not a shortfall",
               "%s of %s open invoices (%.1f%%) carry a property, so the drill-down covers "
               "that share of the value shown here." % (len(with_prop), total_c, pct_tagged)]

        oldest = min(window.mapped("invoice_date_due")) if window and any(window.mapped("invoice_date_due")) else None
        billed_ahead = all_open.filtered(lambda m: m.invoice_date_due and m.invoice_date_due > ctx["today"])
        tenants_open = len(set(all_open.mapped("partner_id").ids))
        avg_past_due = win_v / win_c if win_c else 0.0

        return {
            "unit": "invoices past due", "vunit": "QAR", "prov": "exact",
            "value": win_c, "vtxt": "%s QAR past due" % _r(win_v),
            "action": self._action("Invoices", MOVE,
                                   [("id", "in", window.ids)]),
            "stat": win_c,
            "detail": {
                "subs": [
                    {"k": "In the window", "v": win_c, "x": "%s QAR" % _r(win_v)},
                    {"k": "Total outstanding (all-time)", "v": total_c, "x": "%s QAR" % _r(total_v)},
                    {"k": "Property tagged", "v": "%.1f%%" % pct_tagged, "x": "of open invoices"},
                    {"k": "Oldest unpaid due date in window", "v": fields.Date.to_string(oldest) if oldest else "-",
                     "x": "%s days ago" % (ctx["today"] - oldest).days if oldest else "nothing past due"},
                    {"k": "Billed ahead, not yet due", "v": len(billed_ahead),
                     "x": "%s QAR" % _r(sum(billed_ahead.mapped("amount_residual")))},
                    {"k": "Tenants with an open balance", "v": tenants_open, "x": "across all open invoices"},
                    {"k": "Average past-due invoice", "v": _r(avg_past_due), "x": "QAR"},
                ],
                "bars": [
                    {"title": "Past due, by age", "unit": "QAR", "rows": bars_age},
                    {"title": "Past due by property", "unit": "QAR", "rows": bars_prop, "single": True},
                ],
                "tables": [{"title": "Every past-due invoice in the window",
                            "cols": ["Due date", "Days late", "Property", "Unit", "Tenant", "Residual QAR"],
                            "align": ["id", "n", "", "", "", "n"], "rows": table}],
                "cav": cav,
            },
        }

    # ------------------------------------------------------------ 2. bounced cheques
    def _m_cheques(self, ctx):
        Cheque = self._scoped(CHEQUE)
        Hist = self._scoped(CHQ_HIST)
        bounced = Cheque.search([("check_status", "in", list(BOUNCE_STATES))])
        hist_by_chq = {}
        for h in Hist.search([("chq_id", "in", bounced.ids)]):
            hist_by_chq.setdefault(h.chq_id.id, h)

        def bounce_date(c):
            h = hist_by_chq.get(c.id)
            return h.date if h and h.date else c.cheque_date

        rows_all = [(c, bounce_date(c)) for c in bounced]
        if ctx["all"]:
            window = rows_all
        else:
            window = [(c, d) for c, d in rows_all if d and ctx["from"] <= d <= ctx["to"]]

        win_c = len(window)
        win_v = sum(c.contract_amount for c, _d in window)

        by_route = {}
        for c, _d in window:
            label = ROUTE_LABEL.get(c.check_status, c.check_status or "Unknown")
            by_route[label] = by_route.get(label, 0.0) + c.contract_amount
        bars_route = [[k, 0, _r(v)] for k, v in sorted(by_route.items(), key=lambda kv: -kv[1])]

        by_prop = {}
        for c, _d in window:
            label = c.property_id.name or "No property"
            by_prop[label] = by_prop.get(label, 0.0) + c.contract_amount
        bars_prop = sorted(([k, 0, _r(v)] for k, v in by_prop.items()),
                           key=lambda r: r[2], reverse=True)[:10]

        rows_sorted = sorted(window, key=lambda cd: cd[1] or ctx["today"], reverse=True)
        table = [[
            c.cheque_no or "-", fields.Date.to_string(d) if d else "-",
            ROUTE_LABEL.get(c.check_status, c.check_status or "-"), _r(c.contract_amount),
            c.partner_id.display_name or c.tenant_id.display_name or c.partner or "-",
            c.property_id.name or "-", c.subproperty_id.display_name or "-",
            (hist_by_chq.get(c.id).remarks if hist_by_chq.get(c.id) else "") or "-",
        ] for c, d in rows_sorted]

        # A specific route (cash/bank/legal) means it was actually recovered; plain
        # "returned" is the still-unresolved bounce state.
        recovered_v = sum(c.contract_amount for c, _d in window if c.check_status != "returned")
        unrecovered_v = win_v - recovered_v
        newest = max((d for _c, d in rows_all if d), default=None)
        pending = Cheque.search([("check_status", "=", "not_paid")])
        hist_ids = [hist_by_chq[c.id].id for c, _d in window if c.id in hist_by_chq]

        return {
            "unit": "returned cheques", "vunit": "QAR", "prov": "exact",
            "value": win_c, "vtxt": "%s QAR face value" % _r(win_v),
            "action": self._action("PDC list", CHEQUE,
                                   [("id", "in", [c.id for c, _d in window])]),
            "action2": self._action("CHQ Return History", CHQ_HIST, [("id", "in", hist_ids)]),
            "stat": win_c,
            "detail": {
                "subs": [
                    {"k": "Returned in the window", "v": win_c, "x": "%s QAR" % _r(win_v)},
                    {"k": "All returned on file", "v": len(bounced), "x": ""},
                    {"k": "Face value returned", "v": _r(win_v), "x": "QAR"},
                    {"k": "Recovered since", "v": _r(recovered_v),
                     "x": "%s%%" % round(recovered_v / win_v * 100) if win_v else "QAR"},
                    {"k": "Still unrecovered", "v": _r(unrecovered_v), "x": "QAR"},
                    {"k": "Most recent return on record", "v": fields.Date.to_string(newest) if newest else "-",
                     "x": "%s days ago" % (ctx["today"] - newest).days if newest else ""},
                    {"k": "Cheques pending in the pipeline", "v": len(pending),
                     "x": "%s QAR" % _r(sum(pending.mapped("contract_amount")))},
                ],
                "bars": [
                    {"title": "By return route", "unit": "QAR face", "rows": bars_route},
                    {"title": "Concentration by property", "unit": "QAR face", "rows": bars_prop, "single": True},
                ],
                "tables": [{"title": "Returned cheques in the window",
                            "cols": ["Cheque no", "Return date", "Route", "Face QAR", "Tenant", "Property", "Unit", "Reason"],
                            "align": ["id", "id", "", "n", "", "", "", ""], "rows": table}],
                "cav": ["Bounce reason is free text",
                        "The reason column comes from a plain remarks field, so it can be shown "
                        "per cheque but not grouped or counted reliably. Group by return route instead."],
            },
        }

    # ------------------------------------------------------------ 3. leases nearing expiry
    def _m_leases(self, ctx):
        Contract = self._scoped(CONTRACT)
        live = [("state", "=", "approved")]
        if ctx["all"]:
            ending = Contract.search(live + [("contract_date_to", ">=", ctx["today"])])
        else:
            ending = Contract.search(live + [("contract_date_to", ">=", ctx["fwd_from"]),
                                             ("contract_date_to", "<=", ctx["fwd_to"])])

        # No stored "renewed" flag - inferred from a later contract on the same unit.
        by_unit = {}
        for c in Contract.search([("subproperty_id", "in", ending.mapped("subproperty_id").ids)]):
            by_unit.setdefault(c.subproperty_id.id, []).append(c)

        def is_renewed(c):
            for other in by_unit.get(c.subproperty_id.id, []):
                if other.id != c.id and other.contract_date_from and c.contract_date_to \
                        and other.contract_date_from > c.contract_date_to:
                    return True
            return False

        renewed = [c for c in ending if is_renewed(c)]
        not_renewed = [c for c in ending if c.id not in {r.id for r in renewed}]
        value = sum(ending.mapped("actual_rent"))

        by_month = {}
        for c in ending:
            label = c.contract_date_to.strftime("%b %Y") if c.contract_date_to else "-"
            by_month.setdefault(label, [0, 0.0])
            by_month[label][0] += 1
            by_month[label][1] += c.actual_rent
        bars_month = [[k, v[0], _r(v[1])] for k, v in by_month.items()]

        runway = [["Next 30 days", 0, 0.0], ["31-60 days", 0, 0.0], ["61-90 days", 0, 0.0], ["90+ days", 0, 0.0]]
        for c in ending:
            d = (c.contract_date_to - ctx["today"]).days if c.contract_date_to else 0
            i = 0 if d <= 30 else 1 if d <= 60 else 2 if d <= 90 else 3
            runway[i][1] += 1
            runway[i][2] += c.actual_rent
        bars_runway = [[r[0], r[1], _r(r[2])] for r in runway if r[1]]

        total_active = Contract.search_count(live)
        expired_open = Contract.search(live + [("contract_date_to", "<", ctx["today"])])

        rows = sorted(ending, key=lambda c: c.contract_date_to or ctx["today"])
        table = [[
            c.property_id.name or "-", c.subproperty_id.display_name or "-",
            c.tenant_id.display_name or "-",
            fields.Date.to_string(c.contract_date_to) if c.contract_date_to else "-",
            (c.contract_date_to - ctx["today"]).days if c.contract_date_to else 0,
            _r(c.actual_rent), "Yes" if c.id in {r.id for r in renewed} else "No",
        ] for c in rows]

        return {
            "unit": "leases ending", "vunit": "QAR", "prov": "exact",
            "value": len(ending), "vtxt": "%s QAR rent at risk - %s unrenewed" % (_r(value), len(not_renewed)),
            "action": self._action("Contract", CONTRACT, [("id", "in", ending.ids)]),
            "stat": len(not_renewed),
            "detail": {
                "subs": [
                    {"k": "Ending in window", "v": len(ending), "x": "%s QAR/month" % _r(value)},
                    {"k": "Renewed (successor contract on file)", "v": len(renewed), "x": ""},
                    {"k": "Not yet renewed", "v": len(not_renewed), "x": ""},
                    {"k": "Active contracts in total", "v": total_active, "x": "of all contracts on file"},
                    {"k": "Already past their end date", "v": len(expired_open),
                     "x": "%s QAR/month" % _r(sum(expired_open.mapped("actual_rent")))},
                ],
                "bars": [
                    {"title": "Expiry runway in window", "unit": "QAR rent", "rows": bars_runway},
                    {"title": "Expiry runway, by month", "unit": "QAR rent", "rows": bars_month},
                ],
                "tables": [{"title": "Leases ending in the window",
                            "cols": ["Property", "Unit", "Tenant", "Ends", "Days", "Rent QAR", "Renewed"],
                            "align": ["", "", "", "id", "n", "n", ""], "rows": table}],
                "cav": ["Renewal is inferred, not stored",
                        "There is no renewed flag on the contract. “Renewed” here means a later "
                        "contract exists on the same unit; a renewal recorded by extending the same "
                        "contract instead would not be counted."],
            },
        }

    # ------------------------------------------------------------ 4. vacant units
    def _m_vacant(self, ctx):
        Unit = self._scoped(UNIT)
        own = self._own_companies()
        vacant_all = Unit.search(own + [("is_occupied", "=", "vacant")])
        if ctx["all"]:
            window = vacant_all
        else:
            window = vacant_all.filtered(
                lambda u: u.date_to and ctx["from"] <= u.date_to <= ctx["to"])

        with_date = vacant_all.filtered(lambda u: u.date_to)
        idle_rent = sum(vacant_all.mapped("price"))

        buckets = [("0-30 days", 0), ("31-90 days", 0), ("91-180 days", 0), ("180+ days", 0)]
        bcounts = [0, 0, 0, 0]
        for u in with_date:
            days = (ctx["today"] - u.date_to).days
            i = 0 if days <= 30 else 1 if days <= 90 else 2 if days <= 180 else 3
            bcounts[i] += 1
        bars_age = [[buckets[i][0], bcounts[i], bcounts[i]] for i in range(4) if bcounts[i]]

        by_prop = {}
        for u in vacant_all:
            label = u.property_id.name or "No property"
            by_prop[label] = by_prop.get(label, 0.0) + (u.price or 0.0)
        bars_prop = sorted(([k, 0, _r(v)] for k, v in by_prop.items()),
                           key=lambda r: r[2], reverse=True)[:10]

        by_type = {}
        for u in vacant_all:
            label = u.type_id.name or "Unspecified"
            by_type[label] = by_type.get(label, 0) + 1

        avg_days = round(sum((ctx["today"] - u.date_to).days for u in with_date) / len(with_date), 0) \
            if with_date else 0
        longest = max(with_date, key=lambda u: (ctx["today"] - u.date_to).days) if with_date else None
        occupied_c = Unit.search_count(own + [("is_occupied", "=", "occupied")])
        allocated_c = Unit.search_count(own + [("is_occupied", "=", "allocated")])

        rows = sorted(with_date, key=lambda u: u.date_to)
        table = [[
            fields.Date.to_string(u.date_to), (ctx["today"] - u.date_to).days,
            u.property_id.name or "-", u.display_name or "-",
            u.type_id.name or "-", _r(u.price),
        ] for u in rows]

        return {
            "unit": "units fell vacant" if not ctx["all"] else "vacant units", "vunit": "QAR", "prov": "exact",
            "value": len(window), "vtxt": "%s QAR/month idle - avg %s days empty" % (_r(idle_rent), avg_days),
            "action": self._action("Vacant Units", UNIT, [("id", "in", vacant_all.ids)]),
            "stat": len(window) if not ctx["all"] else len(vacant_all),
            "detail": {
                "subs": [
                    {"k": "Vacant now (all-time)", "v": len(vacant_all), "x": "%s QAR/month idle" % _r(idle_rent)},
                    {"k": "Fell vacant in window", "v": len(window), "x": ""},
                    {"k": "Average days empty", "v": avg_days, "x": "of %s dated units" % len(with_date)},
                    {"k": "Occupied / Allocated", "v": "%s / %s" % (occupied_c, allocated_c), "x": "of all units"},
                    {"k": "Longest standing vacancy", "v": (ctx["today"] - longest.date_to).days if longest else "-",
                     "x": "days - %s" % (longest.display_name or "-") if longest else ""},
                ],
                "bars": [
                    {"title": "How long they have stood empty", "unit": "units", "rows": bars_age},
                    {"title": "Idle asking rent by property", "unit": "QAR/month", "rows": bars_prop, "single": True},
                ],
                "tables": [
                    {"title": "Vacancy start dates", "cols": ["Vacant since", "Days empty", "Property", "Unit", "Type", "Asking rent QAR"],
                     "align": ["id", "n", "", "", "", "n"], "rows": table},
                    {"title": "Vacant units by type", "cols": ["Unit type", "Vacant units"], "align": ["", "n"],
                     "rows": [[k, v] for k, v in sorted(by_type.items(), key=lambda kv: -kv[1])]},
                ],
                "cav": ["Vacancy days are computed, not stored",
                        "“Vacant since” is populated on %s of %s vacant units; the rest cannot show a "
                        "days-empty figure until that date is entered." % (len(with_date), len(vacant_all))],
            },
        }

    # ------------------------------------------------------------ 5. leases below target rent
    def _m_below(self, ctx):
        Unit = self._scoped(UNIT)
        own = self._own_companies()
        occupied = Unit.search(own + [("is_occupied", "=", "occupied")])
        vacant = Unit.search(own + [("is_occupied", "=", "vacant")])
        candidates = occupied + vacant
        below = candidates.filtered(lambda u: u.actual_rent and u.price and u.actual_rent < u.price)
        below_occ = below.filtered(lambda u: u.is_occupied == "occupied")
        below_vac = below.filtered(lambda u: u.is_occupied == "vacant")

        gap_total = sum(u.price - u.actual_rent for u in below)

        by_prop = {}
        for u in below:
            label = u.property_id.name or "No property"
            by_prop[label] = by_prop.get(label, 0.0) + (u.price - u.actual_rent)
        bars_prop = sorted(([k, 0, _r(v)] for k, v in by_prop.items()),
                           key=lambda r: r[2], reverse=True)[:10]

        Contract = self._scoped(CONTRACT)
        live_by_unit = {}
        for u in below:
            live_by_unit[u.id] = Contract.search([("subproperty_id", "=", u.id), ("state", "=", "approved")],
                                                 order="contract_date_to desc", limit=1)

        rows = sorted(below, key=lambda u: (u.price - u.actual_rent), reverse=True)
        table = []
        pct_list = []
        for u in rows:
            gap = u.price - u.actual_rent
            pct = round(gap / u.price * 100.0, 2) if u.price else 0.0
            pct_list.append((pct, u))
            live = live_by_unit.get(u.id)
            table.append([
                u.property_id.name or "-", u.display_name or "-",
                u.tenant_id.display_name or (live.tenant_id.display_name if live else "") or "-",
                u.type_id.name or "-",
                _r(u.actual_rent), _r(u.price), _r(gap), "%.2f%%" % pct,
                "Vacant" if u.is_occupied == "vacant" else "Occupied",
                fields.Date.to_string(live.contract_date_to) if live and live.contract_date_to else "-",
            ])

        avg_pct = round(sum(p for p, _u in pct_list) / len(pct_list), 2) if pct_list else 0.0
        widest = max(pct_list, key=lambda pu: pu[0]) if pct_list else None
        contract_ids = [c.id for c in live_by_unit.values() if c]

        return {
            "unit": "units under target", "vunit": "QAR", "prov": "snap",
            "value": len(below), "vtxt": "%s QAR/month forgone - %s a year" % (_r(gap_total), _r(gap_total * 12)),
            "action": self._action("Sub-Property (Units/Flats)", UNIT, [("id", "in", below.ids)]),
            "action2": self._action("Contract", CONTRACT, [("id", "in", contract_ids)]),
            "stat": len(below),
            "detail": {
                "subs": [
                    {"k": "Units under target", "v": len(below), "x": "of %s occupied + vacant" % len(candidates)},
                    {"k": "Monthly gap", "v": "%s QAR" % _r(gap_total), "x": "%s QAR/year" % _r(gap_total * 12)},
                    {"k": "Average shortfall", "v": "%.2f%%" % avg_pct, "x": "below Ideal Rent"},
                    {"k": "Widest shortfall", "v": "%.2f%%" % widest[0] if widest else "-",
                     "x": (widest[1].display_name or "-") if widest else ""},
                    {"k": "Occupied vs vacant", "v": "%s / %s" % (len(below_occ), len(below_vac)),
                     "x": "of the units under target"},
                ],
                "bars": [{"title": "Monthly gap by property", "unit": "QAR", "rows": bars_prop, "single": True}],
                "tables": [{"title": "Widest gaps",
                            "cols": ["Property", "Unit", "Tenant", "Type", "Contracted QAR", "Target QAR", "Gap QAR", "Gap %", "State", "Next renewal"],
                            "align": ["", "", "", "", "n", "n", "n", "n", "", "id"], "rows": table}],
                "cav": ["A snapshot, not a window",
                        "Contracted and target rent have no date on them, so this reads today's gap "
                        "regardless of the date range chosen above."],
            },
        }

    # ------------------------------------------------------------ 6. cost overview
    def _m_cost(self, ctx):
        Line = self._scoped(MOVE_LINE)
        expense_types = ("expense", "expense_depreciation", "expense_direct_cost")
        base = [("parent_state", "=", "posted"), ("account_id.account_type", "in", expense_types)]

        def window_domain(a, b):
            return base + [("date", ">=", a), ("date", "<=", b)]

        if ctx["all"]:
            lines = Line.search(base)
            cur_total = sum(lines.mapped("balance"))
            prev_total, span = None, 0
        else:
            lines = Line.search(window_domain(ctx["from"], ctx["to"]))
            cur_total = sum(lines.mapped("balance"))
            span = (ctx["to"] - ctx["from"]).days
            prev_a, prev_b = ctx["from"] - timedelta(days=span + 1), ctx["from"] - timedelta(days=1)
            prev_lines = Line.search(window_domain(prev_a, prev_b))
            prev_total = sum(prev_lines.mapped("balance"))

        mv = ((cur_total - prev_total) / prev_total * 100.0) if prev_total else None

        all_total = sum(Line.search(base).mapped("balance"))

        yoy_total = None
        if not ctx["all"]:
            yoy_a = ctx["from"].replace(year=ctx["from"].year - 1) if not (ctx["from"].month == 2 and ctx["from"].day == 29) \
                else ctx["from"].replace(year=ctx["from"].year - 1, day=28)
            yoy_b = ctx["to"].replace(year=ctx["to"].year - 1) if not (ctx["to"].month == 2 and ctx["to"].day == 29) \
                else ctx["to"].replace(year=ctx["to"].year - 1, day=28)
            yoy_total = sum(Line.search(window_domain(yoy_a, yoy_b)).mapped("balance"))

        trend_from = ctx["today"].replace(day=1) - timedelta(days=365)
        trend_lines = Line.search(base + [("date", ">=", trend_from), ("date", "<=", ctx["today"])])
        by_month_cost = {}
        for l in trend_lines:
            label = l.date.strftime("%b %Y") if l.date else "-"
            by_month_cost.setdefault(label, [0, 0.0])
            by_month_cost[label][0] += 1
            by_month_cost[label][1] += l.balance
        bars_month_cost = [[k, v[0], _r(v[1])] for k, v in by_month_cost.items()]

        by_prop = {}
        for l in lines:
            label = l.property_id.name or "No property"
            by_prop[label] = by_prop.get(label, 0.0) + l.balance
        bars_prop = sorted(([k, 0, _r(v)] for k, v in by_prop.items()),
                           key=lambda r: r[2], reverse=True)[:10]

        by_unit = {}
        for l in lines:
            if l.subproperty_id:
                by_unit.setdefault(l.subproperty_id, [0, 0.0])
                by_unit[l.subproperty_id][0] += 1
                by_unit[l.subproperty_id][1] += l.balance
        unit_sorted = sorted(by_unit.items(), key=lambda kv: kv[1][1], reverse=True)
        bars_unit = [[u.display_name or "-", v[0], _r(v[1])] for u, v in unit_sorted[:10]]
        table_unit = [[u.property_id.name or "-", u.display_name or "-", v[0], _r(v[1])]
                      for u, v in unit_sorted]
        untagged_unit_v = sum(l.balance for l in lines if not l.subproperty_id)
        pct_unit = round(len(lines.filtered(lambda l: l.subproperty_id)) / len(lines) * 100.0, 1) \
            if lines else 0.0

        tagged_prop = lines.filtered(lambda l: l.property_id)
        pct_tagged = round(len(tagged_prop) / len(lines) * 100.0, 1) if lines else 0.0

        Budget = self._scoped(BUDGET_LINE)
        budget_lines = Budget.search([])
        budget_rows = []
        for b in budget_lines:
            budget_rows.append([
                b.crossovered_budget_id.name or "-",
                "%s to %s" % (fields.Date.to_string(b.date_from), fields.Date.to_string(b.date_to)),
                _r(b.planned_amount), _r(b.practical_amount),
            ])
        planned_total = sum(budget_lines.mapped("planned_amount"))
        actual_total = sum(budget_lines.mapped("practical_amount"))
        # Actual (practical_amount) is computed as the user and reads the line's analytic
        # account, so a line pointing at another company's analytic account makes the whole
        # Budget Items list raise an access error. Keep those out of the link and name them.
        cross_co = budget_lines.filtered(
            lambda b: b.analytic_account_id.company_id
            and b.analytic_account_id.company_id not in self.env.companies)

        return {
            "unit": "QAR expense", "vunit": "QAR", "prov": "exact",
            "value": cur_total, "vtxt": (
                "no comparable prior window" if mv is None else
                "%+.1f%% vs prior %s days" % (mv, span)),
            "action": self._action("Journal Items", MOVE_LINE,
                                   [("id", "in", lines.ids)]),
            "action2": self._action("Budget Items", BUDGET_LINE, [("id", "in", (budget_lines - cross_co).ids)]),
            "stat": abs(mv) if mv is not None else 0,
            "detail": {
                "subs": [
                    {"k": "Expense in window", "v": "%s QAR" % _r(cur_total), "x": ""},
                    {"k": "Prior equal window", "v": "%s QAR" % _r(prev_total) if prev_total is not None else "-",
                     "x": "%s days before" % span if span else ""},
                    {"k": "Movement", "v": "%+.1f%%" % mv if mv is not None else "-",
                     "x": "%s QAR" % _r(cur_total - prev_total) if prev_total is not None else ""},
                    {"k": "Same period, prior year", "v": "%s QAR" % _r(yoy_total) if yoy_total is not None else "-",
                     "x": ("%+.1f%% YoY" % ((cur_total - yoy_total) / yoy_total * 100.0) if yoy_total else "")},
                    {"k": "Property tagged", "v": "%.1f%%" % pct_tagged, "x": "of expense lines"},
                    {"k": "Unit tagged", "v": "%.1f%%" % pct_unit,
                     "x": "%s QAR has no unit" % _r(untagged_unit_v)},
                    {"k": "All posted expense", "v": "%s QAR" % _r(all_total), "x": "all-time"},
                    {"k": "Budgeted (all budgets on file)", "v": "%s QAR" % _r(planned_total),
                     "x": "actual %s QAR" % _r(actual_total)},
                ],
                "bars": [
                    {"title": "Expense by month", "unit": "QAR", "rows": bars_month_cost},
                    {"title": "Expense by property", "unit": "QAR", "rows": bars_prop, "single": True},
                    {"title": "Expense by unit (top 10)", "unit": "QAR", "rows": bars_unit, "single": True},
                ],
                "tables": [
                    {"title": "Expense by unit - every unit in the window",
                     "cols": ["Property", "Unit", "Lines", "Expense QAR"],
                     "align": ["", "", "n", "n"], "rows": table_unit},
                    {"title": "Budget versus actual - every budget on file",
                     "cols": ["Budget", "Period", "Planned QAR", "Actual QAR"],
                     "align": ["", "id", "n", "n"], "rows": budget_rows},
                ],
                "cav": ["Only as complete as the tagging",
                        "%.1f%% of expense lines carry a property link, so the property breakdown is "
                        "only that complete; untagged cost is real but invisible to this split." % pct_tagged,
                        "%.1f%% of expense lines carry a unit, so the unit breakdown is only that "
                        "complete; the rest is cost with no unit on the journal line." % pct_unit] + ([
                        "%s budget line(s) point to another company's analytic account (%s), so Odoo's "
                        "budget screens raise an access error on them; they are counted above but left "
                        "out of the Budget Items link until the analytic account is corrected." % (
                            len(cross_co), ", ".join(
                                "line %s %s / %s" % (b.id, b.analytic_account_id.name,
                                                     b.analytic_account_id.company_id.name)
                                for b in cross_co))] if cross_co else []),
            },
        }

    # ------------------------------------------------------------ 7. unpaid dues
    def _m_dues(self, ctx):
        Move = self._scoped(MOVE)
        base = [("move_type", "=", "out_invoice"), ("state", "=", "posted"),
                ("amount_residual", ">", 0.01)]
        if ctx["all"]:
            invs = Move.search(base)
        else:
            lo, hi = ctx["from"], (ctx["fwd_to"] or ctx["to"])
            invs = Move.search(base + [("invoice_date_due", ">=", lo), ("invoice_date_due", "<=", hi)])

        Account = self._scoped(ACCOUNT)
        maint_accounts = Account.search([
            ("name", "ilike", "maintenance"), ("name", "ilike", "income")])
        maint_moves = set(Move.search([
            ("id", "in", invs.ids), ("invoice_line_ids.account_id", "in", maint_accounts.ids)]).ids)

        util_moves = invs.filtered(lambda m: m.is_electricity)
        tel_moves = invs.filtered(lambda m: m.is_telecom)
        util_ids = set(util_moves.ids) | set(tel_moves.ids)

        maint_v = sum(m.amount_residual for m in invs if m.id in maint_moves)
        util_v = sum(m.amount_residual for m in invs if m.id in util_ids)
        rent_v = sum(m.amount_residual for m in invs
                     if m.id not in maint_moves and m.id not in util_ids)

        state_labels = dict(Move.fields_get(["payment_state"])["payment_state"]["selection"])
        by_state = {}
        for m in invs:
            label = state_labels.get(m.payment_state, m.payment_state)
            by_state.setdefault(label, [0, 0.0])
            by_state[label][0] += 1
            by_state[label][1] += m.amount_residual
        table_state = [[k, v[0], _r(v[1])] for k, v in by_state.items()]

        bars_type = [["Rent", 0, _r(rent_v)], ["Maintenance", 0, _r(maint_v)], ["Utilities/Telecom", 0, _r(util_v)]]

        already_due = invs.filtered(lambda m: m.invoice_date_due and m.invoice_date_due <= ctx["today"])
        still_future = invs - already_due

        age_buckets = [("0-30 days", 0, 0.0), ("31-60 days", 0, 0.0), ("61-90 days", 0, 0.0), ("90+ days", 0, 0.0)]
        acounts = [0, 0, 0, 0]
        avalues = [0.0, 0.0, 0.0, 0.0]
        for m in already_due:
            d = (ctx["today"] - m.invoice_date_due).days
            i = 0 if d <= 30 else 1 if d <= 60 else 2 if d <= 90 else 3
            acounts[i] += 1
            avalues[i] += m.amount_residual
        bars_age = [[age_buckets[i][0], acounts[i], _r(avalues[i])] for i in range(4) if acounts[i]]

        id_table = [
            ["Rent", "Everything not identified as maintenance or utilities", "%s" % _r(rent_v), "Measurable"],
            ["Maintenance", "Invoice lines posted to a maintenance-income account (matched by name)",
             "%s" % _r(maint_v), "Measurable"],
            ["Utilities / Telecom", "is_electricity / is_telecom flags on the invoice",
             "%s" % _r(util_v), "Measurable" if util_v else "Flag unset on open invoices"],
        ]

        electricity_set = Move.search_count([("is_electricity", "=", True)])
        cav = ["Charge type is inferred, not tagged",
               "The invoice line carries no charge-type field, so “Maintenance” and "
               "“Utilities” come from dedicated income accounts and the is_electricity/"
               "is_telecom flags; everything else is treated as rent."]
        if not electricity_set:
            cav.append("is_electricity is not yet set on any invoice, so utilities read low "
                       "until that flag is used.")

        return {
            "unit": "invoices falling due", "vunit": "QAR", "prov": "exact",
            "value": len(invs), "vtxt": "%s QAR - %s invoices" % (_r(rent_v + maint_v + util_v), len(invs)),
            "action": self._action("Invoices", MOVE, [("id", "in", invs.ids)]),
            "stat": rent_v + maint_v + util_v,
            "detail": {
                "subs": [
                    {"k": "Falling due in window", "v": len(invs), "x": "%s QAR" % _r(rent_v + maint_v + util_v)},
                    {"k": "Already past due", "v": len(already_due),
                     "x": "%s QAR" % _r(sum(already_due.mapped("amount_residual")))},
                    {"k": "Still to fall due", "v": len(still_future),
                     "x": "%s QAR" % _r(sum(still_future.mapped("amount_residual")))},
                    {"k": "Rent", "v": "%s QAR" % _r(rent_v), "x": ""},
                    {"k": "Maintenance", "v": "%s QAR" % _r(maint_v), "x": ""},
                    {"k": "Utilities / telecom", "v": "%s QAR" % _r(util_v), "x": ""},
                ],
                "bars": [
                    {"title": "Ageing of past-due dues", "unit": "QAR", "rows": bars_age},
                    {"title": "By charge type", "unit": "QAR", "rows": bars_type},
                ],
                "tables": [
                    {"title": "Charge type - how each one is identified",
                     "cols": ["Charge type", "Identified by", "Open QAR", "State"],
                     "align": ["", "", "n", ""], "rows": id_table},
                    {"title": "Collection status - all invoices in window",
                     "cols": ["Payment state", "Invoices", "Outstanding QAR"], "align": ["", "n", "n"],
                     "rows": table_state},
                ],
                "cav": cav,
            },
        }

    # ------------------------------------------------------------ 8. unearned income
    def _m_unearned(self, ctx):
        Account = self._scoped(ACCOUNT)
        Line = self._scoped(MOVE_LINE)
        accounts = Account.search([("name", "ilike", "Unearned")])
        base = [("account_id", "in", accounts.ids), ("parent_state", "=", "posted")]

        all_lines = Line.search(base)
        balance = sum(all_lines.mapped("balance")) * -1  # liability: credit-heavy is positive

        if ctx["all"]:
            window_lines = all_lines
        else:
            window_lines = Line.search(base + [("date", ">=", ctx["from"]), ("date", "<=", ctx["to"])])
        received = sum(window_lines.mapped("credit"))
        recognised = sum(window_lines.mapped("debit"))

        rows = []
        for a in accounts:
            a_lines = all_lines.filtered(lambda l: l.account_id.id == a.id)
            rows.append([a.code or "-", a.name or "-", len(a_lines),
                        _r(sum(a_lines.mapped("credit")) - sum(a_lines.mapped("debit"))),
                        "Yes" if a_lines else "No"])

        with_unit = window_lines.filtered(lambda l: l.subproperty_id)
        pct_unit = round(len(with_unit) / len(window_lines) * 100.0, 1) if window_lines else 0.0

        received_all_time = sum(all_lines.mapped("credit"))
        in_use = len([r for r in rows if r[4] == "Yes"])

        Move = self._scoped(MOVE)
        billed_ahead = Move.search([("move_type", "=", "out_invoice"), ("state", "=", "posted"),
                                    ("amount_residual", ">", 0.01),
                                    ("invoice_date_due", ">", ctx["today"])])

        by_period = {}
        for l in window_lines:
            label = l.date.strftime("%b %Y") if l.date else "-"
            by_period.setdefault(label, [0, 0.0])
            by_period[label][0] += 1
            by_period[label][1] += (l.credit - l.debit)
        bars_period = [[k, v[0], _r(v[1])] for k, v in sorted(by_period.items())]

        by_year = {}
        for l in all_lines:
            label = str(l.date.year) if l.date else "-"
            by_year.setdefault(label, [0, 0.0, 0.0])
            by_year[label][0] += 1
            by_year[label][1] += l.credit
            by_year[label][2] += l.debit
        year_rows = [[k, v[0], _r(v[1]), _r(v[2]), _r(v[1] - v[2])] for k, v in sorted(by_year.items())]

        return {
            "unit": "QAR recognised", "vunit": "QAR", "prov": "exact",
            "value": recognised, "vtxt": "%s QAR received in advance - balance %s" % (_r(received), _r(balance)),
            "action": self._action("Journal Items", MOVE_LINE, [("id", "in", all_lines.ids)]),
            "action2": self._action("Chart of Accounts", ACCOUNT, [("id", "in", accounts.ids)]),
            "stat": abs(balance),
            "detail": {
                "subs": [
                    {"k": "Remaining unearned balance", "v": "%s QAR" % _r(balance), "x": ""},
                    {"k": "Received in advance (window)", "v": "%s QAR" % _r(received), "x": ""},
                    {"k": "Recognised as income (window)", "v": "%s QAR" % _r(recognised), "x": ""},
                    {"k": "Received in advance, all time", "v": "%s QAR" % _r(received_all_time), "x": ""},
                    {"k": "Posted lines on these accounts", "v": len(all_lines),
                     "x": "across %s of %s accounts" % (in_use, len(accounts))},
                    {"k": "Billed ahead but not parked here", "v": len(billed_ahead),
                     "x": "%s QAR on invoices not yet due" % _r(sum(billed_ahead.mapped("amount_residual")))},
                ],
                "bars": [
                    {"title": "Movement by period", "unit": "QAR", "rows": bars_period},
                    {"title": "Movement by account", "unit": "QAR", "single": True,
                     "rows": [[r[1], 0, r[3]] for r in rows]},
                ],
                "tables": [
                    {"title": "Recognition period - movement by year",
                     "cols": ["Year", "Lines", "Received in advance", "Recognised", "Net"],
                     "align": ["id", "n", "n", "n", "n"], "rows": year_rows},
                    {"title": "Accounts in the Unearned Revenue block",
                     "cols": ["Code", "Account", "Lines", "Value moved", "In use"],
                     "align": ["id", "", "n", "n", ""], "rows": rows},
                ],
                "cav": ["Unit and tenant need a join",
                        "The account is split per property, so that level works directly. Unit and "
                        "tenant are not on the journal entry - only %.1f%% of window lines carry a "
                        "unit - reaching them means joining back to the originating invoice." % pct_unit,
                        "%s invoices worth %s QAR are billed ahead of their due date. If advance rent is "
                        "going straight to income rather than being parked on these accounts, that gap "
                        "would not show up in the balance above." %
                        (len(billed_ahead), _r(sum(billed_ahead.mapped("amount_residual"))))],
            },
        }
