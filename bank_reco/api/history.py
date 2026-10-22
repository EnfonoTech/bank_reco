"""Reconciliation history and Undo.

``Bank Reco Action Log`` rows are grouped by ``batch_id`` (one user action,
possibly touching many transactions). Undo is applied per
(batch, bank transaction) group and only when everything currently linked to
that transaction belongs to the batch, so an undo can never silently drop a
voucher that was linked later by someone else.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate, nowdate

from bank_reco.bank_reco.doctype.advance_bank_reconciliation_tool.advance_bank_reconciliation_tool import (
	CANCELLABLE_DOCTYPES,
	_is_allocated_in_other_bank_transactions,
	unreconcile_bank_transaction,
)
from bank_reco.api.matching import _lock_bank_transaction
from bank_reco.api.permission import (
	assert_bank_account_access,
	assert_bank_transaction_access,
	log_unexpected_api_exception,
	require_bank_rec_permission,
)
from bank_reco.utils.action_log import (
	ACTION_CREATED,
	ACTION_TRANSFER,
	mark_logs_undone,
	snapshot_links,
)

LOG_FIELDS = [
	"name",
	"bank_transaction",
	"action_type",
	"status",
	"batch_id",
	"voucher_type",
	"voucher_name",
	"amount",
	"details",
	"owner",
	"creation",
	"undone_on",
]


@frappe.whitelist()
def get_action_history(bank_account, from_date=None, to_date=None, mine_only=1, limit=200):
	"""Return action log rows for a bank account, grouped into batches (newest first)."""
	assert_bank_account_access(bank_account)

	filters = {"bank_account": bank_account}
	if cint(mine_only):
		filters["owner"] = frappe.session.user
	if from_date or to_date:
		# creation is a datetime: the upper bound is the day after to_date
		filters["creation"] = [
			"between",
			[getdate(from_date or "1900-01-01"), add_days(getdate(to_date or nowdate()), 1)],
		]

	rows = frappe.get_all(
		"Bank Reco Action Log",
		filters=filters,
		fields=LOG_FIELDS,
		order_by="creation desc, name desc",
		limit_page_length=min(cint(limit) or 200, 500),
	)

	transactions = {}
	names = list({row.bank_transaction for row in rows})
	if names:
		for txn in frappe.get_all(
			"Bank Transaction",
			filters={"name": ["in", names]},
			fields=["name", "date", "description", "deposit", "withdrawal", "reference_number"],
		):
			transactions[txn.name] = txn

	batches = {}
	order = []
	for row in rows:
		txn = transactions.get(row.bank_transaction) or {}
		item = {
			**row,
			"transaction_date": txn.get("date"),
			"transaction_description": txn.get("description") or txn.get("reference_number") or "",
			"transaction_amount": (txn.get("deposit") or 0) - (txn.get("withdrawal") or 0),
		}
		key = row.batch_id or row.name
		if key not in batches:
			batches[key] = {
				"batch_id": key,
				"creation": row.creation,
				"owner": row.owner,
				"rows": [],
			}
			order.append(key)
		batches[key]["rows"].append(item)

	result = []
	for key in order:
		batch = batches[key]
		batch["active_count"] = len([r for r in batch["rows"] if r["status"] == "Active"])
		result.append(batch)
	return {"batches": result}


@frappe.whitelist()
def undo_action(log_name):
	"""Undo the action behind one log row (every row of its batch+transaction)."""
	require_bank_rec_permission()
	log = frappe.get_doc("Bank Reco Action Log", log_name)
	if log.status != "Active":
		frappe.throw(_("This action was already undone."))

	if log.action_type == ACTION_TRANSFER:
		# a transfer links one payment entry to two bank transactions: undo both
		return undo_batch(log.batch_id)

	try:
		result = _undo_group(log.bank_transaction, log.batch_id)
		return {"results": [result], "summary": _summary([result])}
	except Exception as exc:
		log_unexpected_api_exception(exc, "Bank Rec undo_action failed", rollback=True)
		raise


@frappe.whitelist()
def undo_batch(batch_id):
	"""Undo every Active row of a batch, one bank transaction at a time."""
	require_bank_rec_permission()
	transactions = frappe.get_all(
		"Bank Reco Action Log",
		filters={"batch_id": batch_id, "status": "Active"},
		pluck="bank_transaction",
		order_by="creation desc, name desc",
	)
	transactions = list(dict.fromkeys(transactions))
	if not transactions:
		frappe.throw(_("Nothing to undo in this batch."))

	results = []
	for index, name in enumerate(transactions):
		savepoint = "bank_reco_undo_{0}".format(index)
		frappe.db.savepoint(savepoint)
		try:
			results.append(_undo_group(name, batch_id))
			frappe.db.release_savepoint(savepoint)
		except Exception as exc:
			frappe.db.rollback(save_point=savepoint)
			if not _is_expected(exc):
				frappe.log_error(title="Bank Rec undo_batch failed", message=frappe.get_traceback())
			results.append(
				{
					"bank_transaction": name,
					"status": "error",
					"message": str(exc) if _is_expected(exc) else _("Unexpected server error."),
				}
			)

	return {"results": results, "summary": _summary(results)}


def _is_expected(exc):
	return isinstance(exc, (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError))


def _summary(results):
	return {
		"total": len(results),
		"undone": len([r for r in results if r["status"] == "undone"]),
		"error": len([r for r in results if r["status"] == "error"]),
	}


def _undo_group(bank_transaction_name, batch_id):
	transaction = assert_bank_transaction_access(bank_transaction_name, ptype="write")
	_lock_bank_transaction(transaction.name)
	transaction.reload()

	logs = frappe.get_all(
		"Bank Reco Action Log",
		filters={
			"bank_transaction": transaction.name,
			"batch_id": batch_id,
			"status": "Active",
		},
		fields=["name", "action_type", "voucher_type", "voucher_name"],
	)
	if not logs:
		frappe.throw(_("Nothing to undo for bank transaction {0}.").format(transaction.name))

	log_names = [row.name for row in logs]
	batch_keys = {(row.voucher_type, row.voucher_name) for row in logs}
	linked = set(snapshot_links(transaction.name))
	present = linked & batch_keys

	if not present:
		mark_logs_undone(transaction.name, names=log_names, note=_("Already unlinked."))
		return {"bank_transaction": transaction.name, "status": "undone", "cancelled": []}

	if linked - batch_keys:
		frappe.throw(
			_(
				"Bank Transaction {0} has other vouchers linked since this action. "
				"Unreconcile it from the Matched page instead."
			).format(transaction.name)
		)

	created = [
		(row.voucher_type, row.voucher_name)
		for row in logs
		if row.action_type in (ACTION_CREATED, ACTION_TRANSFER)
		and (row.voucher_type, row.voucher_name) in present
		and row.voucher_type in CANCELLABLE_DOCTYPES
	]

	unreconcile_bank_transaction(transaction.name, cancel_linked_documents=False)

	cancelled = []
	for voucher_type, voucher_name in created:
		if _is_allocated_in_other_bank_transactions(voucher_type, voucher_name):
			continue
		voucher = frappe.get_doc(voucher_type, voucher_name)
		if voucher.docstatus == 1:
			voucher.cancel()
			cancelled.append({"voucher_type": voucher_type, "voucher_name": voucher_name})

	mark_logs_undone(transaction.name, names=log_names)
	return {"bank_transaction": transaction.name, "status": "undone", "cancelled": cancelled}
