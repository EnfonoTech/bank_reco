"""Statement import from inside the Bank Rec screen.

Three steps, all server side so the browser never holds the whole file:

1. ``parse_statement``   read the uploaded CSV/XLSX, guess the column mapping
2. ``preview_statement`` apply a mapping and show what would be created
3. ``import_statement``  create the submitted Bank Transactions

The parsing, duplicate detection, CR/DR handling and credit-card flip are the
importer's own (``bank_statement_importer``), so this screen and the desk
importer always agree.
"""

import os
import re
from datetime import datetime

import frappe
from frappe import _
from frappe.utils import flt

from bank_reco.api.permission import (
	assert_bank_account_access,
	log_unexpected_api_exception,
	require_bank_rec_permission,
)
from bank_reco.bank_reco.doctype.bank_statement_importer.bank_statement_importer import (
	build_table,
	flip_amount_for_credit_card,
	is_truthy,
	parse_json_if_required,
	read_content,
	save_bank_mapping_for_future_use,
	start_import,
)

SAMPLE_ROWS = 8
PREVIEW_ROWS = 300
EMPTY_REFERENCE_HEADER = "(none)"

DATE_FORMATS = [
	"Auto",
	"d/m/Y",
	"m/d/Y",
	"Y-m-d",
	"d-m-Y",
	"m-d-Y",
	"Y/m/d",
	"d.m.Y",
	"dd/mm/YY",
	"m/d/YY",
	"d-m-YY",
	"d.m.YY",
]

_PY_FORMATS = {
	"d/m/Y": "%d/%m/%Y",
	"m/d/Y": "%m/%d/%Y",
	"Y-m-d": "%Y-%m-%d",
	"d-m-Y": "%d-%m-%Y",
	"m-d-Y": "%m-%d-%Y",
	"Y/m/d": "%Y/%m/%d",
	"d.m.Y": "%d.%m.%Y",
	"dd/mm/YY": "%d/%m/%y",
	"m/d/YY": "%m/%d/%y",
	"d-m-YY": "%d-%m-%y",
	"d.m.YY": "%d.%m.%y",
}

# header keywords per target, most specific first
_GUESS = {
	"date_select": ["transaction date", "txn date", "posting date", "value date", "date"],
	"description_select": ["description", "narration", "details", "particulars", "remarks", "memo"],
	"reference_number_select": ["reference", "ref no", "ref.", "cheque", "chq", "transaction id", "txn id", "ref"],
	"deposit_select": ["deposit", "credit", "cr amount", "money in", "received"],
	"withdrawal_select": ["withdrawal", "debit", "dr amount", "money out", "paid out", "payment"],
	"amount_select": ["amount", "value"],
}


def _read_file(file_url):
	"""Return (headers, body) of an uploaded CSV/XLSX/XLS file the user may read."""
	if not file_url:
		frappe.throw(_("Upload a statement file first."))
	file_name = frappe.db.get_value("File", {"file_url": file_url})
	if not file_name:
		frappe.throw(_("The uploaded file was not found."))
	file_doc = frappe.get_doc("File", file_name)
	frappe.has_permission("File", "read", doc=file_doc, throw=True)

	extension = os.path.splitext(file_url)[1][1:].lower()
	data = [
		row
		for row in read_content(file_doc.get_content(), extension)
		if row and any(c not in (None, "") for c in row)
	]
	if len(data) < 2:
		frappe.throw(_("The file has no data rows."))

	headers = [str(h).strip() if h is not None else "" for h in data[0]]
	return headers, data[1:]


def _guess_column(headers, keywords, used):
	lowered = [h.lower() for h in headers]
	for keyword in keywords:
		for index, header in enumerate(lowered):
			if header and header not in used and keyword in header:
				return headers[index]
	return ""


def _guess_date_format(headers, body, date_column):
	if not date_column:
		return "Auto"
	index = headers.index(date_column)
	samples = [
		str(row[index]).strip()
		for row in body[:25]
		if index < len(row) and row[index] not in (None, "")
	]
	if not samples:
		return "Auto"
	# a real date object (xlsx) or ISO text needs no format
	if all(re.match(r"^\d{4}-\d{2}-\d{2}", s) for s in samples):
		return "Auto"
	for name in ("d/m/Y", "m/d/Y", "d-m-Y", "m-d-Y", "d.m.Y", "Y/m/d", "dd/mm/YY", "m/d/YY", "d-m-YY", "d.m.YY"):
		pattern = _PY_FORMATS[name]
		try:
			for sample in samples:
				datetime.strptime(sample, pattern)
		except ValueError:
			continue
		return name
	return "Auto"


def _guess_mapping(headers, body):
	mapping = {}
	used = set()
	for key in ("date_select", "deposit_select", "withdrawal_select", "description_select", "reference_number_select"):
		column = _guess_column(headers, _GUESS[key], used)
		mapping[key] = column
		if column:
			used.add(column.lower())

	if mapping["deposit_select"] and mapping["withdrawal_select"]:
		mapping["same_amount_field"] = 0
		mapping["amount_select"] = ""
	else:
		amount = _guess_column(headers, _GUESS["amount_select"], used)
		mapping["same_amount_field"] = 1
		mapping["amount_select"] = amount
		mapping["deposit_select"] = ""
		mapping["withdrawal_select"] = ""
	mapping["positive_field"] = "Deposit"
	mapping["date_format"] = _guess_date_format(headers, body, mapping["date_select"])
	return mapping


def _prepare(file_url, bank_account, mapping):
	"""Read the file and return (headers, body, table-ready mapping)."""
	headers, body = _read_file(file_url)
	mapping = parse_json_if_required(mapping)
	mapping = {**mapping, "bank_account": bank_account}
	width = len(headers)
	body = [list(row)[:width] + [""] * (width - len(row)) for row in body]
	if not mapping.get("reference_number_select"):
		# the importer wants a reference column: give it an empty one
		headers = headers + [EMPTY_REFERENCE_HEADER]
		body = [row + [""] for row in body]
		mapping["reference_number_select"] = EMPTY_REFERENCE_HEADER
	if not mapping.get("date_format"):
		mapping["date_format"] = "Auto"
	return headers, body, mapping


@frappe.whitelist()
def parse_statement(file_url, bank_account):
	"""Read an uploaded statement: headers, sample rows and a guessed mapping."""
	require_bank_rec_permission()
	assert_bank_account_access(bank_account)

	headers, body = _read_file(file_url)
	guessed = _guess_mapping(headers, body)

	# the bank's saved mapping (from an earlier import) beats the guess
	saved = start_import(file_url, bank_account).get("bank_mapping") or {}
	saved_map = {
		"date": "date_select",
		"description": "description_select",
		"reference_number": "reference_number_select",
		"custom_particulars": "particulars_select",
		"custom_code": "code_select",
		"bank_party_name": "other_party_select",
	}
	for field, key in saved_map.items():
		if saved.get(field) in headers:
			guessed[key] = saved[field]
	if saved.get("deposit") in headers and saved.get("withdrawal") in headers:
		if saved["deposit"] == saved["withdrawal"]:
			guessed.update(
				same_amount_field=1, amount_select=saved["deposit"], deposit_select="", withdrawal_select=""
			)
		else:
			guessed.update(
				same_amount_field=0,
				deposit_select=saved["deposit"],
				withdrawal_select=saved["withdrawal"],
				amount_select="",
			)
	if saved.get("date_format"):
		guessed["date_format"] = saved["date_format"]

	return {
		"headers": headers,
		"sample": [["" if c is None else str(c) for c in row] for row in body[:SAMPLE_ROWS]],
		"row_count": len(body),
		"mapping": guessed,
		"used_saved_mapping": bool(saved.get("date")),
		"date_formats": DATE_FORMATS,
	}


def _table_rows(headers, body, mapping):
	"""``build_table`` output without its header row, as dicts."""
	table = build_table(mapping, headers, body)
	rows = []
	for item in table[1:]:
		rows.append(
			{
				"date": str(item[0]) if item[0] else None,
				"deposit": flt(item[1]),
				"withdrawal": flt(item[2]),
				"description": item[3],
				"reference_number": item[4] if item[4] not in (None, "") else None,
				"currency": item[6],
				"duplicate": bool(item[7]),
				"particulars": item[8] if len(item) > 8 else "",
				"code": item[9] if len(item) > 9 else "",
				"other_party": item[10] if len(item) > 10 else "",
			}
		)
	return rows


@frappe.whitelist()
def preview_statement(file_url, bank_account, mapping):
	"""Apply the mapping and report what an import would create."""
	require_bank_rec_permission()
	bank_account_doc = assert_bank_account_access(bank_account)
	headers, body, mapping = _prepare(file_url, bank_account, mapping)
	rows = _table_rows(headers, body, mapping)

	is_card = bool(frappe.db.get_value("Bank Account", bank_account, "is_credit_card"))
	new_rows = [r for r in rows if not r["duplicate"] and r["date"]]
	return {
		"bank_account": bank_account_doc.name,
		"is_credit_card": is_card,
		"total": len(rows),
		"duplicates": len([r for r in rows if r["duplicate"]]),
		"missing_date": len([r for r in rows if not r["date"]]),
		"new_count": len(new_rows),
		"deposit_total": sum(r["deposit"] for r in new_rows),
		"withdrawal_total": sum(r["withdrawal"] for r in new_rows),
		"from_date": min((r["date"] for r in new_rows), default=None),
		"to_date": max((r["date"] for r in new_rows), default=None),
		"rows": rows[:PREVIEW_ROWS],
		"truncated": len(rows) > PREVIEW_ROWS,
	}


@frappe.whitelist()
def import_statement(file_url, bank_account, mapping, skip_duplicates=1, save_mapping=0):
	"""Create and submit a Bank Transaction for every mapped row."""
	try:
		return _import_statement(file_url, bank_account, mapping, skip_duplicates, save_mapping)
	except Exception as exc:
		log_unexpected_api_exception(exc, "Bank Rec import_statement failed", rollback=True)
		raise


def _import_statement(file_url, bank_account, mapping, skip_duplicates, save_mapping):
	require_bank_rec_permission()
	assert_bank_account_access(bank_account)
	frappe.has_permission("Bank Transaction", "create", throw=True)

	headers, body, prepared = _prepare(file_url, bank_account, mapping)
	rows = _table_rows(headers, body, prepared)
	skip = is_truthy(skip_duplicates)

	imported, skipped, errors = 0, 0, []
	for index, row in enumerate(rows, start=1):
		if not row["date"]:
			errors.append({"row": index, "message": _("Date could not be read.")})
			continue
		if skip and row["duplicate"]:
			skipped += 1
			continue

		savepoint = "bank_reco_import_" + str(index)
		frappe.db.savepoint(savepoint)
		try:
			doc = frappe.new_doc("Bank Transaction")
			doc.update(
				{
					"date": row["date"],
					"bank_account": bank_account,
					"currency": row["currency"],
					"deposit": row["deposit"],
					"withdrawal": row["withdrawal"],
					"reference_number": str(row["reference_number"]) if row["reference_number"] else None,
					"description": str(row["description"]) if row["description"] else None,
					"custom_particulars": str(row["particulars"]) if row["particulars"] else None,
					"custom_code": str(row["code"]) if row["code"] else None,
					"bank_party_name": str(row["other_party"]) if row["other_party"] else None,
				}
			)
			flip_amount_for_credit_card(doc)
			doc.insert()
			doc.submit()
			frappe.db.release_savepoint(savepoint)
			imported += 1
		except Exception as exc:
			frappe.db.rollback(save_point=savepoint)
			errors.append({"row": index, "message": str(exc)[:300]})

	if is_truthy(save_mapping):
		try:
			save_bank_mapping_for_future_use({**prepared, "bank_account": bank_account})
		except Exception:
			frappe.log_error(title="Bank Reco: saving statement mapping failed", message=frappe.get_traceback())

	return {
		"imported": imported,
		"skipped_duplicates": skipped,
		"errors": errors[:50],
		"error_count": len(errors),
	}
