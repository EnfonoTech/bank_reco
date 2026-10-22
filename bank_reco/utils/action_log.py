"""Reconciliation action log (the audit trail behind Undo / History).

Every API path that links vouchers to a Bank Transaction snapshots the links
before it runs and calls ``log_new_links`` afterwards, so the log records what
the action *actually* linked rather than what the caller asked for. Logging
must never break a reconciliation: failures are written to the Error Log and
swallowed.
"""

import frappe
from frappe.utils import flt, now_datetime

from bank_reco.utils.logger import get_logger

ACTION_MATCHED = "Matched"
ACTION_CREATED = "Created Voucher"
ACTION_TRANSFER = "Internal Transfer"


def new_batch_id():
	return frappe.generate_hash(length=12)


def snapshot_links(bank_transaction_name):
	"""Return ``{(voucher_type, voucher_name): allocated_amount}`` currently linked."""
	rows = frappe.get_all(
		"Bank Transaction Payments",
		filters={"parent": bank_transaction_name, "parenttype": "Bank Transaction"},
		fields=["payment_document", "payment_entry", "allocated_amount"],
	)
	return {
		(row.payment_document, row.payment_entry): flt(row.allocated_amount)
		for row in rows
		if row.payment_document and row.payment_entry
	}


def log_new_links(
	bank_transaction_name,
	before,
	action_type=ACTION_CREATED,
	batch_id=None,
	matched_keys=None,
	details=None,
):
	"""Log every link added since ``before`` (a ``snapshot_links`` result).

	``matched_keys`` lets one call mix kinds: links whose key is in it are
	logged as ``Matched`` (existing voucher picked by the user), the rest as
	``action_type`` (voucher created by the action, e.g. a payment entry made
	for an unpaid invoice).
	"""
	try:
		after = snapshot_links(bank_transaction_name)
		new_keys = [key for key in after if key not in before]
		if not new_keys:
			return []

		bank_account, company = frappe.db.get_value(
			"Bank Transaction", bank_transaction_name, ["bank_account", "company"]
		) or (None, None)
		batch_id = batch_id or new_batch_id()
		matched_keys = set(matched_keys or [])

		names = []
		for key in new_keys:
			doc = frappe.get_doc(
				{
					"doctype": "Bank Reco Action Log",
					"bank_transaction": bank_transaction_name,
					"bank_account": bank_account,
					"company": company,
					"action_type": ACTION_MATCHED if key in matched_keys else action_type,
					"status": "Active",
					"batch_id": batch_id,
					"voucher_type": key[0],
					"voucher_name": key[1],
					"amount": after[key],
					"details": details,
				}
			)
			doc.insert(ignore_permissions=True)
			names.append(doc.name)
		return names
	except Exception:
		get_logger().error("Action log write failed for %s", bank_transaction_name, exc_info=True)
		frappe.log_error(
			title="Bank Reco action log failed",
			message=frappe.get_traceback(),
		)
		return []


def mark_logs_undone(bank_transaction_name, note=None, names=None):
	"""Flip Active log rows of a transaction (or the given ``names``) to Undone."""
	filters = {"bank_transaction": bank_transaction_name, "status": "Active"}
	if names:
		filters["name"] = ["in", list(names)]
	rows = frappe.get_all("Bank Reco Action Log", filters=filters, pluck="name")
	for name in rows:
		values = {
			"status": "Undone",
			"undone_by": frappe.session.user,
			"undone_on": now_datetime(),
		}
		if note:
			values["details"] = note
		frappe.db.set_value("Bank Reco Action Log", name, values, update_modified=False)
	return rows
