"""Internal transfers between two company bank accounts.

A transfer shows up as a withdrawal in one bank account and a deposit of the
same amount in another. ``find_transfer_counterpart`` looks for that mirror
transaction; ``create_internal_transfer`` posts one Payment Entry of type
Internal Transfer and reconciles both bank transactions to it.
"""

import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate

from bank_reco.api.bank_rec import _transaction_to_dto
from bank_reco.api.matching import _lock_bank_transaction, _submit_match
from bank_reco.api.permission import (
	assert_bank_transaction_access,
	assert_company_access,
	log_unexpected_api_exception,
	require_bank_rec_permission,
)
from bank_reco.utils.action_log import ACTION_TRANSFER, new_batch_id

DEFAULT_MATCH_DAYS = 4

COUNTERPART_FIELDS = [
	"name",
	"date",
	"deposit",
	"withdrawal",
	"currency",
	"description",
	"reference_number",
	"bank_account",
	"company",
	"unallocated_amount",
]


def _is_withdrawal(transaction):
	return flt(transaction.get("withdrawal")) > 0


def _open_amount(transaction):
	return flt(transaction.get("unallocated_amount"))


@frappe.whitelist()
def find_transfer_counterpart(bank_transaction_name):
	"""Return unreconciled transactions in other bank accounts that mirror this one."""
	require_bank_rec_permission()
	transaction = assert_bank_transaction_access(bank_transaction_name)
	if _open_amount(transaction) <= 0:
		return {"counterparts": []}

	days = cint(
		frappe.db.get_single_value("Advance Bank Reconciliation Settings", "transfer_match_days")
	) or DEFAULT_MATCH_DAYS
	date = getdate(transaction.date)

	filters = {
		"company": transaction.company,
		"bank_account": ["!=", transaction.bank_account],
		"docstatus": 1,
		"unallocated_amount": [">", 0],
		"date": ["between", [add_days(date, -days), add_days(date, days)]],
	}
	if _is_withdrawal(transaction):
		filters["deposit"] = flt(transaction.withdrawal)
		filters["withdrawal"] = 0
	else:
		filters["withdrawal"] = flt(transaction.deposit)
		filters["deposit"] = 0

	counterparts = []
	for row in frappe.get_all(
		"Bank Transaction",
		filters=filters,
		fields=COUNTERPART_FIELDS,
		order_by="date asc",
	):
		if row.currency != transaction.currency:
			continue
		try:
			assert_bank_transaction_access(row.name)
		except frappe.PermissionError:
			continue
		counterparts.append(
			{
				**_transaction_to_dto(row, status="Unreconciled"),
				"account": frappe.get_cached_value("Bank Account", row.bank_account, "account"),
				"days_apart": abs((getdate(row.date) - date).days),
			}
		)

	counterparts.sort(key=lambda item: (item["days_apart"], item["name"]))
	return {"counterparts": counterparts}


@frappe.whitelist()
def create_internal_transfer(
	bank_transaction_name,
	counterpart_name=None,
	counter_account=None,
	reference_no=None,
	remarks=None,
):
	"""Post an Internal Transfer and reconcile this transaction (and its mirror).

	Pass ``counterpart_name`` to reconcile the mirror bank transaction too, or
	``counter_account`` (a Bank/Cash ledger account) when the other side has no
	imported bank transaction.
	"""
	try:
		return _create_internal_transfer(
			bank_transaction_name, counterpart_name, counter_account, reference_no, remarks
		)
	except Exception as exc:
		log_unexpected_api_exception(exc, "Bank Reco create_internal_transfer failed", rollback=True)
		raise


def _create_internal_transfer(
	bank_transaction_name, counterpart_name, counter_account, reference_no, remarks
):
	require_bank_rec_permission()
	if not counterpart_name and not counter_account:
		frappe.throw(_("Select the mirror bank transaction or the other bank/cash account."))

	transaction = assert_bank_transaction_access(bank_transaction_name, ptype="write")
	counterpart = None
	if counterpart_name:
		counterpart = assert_bank_transaction_access(counterpart_name, ptype="write")

	# lock in a stable order so two users transferring the same pair cannot deadlock
	for name in sorted(filter(None, [transaction.name, counterpart.name if counterpart else None])):
		_lock_bank_transaction(name)
	transaction.reload()
	if counterpart:
		counterpart.reload()

	company = assert_company_access(transaction.company)
	amount = _open_amount(transaction)
	if amount <= 0 or transaction.status == "Reconciled":
		frappe.throw(_("This bank transaction is already reconciled."))

	own_account = frappe.get_cached_value("Bank Account", transaction.bank_account, "account")
	if counterpart:
		if counterpart.company != transaction.company:
			frappe.throw(_("Both bank transactions must belong to the same company."))
		if counterpart.bank_account == transaction.bank_account:
			frappe.throw(_("The mirror transaction must be in a different bank account."))
		if counterpart.currency != transaction.currency:
			frappe.throw(_("Transfers between different currencies are not supported here."))
		if _is_withdrawal(counterpart) == _is_withdrawal(transaction):
			frappe.throw(_("The mirror transaction must move money in the opposite direction."))
		if abs(_open_amount(counterpart) - amount) > 0.01:
			frappe.throw(_("The mirror transaction must have the same unallocated amount."))
		counter_account = frappe.get_cached_value("Bank Account", counterpart.bank_account, "account")
	else:
		account = frappe.get_doc("Account", counter_account)
		frappe.has_permission("Account", "read", doc=account, throw=True)
		if account.company != company or account.is_group:
			frappe.throw(_("Select a ledger account of {0}.").format(company))
		if account.account_type not in ("Bank", "Cash"):
			frappe.throw(_("The other account must be a Bank or Cash account."))

	withdrawal = _is_withdrawal(transaction)
	payment = frappe.get_doc(
		{
			"doctype": "Payment Entry",
			"company": company,
			"payment_type": "Internal Transfer",
			"posting_date": transaction.date,
			"reference_no": reference_no or transaction.reference_number or transaction.name,
			"reference_date": transaction.date,
			"paid_from": own_account if withdrawal else counter_account,
			"paid_to": counter_account if withdrawal else own_account,
			"paid_amount": amount,
			"received_amount": amount,
			"source_exchange_rate": 1,
			"target_exchange_rate": 1,
			"remarks": remarks or None,
		}
	)
	payment.insert()
	payment.submit()

	vouchers = json.dumps(
		[{"payment_doctype": "Payment Entry", "payment_name": payment.name, "amount": amount}]
	)
	batch_id = new_batch_id()
	results = [
		_submit_match(transaction.name, vouchers, action_type=ACTION_TRANSFER, batch_id=batch_id)
	]
	if counterpart:
		results.append(
			_submit_match(counterpart.name, vouchers, action_type=ACTION_TRANSFER, batch_id=batch_id)
		)

	return {
		"payment_entry": payment.name,
		"desk_url": "/app/payment-entry/" + payment.name,
		"batch_id": batch_id,
		"transaction": results[0]["transaction"],
		"counterpart": results[1]["transaction"] if counterpart else None,
	}
