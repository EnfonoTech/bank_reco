"""Auto reconcile: match bank transactions to existing vouchers without a click.

What counts as a match is set in *Advance Bank Reconciliation Settings*
(Auto Reconcile section). The amount must always match exactly; reference,
party and date are optional extra requirements. A transaction is only matched
when the choice is clear (by default exactly one voucher qualifies), and
everything done is logged in one batch so it can be undone from History.
"""

import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate

from bank_reco.api.matching import _submit_match, get_match_candidates
from bank_reco.api.permission import (
	assert_bank_account_access,
	log_unexpected_api_exception,
	require_bank_rec_permission,
)
from bank_reco.utils.action_log import new_batch_id
from bank_reco.utils.jobs import new_job, set_job

SETTINGS = "Advance Bank Reconciliation Settings"
JOB_TIMEOUT = 3600
MAX_TRANSACTIONS = 2000
PROPOSAL_LIMIT = 200
COMMIT_EVERY = 10

# field -> default when the settings were never saved
_DEFAULTS = {
	"auto_match_reference": 1,
	"auto_match_party": 0,
	"auto_match_date": 1,
	"auto_match_date_days": 5,
	"auto_match_one_only": 1,
	"auto_match_unpaid_invoices": 0,
}


def _stored(field):
	"""Raw saved value of a settings field, or None when it was never saved."""
	rows = frappe.db.sql(
		"select `value` from `tabSingles` where `doctype` = %s and `field` = %s",
		(SETTINGS, field),
	)
	return rows[0][0] if rows else None


def _single(field):
	value = _stored(field)
	return _DEFAULTS[field] if value in (None, "") else cint(value)


def get_auto_match_settings():
	return {
		"reference": bool(_single("auto_match_reference")),
		"party": bool(_single("auto_match_party")),
		"date": bool(_single("auto_match_date")),
		"date_days": _single("auto_match_date_days"),
		"one_only": bool(_single("auto_match_one_only")),
		"unpaid_invoices": bool(_single("auto_match_unpaid_invoices")),
	}


@frappe.whitelist()
def get_auto_reconcile_settings():
	require_bank_rec_permission()
	return get_auto_match_settings()


def _document_types(settings):
	types = ["payment_entry", "journal_entry", "sales_invoice", "purchase_invoice"]
	if settings["unpaid_invoices"]:
		types += ["unpaid_sales_invoice", "unpaid_purchase_invoice"]
	return types


def _within_days(candidate, transaction_date, days):
	for key in ("posting_date", "reference_date"):
		value = candidate.get(key)
		if value and abs((getdate(value) - transaction_date).days) <= days:
			return True
	return False


def qualifying_candidates(transaction_name, transaction_date, settings):
	"""Vouchers that satisfy every enabled criterion for this transaction."""
	days = settings["date_days"] if settings["date"] else 90
	response = get_match_candidates(
		transaction_name,
		document_types=_document_types(settings),
		from_date=add_days(transaction_date, -days),
		to_date=add_days(transaction_date, days),
	)
	result = []
	for candidate in response["candidates"]:
		reasons = set(candidate["reasons"])
		if "Amount" not in reasons or candidate.get("is_internal_transfer"):
			continue
		if settings["reference"] and "Reference" not in reasons:
			continue
		if settings["party"] and "Party" not in reasons:
			continue
		if settings["date"] and not _within_days(candidate, transaction_date, settings["date_days"]):
			continue
		result.append(candidate)
	result.sort(key=lambda c: -c["rank"])
	return result


def _unreconciled(bank_account, from_date, to_date):
	filters = {
		"bank_account": bank_account,
		"docstatus": 1,
		"unallocated_amount": [">", 0],
	}
	if from_date or to_date:
		filters["date"] = ["between", [from_date or "1900-01-01", to_date or "2999-12-31"]]
	return frappe.get_all(
		"Bank Transaction",
		filters=filters,
		fields=["name", "date", "description", "reference_number", "deposit", "withdrawal", "unallocated_amount"],
		order_by="date asc",
		limit_page_length=MAX_TRANSACTIONS,
	)


def run_auto_reconcile(progress_id, user, bank_account, from_date, to_date, dry_run):
	"""Background job. ``dry_run`` only reports what would be matched."""
	frappe.set_user(user)
	try:
		_auto_reconcile(progress_id, bank_account, from_date, to_date, dry_run)
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="Bank Reco auto reconcile failed", message=frappe.get_traceback())
		set_job(progress_id, status="failed", message=_("Auto reconcile failed. See the Error Log."))


def _auto_reconcile(job_id, bank_account, from_date, to_date, dry_run):
	settings = get_auto_match_settings()
	transactions = _unreconciled(bank_account, from_date, to_date)
	set_job(job_id, status="running", total=len(transactions), processed=0)

	batch_id = new_batch_id()
	counts = {"matched": 0, "ambiguous": 0, "no_match": 0, "failed": 0}
	proposals = []

	for index, transaction in enumerate(transactions, start=1):
		savepoint = "bank_reco_auto_" + str(index)
		frappe.db.savepoint(savepoint)
		try:
			candidates = qualifying_candidates(transaction.name, getdate(transaction.date), settings)
			if not candidates:
				counts["no_match"] += 1
			elif settings["one_only"] and len(candidates) > 1:
				counts["ambiguous"] += 1
			else:
				pick = candidates[0]
				amount = min(abs(flt(pick["amount"])), abs(flt(transaction.unallocated_amount)))
				if dry_run:
					counts["matched"] += 1
					if len(proposals) < PROPOSAL_LIMIT:
						proposals.append(
							{
								"bank_transaction": transaction.name,
								"date": str(transaction.date),
								"description": transaction.description,
								"amount": flt(transaction.deposit) or -flt(transaction.withdrawal),
								"voucher_type": pick["voucher_type"],
								"voucher_name": pick["voucher_name"],
								"reasons": pick["reasons"],
							}
						)
				else:
					_submit_match(
						transaction.name,
						json.dumps(
							[
								{
									"voucher_type": pick["voucher_type"],
									"voucher_name": pick["voucher_name"],
									"amount": amount,
									"source_type": pick.get("source_type"),
								}
							]
						),
						batch_id=batch_id,
					)
					counts["matched"] += 1
			frappe.db.release_savepoint(savepoint)
		except Exception:
			frappe.db.rollback(save_point=savepoint)
			counts["failed"] += 1
			frappe.log_error(
				title="Bank Reco auto reconcile: {0} failed".format(transaction.name),
				message=frappe.get_traceback(),
			)

		if index % COMMIT_EVERY == 0:
			if not dry_run:
				frappe.db.commit()
			set_job(job_id, processed=index, **counts)

	if not dry_run:
		frappe.db.commit()
	set_job(
		job_id,
		status="done",
		processed=len(transactions),
		dry_run=bool(dry_run),
		batch_id=None if dry_run else batch_id,
		proposals=proposals,
		**counts,
	)


@frappe.whitelist()
def start_auto_reconcile(bank_account, from_date=None, to_date=None, dry_run=1):
	"""Queue an auto-reconcile run (``dry_run`` = preview only). Returns a job id."""
	try:
		require_bank_rec_permission()
		assert_bank_account_access(bank_account)
		dry = bool(cint(dry_run))
		if not dry:
			frappe.has_permission("Bank Transaction", "write", throw=True)

		job_id = new_job("auto_reconcile", bank_account=bank_account, dry_run=dry)
		frappe.enqueue(
			"bank_reco.api.auto_match.run_auto_reconcile",
			queue="long",
			timeout=JOB_TIMEOUT,
			job_id="bank_reco_auto_" + job_id,
			progress_id=job_id,
			user=frappe.session.user,
			bank_account=bank_account,
			from_date=from_date,
			to_date=to_date,
			dry_run=dry,
			enqueue_after_commit=True,
		)
		return {"job_id": job_id}
	except Exception as exc:
		log_unexpected_api_exception(exc, "Bank Reco start_auto_reconcile failed", rollback=True)
		raise


def scheduled_auto_reconcile():
	"""Hourly job (off by default): auto reconcile the last 45 days of every company bank account."""
	if not _single_flag("auto_reconcile_hourly"):
		return
	today = getdate()
	for bank_account in frappe.get_all("Bank Account", filters={"is_company_account": 1}, pluck="name"):
		job_id = new_job("auto_reconcile", bank_account=bank_account, dry_run=False)
		try:
			run_auto_reconcile(job_id, "Administrator", bank_account, add_days(today, -45), today, False)
		except Exception:
			frappe.log_error(title="Bank Reco hourly auto reconcile failed", message=frappe.get_traceback())


def _single_flag(field):
	return bool(cint(_stored(field)))
