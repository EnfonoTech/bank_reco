"""Banking reports in one place.

Thin wrapper over ERPNext's own query reports so the Bank Reco screen can show
them next to the reconciliation without sending users to the report builder.
Report permissions are enforced by ``frappe.desk.query_report.run`` itself.
"""

import frappe
from frappe import _
from frappe.desk.query_report import run as run_query_report
from frappe.utils import getdate, nowdate

from bank_reco.api.permission import assert_bank_account_access

REPORTS = (
	"Bank Reconciliation Statement",
	"Bank Clearance Summary",
	"Cheques and Deposits Incorrectly cleared",
)


def _report_filters(report_name, bank_account_doc, from_date, to_date):
	account = bank_account_doc.account
	if report_name == "Bank Clearance Summary":
		return {"account": account, "from_date": from_date, "to_date": to_date}
	return {
		"company": bank_account_doc.company,
		"account": account,
		"report_date": to_date,
	}


@frappe.whitelist()
def get_bank_report(report_name, bank_account, from_date=None, to_date=None):
	"""Run one of the supported banking reports for a bank account."""
	if report_name not in REPORTS:
		frappe.throw(_("Unsupported report: ") + str(report_name))

	bank_account_doc = assert_bank_account_access(bank_account)
	if not bank_account_doc.account:
		frappe.throw(_("The bank account has no ledger account."))

	to_date = getdate(to_date or nowdate())
	from_date = getdate(from_date or to_date.replace(day=1))
	filters = _report_filters(report_name, bank_account_doc, from_date, to_date)

	result = run_query_report(report_name=report_name, filters=filters, are_default_filters=False)
	return {
		"report_name": report_name,
		"filters": filters,
		"columns": result.get("columns") or [],
		"result": result.get("result") or [],
		"report_summary": result.get("report_summary") or [],
	}
