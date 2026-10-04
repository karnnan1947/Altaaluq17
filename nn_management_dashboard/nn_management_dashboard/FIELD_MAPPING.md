# Management Dashboard - field mapping and filters

How each card is computed (model, fields, filter) and how to check it by hand
from the records in Odoo. Every figure follows the companies selected in the
company switcher; select **only one company** when checking against a list.

The window is the date range shown on the dashboard ("Window: ..."). For a
preset of N days it is today - N to today; lease expiry and unpaid dues also
look forward to today + N.

Tip: the link button at the top right of each card (Invoices, PDC list,
Contract, ...) opens exactly the records the card counted. Comparing that list
with your own filtered list is the quickest check.

---

## 01 Rent Arrears

| | |
|---|---|
| Model | `account.move` (Customer Invoices) |
| Filter | `move_type = out_invoice`, `state = posted`, `amount_residual > 0.01` |
| Window | `invoice_date_due` between window start and window end. **All time**: `invoice_date_due <= today` (past due only) |
| Count / QAR | number of invoices / sum of `amount_residual` (Amount Due) |
| Property / Unit / Tenant | `property_id`, `subproperty_id`, `partner_id` |
| Total outstanding (all-time) | same filter, no date - includes invoices not yet due |
| Billed ahead, not yet due | `invoice_date_due > today` |

Check:

1. Go to Accounting > Customers > Invoices.
2. Filter Posted, plus Not Paid or Partially Paid.
3. Add a custom filter: Due Date between the window's start date and today
   (All time: Due Date on or before today).
4. Compare the record count with the card's count, and the **Amount Due**
   column total with the card's QAR. Use Amount Due, not Total, because partly
   paid invoices only count what is still owed.
5. Group By > Property and compare with the "Past due by property" chart.

## 02 Bounced Cheques

| | |
|---|---|
| Model | `nn.pdc.cheque` (PDC), return details from `cheq.history` |
| Filter | `check_status` in `returned`, `returned_cash`, `returned_bank`, `returned_legal`; active cheques only |
| Return date | first `cheq.history.date` for the cheque, else `cheque_date` |
| Window | return date between window start and end |
| Count / QAR | number of cheques / sum of `contract_amount` (CHQ amount, face value) |
| Tenant | `partner_id`, else `tenant_id`, else `partner` (text) |
| Property / Unit | `property_id`, `subproperty_id` |
| Reason | `cheq.history.remarks` (free text) |
| Recovered since | face value of cheques in `returned_cash` / `returned_bank` / `returned_legal` |

Check:

1. Go to PMS > Reporting > PDC List. Use the card's "PDC list" link instead,
   because this menu hides two of the returned statuses.
2. Group By > Status and add up the four Returned statuses: Returned, Cash Pay,
   Bank Transfer and Legal Action.
3. Compare the count, and the CHQ Amount total, with the card.
4. To check one cheque's date and reason, open it and look at CHQ Return History.

## 03 Leases Nearing Expiry

| | |
|---|---|
| Model | `property.contract` |
| Filter | `state = approved` (Active) |
| Window | `contract_date_to` between today and today + N days. **All time**: `contract_date_to >= today` |
| Count / QAR | number of contracts / sum of `actual_rent` (Contract Rent) |
| Renewed | another contract on the same `subproperty_id` whose `contract_date_from` is after this one's `contract_date_to` (no stored flag) |

Check:

1. Go to PMS > Contract.
2. Filter Status = Active, and Contract End Date between today and today + 30,
   60 or 90 days.
3. Compare the count and the Contract Rent total with the card.

## 04 Vacant Units

| | |
|---|---|
| Model | `real.estate.subproperty` (units) |
| Filter | `is_occupied = vacant`, `company_id` in the selected companies |
| Vacant since | `date_to` |
| Window | `date_to` between window start and end (units that fell vacant). **All time**: every vacant unit |
| Days empty | today - `date_to` (units without `date_to` are left out of the average) |
| Asking rent | `price` (Ideal Rent(QAR)) |
| Unit type | `type_id` |

Check:

1. Go to PMS > Reporting > Vacant Units.
2. Add Group By > Company and read only the selected company's group.
3. Open a unit and check that Date To = "Vacant since" and Ideal Rent = "Asking rent".
4. Days empty = today minus Date To.

## 05 Leases Below Target Rent

| | |
|---|---|
| Model | `real.estate.subproperty` |
| Filter | `is_occupied` in `occupied`, `vacant`; `company_id` in the selected companies; `actual_rent > 0`, `price > 0`, `actual_rent < price` |
| Window | none - a snapshot of today |
| Gap | `price - actual_rent` (Ideal - Actual) |
| Gap % | gap / `price` x 100, 2 decimals |
| Tenant | `tenant_id`, else the tenant of the unit's Active contract |
| Next renewal | `contract_date_to` of the unit's latest Active contract |

Check:

1. Go to the PMS units list and show the Actual Rent(QAR) and Ideal Rent(QAR) columns.
2. Count the occupied and vacant units where Actual is lower than Ideal, only
   those of the selected company.
3. Gap = Ideal - Actual. Gap % = Gap / Ideal x 100. For example, AB02 31:
   6,000 - 5,000 = 1,000, which is 16.67%.

## 06 Cost Overview

| | |
|---|---|
| Model | `account.move.line` (Journal Items) |
| Filter | `parent_state = posted`, `account_id.account_type` in `expense`, `expense_depreciation`, `expense_direct_cost` |
| Window | `date` between window start and end |
| QAR | sum of `balance` |
| Prior equal window | same filter, the same span immediately before the window |
| By property / unit | `property_id`, `subproperty_id` |
| Budget versus actual | `crossovered.budget.lines`: `planned_amount`, `practical_amount` (all budget lines, not windowed) |

Check:

1. Go to Accounting > Accounting > Journal Items.
2. Filter Posted, Account Type = Expenses, Depreciation or Cost of Revenue, and
   Date = the window.
3. Compare the Balance total with the card.
4. Group By > Property and Sub Property to check the two expense charts.
5. For "Prior equal window", repeat with the same number of days just before the window.
6. For budgets, compare with Accounting > Management > Budgets, Planned and Actual.

Budget lines whose analytic account belongs to another company are counted in
the totals but left out of the Budget Items link (Odoo's budget screens raise
an access error on them); the card's note names them.

## 07 Unpaid Dues

| | |
|---|---|
| Model | `account.move` |
| Filter | `move_type = out_invoice`, `state = posted`, `amount_residual > 0.01` |
| Window | `invoice_date_due` from window start to the end of the forward window (today + N). **All time**: no date |
| Maintenance | invoices with a line on an account whose name contains "maintenance" and "income" (e.g. 4200 Maintenance Charges Income) |
| Utilities / telecom | `is_electricity` or `is_telecom` set on the invoice |
| Rent | everything else (other tenant dues included) |
| Collection status | grouped by `payment_state` |

Check:

1. Use the same invoice filter as card 01, but set the Due Date range from the
   window's start to the end of the forward window. For 90d on 1 Oct 2026 that
   is 3 Jul to 30 Dec 2026.
2. Group By > Payment Status and compare with the "Collection status" table.
3. Maintenance = invoices with a line on account 4200 Maintenance Charges Income.

## 08 Unearned Income

| | |
|---|---|
| Model | `account.move.line`, accounts from `account.account` |
| Accounts | name contains "Unearned" (2320 - 2326) |
| Filter | `parent_state = posted` |
| Received in advance | sum of `credit` in the window |
| Recognised as income | sum of `debit` in the window (the card's headline) |
| Remaining balance | (sum of `credit` - sum of `debit`) on all lines, any date |

Check:

1. Go to Journal Items, filter Account containing "Unearned", Posted, and Date = the window.
2. Total Credit = "Received in advance". Total Debit = "Recognised".
3. Accounting > Configuration > Chart of Accounts, searching "Unearned", gives
   the remaining balance.
