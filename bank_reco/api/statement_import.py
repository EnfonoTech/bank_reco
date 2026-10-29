"""Statement import from inside the Bank Reco screen.

Three steps, all server side so the browser never holds the whole file:

1. ``parse_statement``   read the uploaded CSV/XLSX, guess the column mapping
2. ``preview_statement`` apply a mapping and show what would be created
3. ``import_statement``  create the submitted Bank Transactions

The parsing, duplicate detection, CR/DR handling and credit-card flip are the
importer's own (``bank_statement_importer``), so this screen and the desk
importer always agree.
"""

import os

import frappe
from frappe import _
from frappe.utils import flt

from bank_reco.api.permission import (
	assert_bank_account_access,
	log_unexpected_api_exception,
	require_bank_rec_permission,
)
from bank_reco.utils.jobs import get_job, new_job, set_job
from bank_reco.bank_reco.doctype.bank_statement_importer.bank_statement_importer import (
	EMPTY_REFERENCE_HEADER,
	build_table_with_report,
	clean_header,
	detect_date_format,
	flip_amount_for_credit_card,
	get_readable_file_doc,
	is_truthy,
	mark_duplicates,
	parse_json_if_required,
	read_content,
	row_key,
	save_bank_mapping_for_future_use,
	start_import,
)

SAMPLE_ROWS = 8
PREVIEW_ROWS = 300
MAX_ERRORS_SHOWN = 50

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
	"""Return (headers, body, row_numbers) of an uploaded CSV/XLSX/XLS file the user may read.

	``row_numbers`` are the rows' numbers in the file (the header is row 1), so a
	row error can be found again after blank lines were dropped.
	"""
	file_doc = get_readable_file_doc(file_url)

	extension = os.path.splitext(file_url)[1][1:].lower()
	numbered = [
		(number, row)
		for number, row in enumerate(read_content(file_doc.get_content(), extension), start=1)
		if row and any(c not in (None, "") for c in row)
	]
	if len(numbered) < 2:
		frappe.throw(_("The file has no data rows."))

	headers = [clean_header(h) for h in numbered[0][1]]
	body = [row for _number, row in numbered[1:]]
	row_numbers = [number for number, _row in numbered[1:]]
	return headers, body, row_numbers


def _guess_column(headers, keywords, used):
	lowered = [h.lower() for h in headers]
	for keyword in keywords:
		for index, header in enumerate(lowered):
			if header and header not in used and keyword in header:
				return headers[index]
	return ""


def _date_detection(headers, body, date_column):
	"""``detect_date_format`` over every value of the date column."""
	if not date_column or date_column not in headers:
		return detect_date_format([])
	index = headers.index(date_column)
	return detect_date_format(row[index] for row in body if index < len(row))


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
	mapping["date_format"] = _date_detection(headers, body, mapping["date_select"])["format"] or "Auto"
	return mapping


def _prepare(file_url, bank_account, mapping):
	"""Read the file and return (headers, body, table-ready mapping, row numbers)."""
	headers, body, row_numbers = _read_file(file_url)
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
	return headers, body, mapping, row_numbers


@frappe.whitelist()
def parse_statement(file_url, bank_account):
	"""Read an uploaded statement: headers, sample rows and a guessed mapping."""
	require_bank_rec_permission()
	assert_bank_account_access(bank_account)

	headers, body, _row_numbers = _read_file(file_url)
	guessed = _guess_mapping(headers, body)

	# the bank's saved mapping (from an earlier import) beats the guess
	saved = start_import(file_url, bank_account, include_rows=False).get("bank_mapping") or {}
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

	# the dates decide the format. The saved format only breaks a tie: when every
	# date is ambiguous (day and month both 12 or less) and the saved format is one
	# of the two readings of this file's layout.
	detection = _date_detection(headers, body, guessed["date_select"])
	warnings = []
	guessed["date_format"] = detection["format"] or "Auto"
	saved_format = saved.get("date_format")
	if (
		detection["ambiguous"]
		and saved.get("date") in headers
		and saved_format in detection["candidates"]
	):
		guessed["date_format"] = saved_format
	elif detection["warning"]:
		warnings.append(detection["warning"])

	return {
		"headers": headers,
		"sample": [["" if c is None else str(c) for c in row] for row in body[:SAMPLE_ROWS]],
		"row_count": len(body),
		"mapping": guessed,
		"used_saved_mapping": bool(saved.get("date")),
		"date_formats": DATE_FORMATS,
		"warnings": warnings,
	}


def _table_rows(headers, body, mapping, row_numbers=None):
	"""``build_table`` output without its header row, as dicts, plus its report.

	The report lists the rows that cannot be imported (``errors``), the rows
	skipped for having no amount, warnings and the date format used. Duplicates
	are not marked here, see ``_mark_duplicates``.
	"""
	table, report = build_table_with_report(
		mapping, headers, body, row_numbers=row_numbers, check_duplicates=False
	)
	rows = []
	for item, source_row in zip(table[1:], report["row_numbers"]):
		rows.append(
			{
				"source_row": source_row,
				"date": str(item[0]),
				"deposit": flt(item[1]),
				"withdrawal": flt(item[2]),
				"description": item[3],
				"reference_number": item[4] if item[4] not in (None, "") else None,
				"currency": item[6],
				"duplicate": False,
				"particulars": item[8],
				"code": item[9],
				"other_party": item[10],
			}
		)
	return rows, report


# one duplicate rule for this screen and for the desk importer: it lives in the importer
_row_key = row_key
_mark_duplicates = mark_duplicates


@frappe.whitelist()
def preview_statement(file_url, bank_account, mapping):
	"""Apply the mapping and report what an import would create."""
	require_bank_rec_permission()
	bank_account_doc = assert_bank_account_access(bank_account)
	headers, body, mapping, row_numbers = _prepare(file_url, bank_account, mapping)
	rows, report = _table_rows(headers, body, mapping, row_numbers)
	rows = _mark_duplicates(rows, bank_account)

	is_card = bool(frappe.db.get_value("Bank Account", bank_account, "is_credit_card"))
	new_rows = [r for r in rows if not r["duplicate"] and r["date"]]
	return {
		"bank_account": bank_account_doc.name,
		"is_credit_card": is_card,
		"total": len(rows),
		"duplicates": len([r for r in rows if r["duplicate"]]),
		"missing_date": len([e for e in report["errors"] if e["field"] == "date"]),
		"error_count": len(report["errors"]),
		"errors": report["errors"][:MAX_ERRORS_SHOWN],
		"skipped_zero": report["skipped_zero"],
		"warnings": report["warnings"],
		"date_format": report["date_format"],
		"new_count": len(new_rows),
		"deposit_total": sum(r["deposit"] for r in new_rows),
		"withdrawal_total": sum(r["withdrawal"] for r in new_rows),
		"from_date": min((r["date"] for r in new_rows), default=None),
		"to_date": max((r["date"] for r in new_rows), default=None),
		"rows": rows[:PREVIEW_ROWS],
		"truncated": len(rows) > PREVIEW_ROWS,
	}


@frappe.whitelist()
def import_statement(
	file_url,
	bank_account,
	mapping,
	skip_duplicates=1,
	save_mapping=0,
	auto_reconcile=0,
	run_rules=0,
):
	"""Queue the import. Returns a job id; poll ``bank_reco.api.jobs.get_job_status``."""
	try:
		require_bank_rec_permission()
		assert_bank_account_access(bank_account)
		frappe.has_permission("Bank Transaction", "create", throw=True)

		job_id = new_job("import", bank_account=bank_account, file_url=file_url)
		frappe.enqueue(
			"bank_reco.api.statement_import.run_import",
			queue="long",
			timeout=3600,
			job_id="bank_reco_import_" + job_id,
			progress_id=job_id,
			user=frappe.session.user,
			file_url=file_url,
			bank_account=bank_account,
			mapping=mapping,
			skip_duplicates=skip_duplicates,
			save_mapping=save_mapping,
			auto_reconcile=auto_reconcile,
			run_rules=run_rules,
			enqueue_after_commit=True,
		)
		return {"job_id": job_id}
	except Exception as exc:
		log_unexpected_api_exception(exc, "Bank Reco import_statement failed", rollback=True)
		raise


def run_import(
	progress_id, user, file_url, bank_account, mapping, skip_duplicates, save_mapping, auto_reconcile, run_rules
):
	"""Background job: create the Bank Transactions, committing as it goes."""
	frappe.set_user(user)
	try:
		_run_import(
			progress_id, file_url, bank_account, mapping, skip_duplicates, save_mapping, auto_reconcile, run_rules
		)
	except frappe.ValidationError as exc:
		# a problem with the file or the mapping that the user can fix: say what it is
		frappe.db.rollback()
		set_job(progress_id, status="failed", message=str(exc)[:300])
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="Bank Reco statement import failed", message=frappe.get_traceback())
		set_job(progress_id, status="failed", message=_("The import failed. See the Error Log."))


def _run_import(job_id, file_url, bank_account, mapping, skip_duplicates, save_mapping, auto_reconcile, run_rules):
	set_job(job_id, status="running", stage="Reading the file")
	headers, body, prepared, row_numbers = _prepare(file_url, bank_account, mapping)
	rows, report = _table_rows(headers, body, prepared, row_numbers)
	rows = _mark_duplicates(rows, bank_account)
	skip = is_truthy(skip_duplicates)
	set_job(
		job_id,
		total=len(rows),
		stage="Creating bank transactions",
		warnings=report["warnings"],
		skipped_zero=report["skipped_zero"],
		date_format=report["date_format"],
	)

	# rows that could not be read are reported with their row in the file
	imported, skipped, errors, dates = 0, 0, [dict(e) for e in report["errors"]], []
	for index, row in enumerate(rows, start=1):
		if skip and row["duplicate"]:
			skipped += 1
		else:
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
				dates.append(row["date"])
			except Exception as exc:
				frappe.db.rollback(save_point=savepoint)
				errors.append({"row": row["source_row"], "message": str(exc)[:300]})

		if index % 20 == 0:
			frappe.db.commit()
			set_job(
				job_id,
				processed=index,
				imported=imported,
				skipped_duplicates=skipped,
				error_count=len(errors),
			)

	frappe.db.commit()
	set_job(job_id, processed=len(rows), imported=imported, skipped_duplicates=skipped, error_count=len(errors))

	if is_truthy(save_mapping):
		try:
			save_bank_mapping_for_future_use({**prepared, "bank_account": bank_account})
			frappe.db.commit()
		except Exception:
			frappe.log_error(title="Bank Reco: saving statement mapping failed", message=frappe.get_traceback())

	extras = {}
	if dates and (is_truthy(run_rules) or is_truthy(auto_reconcile)):
		from_date, to_date = min(dates), max(dates)
		if is_truthy(run_rules):
			set_job(job_id, stage="Running auto rules")
			from bank_reco.bank_reco.doctype.abr_bank_rule.abr_bank_rule import run_bank_rules

			try:
				extras["rules"] = run_bank_rules(bank_account, from_date, to_date)
			except Exception:
				frappe.db.rollback()
				frappe.log_error(title="Bank Reco: auto rules after import failed", message=frappe.get_traceback())
		if is_truthy(auto_reconcile):
			set_job(job_id, stage="Auto reconciling")
			from bank_reco.api.auto_match import _auto_reconcile

			sub_id = new_job("auto_reconcile", bank_account=bank_account, dry_run=False)
			try:
				_auto_reconcile(sub_id, bank_account, from_date, to_date, False)
				extras["auto_reconcile"] = {
					key: value
					for key, value in (get_job(sub_id) or {}).items()
					if key in ("matched", "ambiguous", "no_match", "failed", "batch_id")
				}
			except Exception:
				frappe.db.rollback()
				frappe.log_error(title="Bank Reco: auto reconcile after import failed", message=frappe.get_traceback())

	set_job(
		job_id,
		status="done",
		stage="Done",
		imported=imported,
		skipped_duplicates=skipped,
		error_count=len(errors),
		errors=sorted(errors, key=lambda e: e["row"])[:MAX_ERRORS_SHOWN],
		from_date=str(min(dates)) if dates else None,
		to_date=str(max(dates)) if dates else None,
		**extras,
	)
