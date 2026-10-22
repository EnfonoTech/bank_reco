import frappe
from frappe import _
from frappe.utils import flt, getdate

from bank_reco.api.permission import assert_bank_account_access


def get_saved_balance(bank_account, on_date):
	"""Statement balance saved for exactly ``on_date``, or None."""
	row = frappe.db.get_value(
		"Bank Reco Statement Balance",
		{"bank_account": bank_account, "balance_date": getdate(on_date)},
		["name", "balance_date", "closing_balance"],
		as_dict=True,
	)
	if not row:
		return None
	return {
		"name": row.name,
		"balance_date": row.balance_date,
		"closing_balance": flt(row.closing_balance),
	}


@frappe.whitelist()
def get_statement_balance(bank_account, date):
	"""Return the saved statement balance for the exact date (or None)."""
	assert_bank_account_access(bank_account)
	return get_saved_balance(bank_account, date)


@frappe.whitelist()
def set_statement_balance(bank_account, date, balance):
	"""Create or update the statement balance of a bank account on a date.

	An empty ``balance`` removes the saved value so the field can be cleared.
	"""
	assert_bank_account_access(bank_account)
	if not date:
		frappe.throw(_("Statement date is required."))
	date = getdate(date)

	existing = frappe.db.exists(
		"Bank Reco Statement Balance",
		{"bank_account": bank_account, "balance_date": date},
	)

	if balance is None or str(balance).strip() == "":
		if existing:
			frappe.delete_doc("Bank Reco Statement Balance", existing)
		return None

	if existing:
		doc = frappe.get_doc("Bank Reco Statement Balance", existing)
		doc.closing_balance = flt(balance)
		doc.save()
	else:
		doc = frappe.get_doc(
			{
				"doctype": "Bank Reco Statement Balance",
				"bank_account": bank_account,
				"balance_date": date,
				"closing_balance": flt(balance),
			}
		)
		doc.insert()

	return get_saved_balance(bank_account, date)
