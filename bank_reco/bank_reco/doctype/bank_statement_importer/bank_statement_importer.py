# Copyright (c) 2024, High Flyer and contributors
# For license information, please see license.txt
import json
import logging
import math
import os
import re
import unicodedata
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, cstr, flt
from frappe.utils.csvutils import read_csv_content
from frappe.utils.xlsxutils import (
    read_xls_file_from_attached_file,
    read_xlsx_file_from_attached_file,
)

from bank_reco.api.permission import (
    assert_bank_account_access,
    require_bank_rec_permission,
)

logger = frappe.logger("bank_rec", allow_site=True)
logger.setLevel(logging.INFO)

TABLE_HEADER = [
    "Date",
    "Deposit",
    "Withdrawal",
    "Description",
    "Reference Number",
    "Bank Account",
    "Currency",
    "Is Duplicated",
    "Particulars",
    "Code",
    "Other Party",
]
MAX_ROW_ERRORS_SHOWN = 10
EMPTY_REFERENCE_HEADER = "(none)"


class StatementParseError(ValueError):
    """A statement cell that cannot be read. The message is already translated."""


def flip_amount_for_credit_card(doc):
    """Swap deposit and withdrawal on a Bank Transaction document when the
    target Bank Account is flagged as a credit card.

    Credit-card statements typically put charges and payments under one
    "Amount" column; importers route positive amounts to deposit, but for
    credit-card accounts treated as Bank/Asset the sign is inverted (charges
    should be withdrawals, payments should be deposits).

    This helper is called explicitly from statement-import flows only. Manual
    Bank Transaction creation and direct API insertions are not flipped; the
    caller is expected to provide values in the correct orientation.
    """
    if not doc.bank_account:
        return

    if not frappe.db.has_column("Bank Account", "is_credit_card"):
        return

    is_credit_card = frappe.db.get_value(
        "Bank Account", doc.bank_account, "is_credit_card"
    )
    if not is_credit_card:
        return

    original_deposit = flt(doc.deposit)
    original_withdrawal = flt(doc.withdrawal)
    if original_deposit == 0 and original_withdrawal == 0:
        return

    doc.deposit = original_withdrawal
    doc.withdrawal = original_deposit
    logger.info(
        "Credit-card flip applied on statement import (%s): deposit %s -> %s, withdrawal %s -> %s",
        doc.bank_account, original_deposit, doc.deposit,
        original_withdrawal, doc.withdrawal,
    )

BANK_MAPPING_FIELD_ORDER = [
    "date",
    "deposit",
    "withdrawal",
    "description",
    "reference_number",
    "custom_particulars",
    "custom_code",
    "bank_party_name",
]


class BankStatementImporter(Document):
    pass


def read_content(content, extension):
    if extension not in ("csv", "xlsx", "xls"):
        frappe.throw(
            _("Import template should be of type .csv, .xlsx or .xls"),
            title="Template Error",
        )

    if extension == "csv":
        data = read_csv_content(content)
    elif extension == "xlsx":
        data = read_xlsx_file_from_attached_file(fcontent=content)
    elif extension == "xls":
        data = read_xls_file_from_attached_file(content)

    return data


def clean_header(value):
    """Header cell as plain text: no BOM (Excel's "CSV UTF-8"), no padding."""
    if value is None:
        return ""
    return cstr(value).replace("﻿", "").strip()


def get_readable_file_doc(file_url):
    """The File behind ``file_url`` that the current user may read.

    Identical uploads share one ``file_url``, so several File rows can match:
    take the first one this user is allowed to read.
    """
    if not file_url:
        frappe.throw(_("Upload a statement file first."))

    names = frappe.get_all(
        "File", filters={"file_url": file_url}, pluck="name", order_by="creation asc"
    )
    if not names:
        frappe.throw(_("The uploaded file was not found."))

    for name in names:
        file_doc = frappe.get_doc("File", name)
        if frappe.has_permission("File", "read", doc=file_doc):
            return file_doc
    raise frappe.PermissionError(_("You do not have permission to read this file."))


def start_import(file_path, bank_account, include_rows=True):
    # Validate bank_account parameter
    if not bank_account:
        frappe.throw(_("Bank Account is required"), title="Validation Error")

    # Validate that bank account exists
    if not frappe.db.exists("Bank Account", bank_account):
        frappe.throw(
            _("Bank Account '{0}' does not exist").format(bank_account),
            title="Validation Error",
        )

    data_headers, data_body = [], []
    if include_rows:
        file_doc = get_readable_file_doc(file_path)
        # banks often download "STATEMENT.CSV" / ".XLSX": compare in lower case
        extn = os.path.splitext(file_path)[1][1:].lower()
        data = read_content(file_doc.get_content(), extn)
        if not data:
            frappe.throw(_("The file has no data rows."))
        data_headers = [clean_header(cell) for cell in data[0]]
        data_body = data[1:]

    bank_mapping = {}
    bank_doc = frappe.get_doc("Bank Account", bank_account)

    # Validate that bank account is linked to a bank
    if bank_doc.bank:
        try:
            bank = frappe.get_doc("Bank", bank_doc.bank)

            # Safely get bank transaction mapping if it exists
            if (
                hasattr(bank, "bank_transaction_mapping")
                and bank.bank_transaction_mapping
            ):
                # Get bank transaction mapping - swap key and value
                for mapping in bank.bank_transaction_mapping:
                    if hasattr(mapping, "bank_transaction_field") and hasattr(
                        mapping, "file_field"
                    ):
                        bank_mapping[mapping.bank_transaction_field] = (
                            mapping.file_field
                        )

            # Safely get date format with fallback
            date_format = "Auto"  # Default fallback
            if (
                hasattr(bank, "bank_statement_date_format")
                and bank.bank_statement_date_format
            ):
                date_format = bank.bank_statement_date_format
            bank_mapping["date_format"] = date_format

        except frappe.DoesNotExistError:
            frappe.throw(
                _("Bank '{0}' linked to Bank Account '{1}' does not exist").format(
                    bank_doc.bank, bank_account
                ),
                title="Validation Error",
            )
    else:
        # Bank account not linked to any bank - use default date format
        bank_mapping["date_format"] = "Auto"
        frappe.msgprint(
            _(
                "Bank Account '{0}' is not linked to any Bank. Using default settings."
            ).format(bank_account),
            title="Warning",
        )

    return {"header": data_headers, "body": data_body, "bank_mapping": bank_mapping}


_CR_DR_PATTERN = re.compile(r"^\s*(?:(CR|DR)\.?\s*)?(.*?)(?:\s*(CR|DR)\.?)?\s*$", re.IGNORECASE)


def split_cr_dr(amount_str):
    """Split a statement amount such as "1,250.00 CR" into (``"1,250.00"``, ``"CR"``).

    Some banks and card issuers print the direction as a CR/DR marker instead
    of a sign. Returns ``(amount_str, None)`` when there is no marker.
    """
    if not isinstance(amount_str, str):
        return amount_str, None
    match = _CR_DR_PATTERN.match(amount_str)
    marker = match and (match.group(1) or match.group(3))
    if not marker:
        return amount_str, None
    return match.group(2).strip(), marker.upper()


_AMOUNT_TRANSLATE = {
    # Arabic-Indic and Persian digits, Arabic decimal / thousands separators
    **{ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")},
    **{ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")},
    0x066B: ".",
    0x066C: ",",
    # unicode minus and dashes read as "-"
    **{cp: "-" for cp in (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212)},
    # no-break / narrow / thin spaces
    **{cp: " " for cp in (0x00A0, 0x2007, 0x2009, 0x202F)},
    # direction marks that Arabic exports embed around numbers
    0x200E: None,
    0x200F: None,
    0x061C: None,
}


def _group_digits_ok(integer_part, separator):
    """True for 1,250,000 and for the Indian 12,50,000 grouping."""
    sep = re.escape(separator)
    return bool(
        re.fullmatch(r"\d{1,3}(?:%s\d{3})+" % sep, integer_part)
        or re.fullmatch(r"\d{1,3}(?:%s\d{2})*%s\d{3}" % (sep, sep), integer_part)
    )


def _amount_text_to_number(raw):
    """Read one statement amount cell given as text.

    Handles a currency symbol or code, ``(1,250.00)`` and ``1,250.00-`` negatives,
    ``1,250.50`` / ``1.250,50`` / ``250,50`` / ``1 250,50`` separators and
    ``0.000``. The one ambiguous shape, a single comma followed by exactly three
    digits (``1,250``), is a thousands separator, as before. ``0,500`` (a zero
    before the comma) is the decimal 0.5, and a single dot is always a decimal
    point. Raises StatementParseError for anything else.
    """

    def invalid():
        return StatementParseError(
            _("'{0}' is not a valid amount.").format(cstr(raw).strip())
        )

    text = cstr(raw).translate(_AMOUNT_TRANSLATE)
    # currency symbols (SAR sign, $, EUR sign, ...) become blanks
    text = "".join(" " if unicodedata.category(ch) == "Sc" else ch for ch in text)

    # currency codes / words such as SAR, Rs, CR are allowed, long words are not
    words = re.findall(r"[^\W\d_]+", text)
    if len(words) > 2 or any(len(word) > 3 for word in words):
        raise invalid()
    text = re.sub(r"[^\W\d_]+\.?", " ", text).strip()

    if text in ("", "-", "--"):
        # a blank or a dash is "no amount"; a bare word ("abc", "Total") is not
        if words:
            raise invalid()
        return 0.0

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1].strip()
    leading = text[:1] in ("-", "+")
    trailing = text[-1:] in ("-", "+")
    if leading and trailing:
        raise invalid()
    if leading or trailing:
        sign = text[0] if leading else text[-1]
        negative = negative or sign == "-"
        text = (text[1:] if leading else text[:-1]).strip()

    if re.fullmatch(r"[.,]\d+", text):
        number = "0." + text[1:]
    elif re.fullmatch(r"\d(?:[\d.,' ]*\d)?", text):
        if " " in text or "'" in text:
            if not re.fullmatch(r"\d{1,3}(?:[ ']\d{3})+(?:[.,]\d+)?", text):
                raise invalid()
            text = re.sub(r"[ ']", "", text)
        dots, commas = text.count("."), text.count(",")
        if dots and commas:
            decimal = "." if text.rfind(".") > text.rfind(",") else ","
            group = "," if decimal == "." else "."
            if text.count(decimal) != 1:
                raise invalid()
            integer, fraction = text.split(decimal)
            if not _group_digits_ok(integer, group):
                raise invalid()
            number = integer.replace(group, "") + "." + fraction
        elif commas > 1 or dots > 1:
            separator = "," if commas else "."
            if not _group_digits_ok(text, separator):
                raise invalid()
            number = text.replace(separator, "")
        elif commas == 1:
            left, right = text.split(",")
            thousands = len(right) == 3 and len(left) <= 3 and left.strip("0") != ""
            number = left + right if thousands else left + "." + right
        else:
            number = text
    else:
        raise invalid()

    try:
        value = flt(Decimal(number))
    except (InvalidOperation, ValueError):
        raise invalid()
    if value == 0:
        return 0.0
    return -value if negative else value


def _parse_amount_value(value):
    """Amount cell (text or number) as a float; blank is 0. Raises StatementParseError."""
    if value is None:
        return 0.0
    if isinstance(value, bool):
        raise StatementParseError(_("'{0}' is not a valid amount.").format(value))
    if isinstance(value, (int, float, Decimal)):
        number = flt(value)
        if math.isnan(number) or math.isinf(number):
            raise StatementParseError(_("'{0}' is not a valid amount.").format(value))
        return number
    return _amount_text_to_number(value)


def parse_amount(amount_str, row=None):
    """Parse one statement amount; throws with the offending value (and row)."""
    try:
        return _parse_amount_value(amount_str)
    except StatementParseError as exc:
        logger.error("Failed to parse amount '%s'", amount_str)
        frappe.throw(_row_message(row, str(exc)))


def _row_message(row, message):
    if row:
        return _("Row {0}: {1}").format(row, message)
    return message


def parse_json_if_required(data):
    if isinstance(data, str):
        value = data.strip()
        if not value:
            return {}
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            logger.warning("Failed to parse JSON from importer data: %s", value[:200])
            return {}

    if isinstance(data, dict):
        return data

    return {}


def is_truthy(value):
    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return value == 1

    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def get_selected_bank_mapping(importer_data):
    selected_mapping = {}

    if importer_data.get("date_select"):
        selected_mapping["date"] = importer_data.get("date_select")

    if is_truthy(importer_data.get("same_amount_field")):
        amount_field = importer_data.get("amount_select")
        if amount_field:
            selected_mapping["deposit"] = amount_field
            selected_mapping["withdrawal"] = amount_field
    else:
        if importer_data.get("deposit_select"):
            selected_mapping["deposit"] = importer_data.get("deposit_select")
        if importer_data.get("withdrawal_select"):
            selected_mapping["withdrawal"] = importer_data.get("withdrawal_select")

    if importer_data.get("description_select"):
        selected_mapping["description"] = importer_data.get("description_select")

    if importer_data.get("reference_number_select"):
        selected_mapping["reference_number"] = importer_data.get("reference_number_select")

    if importer_data.get("particulars_select"):
        selected_mapping["custom_particulars"] = importer_data.get("particulars_select")

    if importer_data.get("code_select"):
        selected_mapping["custom_code"] = importer_data.get("code_select")

    if importer_data.get("other_party_select"):
        selected_mapping["bank_party_name"] = importer_data.get("other_party_select")

    return selected_mapping


def save_bank_mapping_for_future_use(importer_data):
    bank_account = importer_data.get("bank_account")
    if not bank_account:
        frappe.msgprint(
            _("Could not save field mapping: no Bank Account was specified."),
            title=_("Warning"),
            indicator="orange",
        )
        return

    # The Bank is saved with ignore_permissions below, so the only Bank this may
    # ever touch is the one that belongs to a Bank Account the user can access.
    # It is read from the Bank Account here, never taken from the client.
    bank_account_doc = assert_bank_account_access(bank_account)
    if not bank_account_doc.bank:
        frappe.msgprint(
            _(
                "Could not save field mapping because Bank Account '{0}' is not linked to a Bank."
            ).format(bank_account),
            title=_("Warning"),
            indicator="orange",
        )
        return

    bank = frappe.get_doc("Bank", bank_account_doc.bank)
    selected_mapping = get_selected_bank_mapping(importer_data)

    if hasattr(bank, "bank_transaction_mapping"):
        bank.bank_transaction_mapping = [
            row
            for row in bank.bank_transaction_mapping
            if row.bank_transaction_field not in BANK_MAPPING_FIELD_ORDER
        ]

        for fieldname in BANK_MAPPING_FIELD_ORDER:
            file_field = cstr(selected_mapping.get(fieldname)).strip()[:140]
            # "(none)" only stands in for a missing reference column
            if file_field and file_field != EMPTY_REFERENCE_HEADER:
                bank.append(
                    "bank_transaction_mapping",
                    {"bank_transaction_field": fieldname, "file_field": file_field},
                )

    # only a known format name may be stored on the Bank
    date_format = importer_data.get("date_format")
    if date_format == "Auto" or date_format in DATE_FORMAT_PATTERNS:
        bank.bank_statement_date_format = date_format

    bank.save(ignore_permissions=True)


def normalize_mapping_for_headers(mapping, data_headers):
    mapping = dict(mapping)

    fields_to_validate = [
        ("date_select", _("Date"), True),
        ("description_select", _("Description"), True),
        ("reference_number_select", _("Reference Number"), True),
    ]
    if is_truthy(mapping.get("same_amount_field")):
        fields_to_validate.append(("amount_select", _("Amount"), True))
    else:
        fields_to_validate.append(("deposit_select", _("Deposit"), True))
        fields_to_validate.append(("withdrawal_select", _("Withdrawal"), True))

    for select_key, label in (
        ("particulars_select", _("Particulars")),
        ("code_select", _("Code")),
        ("other_party_select", _("Other Party")),
    ):
        fields_to_validate.append((select_key, label, False))

    missing_required = []
    missing = []
    skipped_optional = []
    for select_key, label, is_required in fields_to_validate:
        value = mapping.get(select_key)
        if not value:
            if is_required:
                missing_required.append(str(label))
        elif value not in data_headers:
            if is_required:
                missing.append(f"'{value}' (selected for {label})")
            else:
                skipped_optional.append(str(label))
                mapping[select_key] = ""

    if missing_required or missing:
        parts = []
        if missing_required:
            parts.append(
                _("Please select a column for: {0}.").format(", ".join(missing_required))
            )
        if missing:
            parts.append(
                _(
                    "The following selected columns are not present in the uploaded file: {0}."
                ).format(", ".join(missing))
            )
        parts.append(_("Re-pick the correct columns from the dropdowns above and click Map Fields again."))
        frappe.throw(
            " ".join(parts),
            title=_("Column Mapping Error"),
        )

    if skipped_optional:
        frappe.msgprint(
            _("Optional saved column mappings were skipped because they are not in this file: {0}.").format(
                ", ".join(skipped_optional)
            ),
            title=_("Saved Field Mapping"),
            indicator="orange",
        )

    return mapping


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

DATE_FORMAT_PATTERNS = {
    # Slash-separated
    "Y/m/d": "%Y/%m/%d",
    "d/m/Y": "%d/%m/%Y",
    "dd/mm/YY": "%d/%m/%y",
    "m/d/Y": "%m/%d/%Y",
    "m/d/YY": "%m/%d/%y",
    "Y/d/m": "%Y/%d/%m",
    # Dash-separated
    "m-d-Y": "%m-%d-%Y",
    "m-d-YY": "%m-%d-%y",
    "d-m-Y": "%d-%m-%Y",
    "d-m-YY": "%d-%m-%y",
    "Y-m-d": "%Y-%m-%d",
    # Dot-separated
    "d.m.Y": "%d.%m.%Y",
    "d.m.YY": "%d.%m.%y",
    "Y.m.d": "%Y.%m.%d",
    "m.d.Y": "%m.%d.%Y",
    "m.d.YY": "%m.%d.%y",
}

# (4-digit year, 2-digit year) format names per separator
_DAY_FIRST = {"/": ("d/m/Y", "dd/mm/YY"), "-": ("d-m-Y", "d-m-YY"), ".": ("d.m.Y", "d.m.YY")}
_MONTH_FIRST = {"/": ("m/d/Y", "m/d/YY"), "-": ("m-d-Y", "m-d-YY"), ".": ("m.d.Y", "m.d.YY")}

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:$|[ T])")
_COMPACT_DATE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_DATE_PARTS = re.compile(r"^(\d{1,4})([/.\-])(\d{1,2})\2(\d{1,4})$")
_TIME_TAIL = re.compile(
    r"^(\S+?)[ T]+\d{1,2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?\s*(?:[AaPp][Mm])?\s*(?:Z|[+-]\d{2}:?\d{2})?$"
)


def _date_core(text):
    """The date part of ``05/03/2024 14:22:10`` (bank exports often add a time)."""
    match = _TIME_TAIL.match(text)
    return match.group(1) if match else text


def _date_shape(core):
    """Split a numeric date into its layout and its two non-year parts."""
    match = _DATE_PARTS.match(core)
    if not match:
        return None
    first, separator, second, third = match.groups()
    if len(first) == 4:
        if len(third) > 2:
            return None
        return {"year_first": True, "sep": separator, "year_digits": 4,
                "a": cint(second), "b": cint(third)}
    if len(first) <= 2 and len(third) in (2, 4):
        return {"year_first": False, "sep": separator, "year_digits": len(third),
                "a": cint(first), "b": cint(second)}
    return None


def _detection(format_name, ambiguous=False, warning=None, candidates=None):
    return {
        "format": format_name,
        "ambiguous": ambiguous,
        "warning": warning,
        "candidates": candidates if candidates is not None else ([format_name] if format_name else []),
    }


def detect_date_format(values):
    """Work out one date format for a whole column, looking at EVERY value.

    * a first part above 12 means day first, a second part above 12 means month
      first (``13/03/2024`` / ``03/13/2024``);
    * with neither the column is ambiguous: day first is assumed and a warning
      says so (``candidates`` lists the two formats the caller may choose from);
    * contradictory evidence (or mixed layouts) gives ``format = None`` and a
      warning: the user has to pick the format.

    Real dates, ISO text and cells that are not dates at all (a "Total" line,
    a repeated header) never influence the result. Returns a dict with
    ``format`` ("Auto" when nothing needs a format), ``ambiguous``, ``warning``
    and ``candidates``.
    """
    shapes = set()
    day_first_example = month_first_example = None
    swapped_example = normal_example = None

    for value in values:
        if value is None or isinstance(value, date):
            continue
        text = cstr(value).strip()
        if not text:
            continue
        core = _date_core(text)
        if _ISO_DATE.match(core):
            continue
        shape = _date_shape(core)
        if not shape:
            continue
        shapes.add((shape["year_first"], shape["sep"], shape["year_digits"]))
        first, second = shape["a"], shape["b"]
        if shape["year_first"]:
            if first > 12 >= second:
                swapped_example = swapped_example or text
            elif second > 12 >= first:
                normal_example = normal_example or text
        elif first > 12 >= second:
            day_first_example = day_first_example or text
        elif second > 12 >= first:
            month_first_example = month_first_example or text

    if not shapes:
        return _detection("Auto")

    manual = _("Pick the date format manually.")
    if len(shapes) > 1:
        return _detection(
            None,
            warning=_("The dates in this file use more than one layout (separator or year length).")
            + " " + manual,
        )

    year_first, separator, year_digits = next(iter(shapes))
    if year_first:
        if swapped_example and normal_example:
            return _detection(
                None,
                warning=_("The dates mix year/month/day ({0}) and year/day/month ({1}).").format(
                    normal_example, swapped_example
                ) + " " + manual,
            )
        if swapped_example:
            if separator != "/":
                return _detection(
                    None,
                    warning=_("The dates look like year/day/month ({0}), which is only supported with '/'.").format(
                        swapped_example
                    ) + " " + manual,
                )
            return _detection("Y/d/m")
        return _detection("Y%sm%sd" % (separator, separator))

    index = 0 if year_digits == 4 else 1
    day_first, month_first = _DAY_FIRST[separator][index], _MONTH_FIRST[separator][index]
    if day_first_example and month_first_example:
        return _detection(
            None,
            warning=_("The dates mix day first ({0}) and month first ({1}).").format(
                day_first_example, month_first_example
            ) + " " + manual,
        )
    if day_first_example:
        return _detection(day_first)
    if month_first_example:
        return _detection(month_first)
    return _detection(
        day_first,
        ambiguous=True,
        candidates=[day_first, month_first],
        warning=_(
            "Every date in this file has a day and a month of 12 or less, so day/month order "
            "cannot be told apart. Dates were read as day/month/year ({0}). If this bank uses "
            "month/day/year, pick {1} as the date format and preview again."
        ).format(day_first, month_first),
    )


def _parse_free_date(text):
    """Date written in words ("5 Mar 2024", "05-Mar-24"); numbers read day first."""
    from dateutil import parser as date_parser

    if not re.search(r"\d", text):
        raise ValueError(text)
    # a date with a missing day, month or year would silently borrow today's
    first = date_parser.parse(text, dayfirst=True, default=datetime(1904, 1, 1))
    second = date_parser.parse(text, dayfirst=True, default=datetime(1905, 2, 2))
    if (first.year, first.month, first.day) != (second.year, second.month, second.day):
        raise ValueError(text)
    return first.date()


def _parse_date_value(value, date_format="Auto"):
    """One date cell as a ``date``. Raises StatementParseError, never falls back
    to another format: a value that does not fit the chosen format is an error."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = cstr(value).strip()
    if not text:
        raise StatementParseError(_("Date is empty."))
    core = _date_core(text)
    date_format = date_format or "Auto"

    if date_format == "Auto":
        iso = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:$|[ T])", core)
        compact = _COMPACT_DATE.match(core)
        try:
            if iso:
                return date(cint(iso.group(1)), cint(iso.group(2)), cint(iso.group(3)))
            if compact and 1900 <= cint(compact.group(1)) <= 2100:
                return date(cint(compact.group(1)), cint(compact.group(2)), cint(compact.group(3)))
            return _parse_free_date(text)
        except (ValueError, OverflowError):
            raise StatementParseError(_("'{0}' is not a valid date.").format(text))

    pattern = DATE_FORMAT_PATTERNS.get(date_format)
    if not pattern:
        raise StatementParseError(_("Unknown date format {0}.").format(date_format))
    try:
        return datetime.strptime(core, pattern).date()
    except ValueError:
        raise StatementParseError(
            _("'{0}' does not match the date format {1}.").format(text, date_format)
        )


def parse_date(date_str, format="Auto", row=None):
    """Parse one date. Empty gives None; anything unreadable throws (with the row).

    Unlike before there is no silent month-first fallback: ``Auto`` reads ISO
    dates, ``yyyymmdd`` and dates written with a month name, and numeric dates
    day first. Use ``detect_date_format`` on the whole column to pick a format.
    """
    if date_str is None or (isinstance(date_str, str) and not date_str.strip()):
        return None
    try:
        return _parse_date_value(date_str, format)
    except StatementParseError as exc:
        logger.warning(
            "Failed to parse date '%s' with format '%s': %s", date_str, format, str(exc)
        )
        frappe.throw(_row_message(row, str(exc)))


# ---------------------------------------------------------------------------
# Duplicates (shared by the desk importer and the Bank Reco import screen)
# ---------------------------------------------------------------------------


def row_key(date_value, deposit, withdrawal, reference, description):
    return (
        str(date_value),
        round(flt(deposit), 3),
        round(flt(withdrawal), 3),
        cstr(reference or "").strip(),
        cstr(description or "").strip(),
    )


def mark_duplicates(rows, bank_account):
    """Set ``row["duplicate"]`` on every row that is already in the system.

    ``rows`` are dicts with date, deposit, withdrawal, reference_number and
    description as read from the file (before the credit-card flip). Compared on
    date, amounts, reference and description as stored (so after the flip),
    ignoring cancelled transactions. Identical lines inside one file are real
    separate transactions: with m copies already in the system, only the first
    m copies in the file are duplicates. Covers every row, whatever the order.
    """
    for row in rows:
        row["duplicate"] = False
    dated = [str(row["date"]) for row in rows if row.get("date")]
    if not dated:
        return rows

    is_card = bool(frappe.db.get_value("Bank Account", bank_account, "is_credit_card"))
    existing = Counter()
    for item in frappe.get_all(
        "Bank Transaction",
        filters={
            "bank_account": bank_account,
            "docstatus": ["!=", 2],
            "date": ["between", [min(dated), max(dated)]],
        },
        fields=["date", "deposit", "withdrawal", "reference_number", "description"],
    ):
        existing[
            row_key(item.date, item.deposit, item.withdrawal, item.reference_number, item.description)
        ] += 1

    for row in rows:
        if not row.get("date"):
            continue
        deposit, withdrawal = (
            (row["withdrawal"], row["deposit"]) if is_card else (row["deposit"], row["withdrawal"])
        )
        key = row_key(row["date"], deposit, withdrawal, row["reference_number"], row["description"])
        if existing[key] > 0:
            existing[key] -= 1
            row["duplicate"] = True
    return rows


# ---------------------------------------------------------------------------
# Building the import table
# ---------------------------------------------------------------------------


def _cell_text(value):
    """Cell as stripped text; None when blank. 123.0 from a sheet reads "123"."""
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = cint(value)
    return cstr(value).strip() or None


def _cell(row, index):
    if index is None or index >= len(row):
        return None
    return row[index]


def _read_amounts(data_row, columns, same_amount_field, positive_field):
    """(deposit, withdrawal) of one row; never negative."""
    if same_amount_field:
        raw_amount, direction = split_cr_dr(_cell(data_row, columns["amount"]))
        value = _parse_amount_value(raw_amount)
        amount = abs(value)
        if direction:
            # an explicit CR/DR marker decides the column, whatever the sign setting says
            return (amount, 0.0) if direction == "CR" else (0.0, amount)
        to_deposit = (value >= 0) == (positive_field == "Deposit")
        return (amount, 0.0) if to_deposit else (0.0, amount)

    deposit = abs(_parse_amount_value(split_cr_dr(_cell(data_row, columns["deposit"]))[0]))
    withdrawal = abs(_parse_amount_value(split_cr_dr(_cell(data_row, columns["withdrawal"]))[0]))
    return deposit, withdrawal


def build_table_with_report(
    mapping, data_headers, data_body, row_numbers=None, check_duplicates=True
):
    """Apply a column mapping to the file rows.

    Returns ``(table, report)``. ``table`` is the header row followed by one row
    per importable transaction. ``report`` has the rows that cannot be imported
    (``errors``: row number, field, message), ``skipped_zero`` (rows without any
    amount, such as balance lines), ``warnings`` and the date format used.
    A bad cell never aborts the file; it is reported with its row number.
    ``row_numbers`` are the rows' numbers in the file (default: position + 2,
    the header being row 1).
    """
    bank_account = frappe.get_doc("Bank Account", mapping["bank_account"])
    account = frappe.get_doc("Account", bank_account.account)
    currency = account.account_currency

    mapping = normalize_mapping_for_headers(mapping, data_headers)
    positive_field = mapping.get("positive_field") or "Deposit"
    if positive_field not in ("Deposit", "Withdrawal"):
        frappe.throw(_("Positive amounts must be Deposit or Withdrawal."))
    same_amount_field = is_truthy(mapping.get("same_amount_field"))

    def column(select_key):
        name = mapping.get(select_key)
        return data_headers.index(name) if name and name in data_headers else None

    columns = {
        "date": column("date_select"),
        "amount": column("amount_select"),
        "deposit": column("deposit_select"),
        "withdrawal": column("withdrawal_select"),
        "description": column("description_select"),
        "reference": column("reference_number_select"),
        "particulars": column("particulars_select"),
        "code": column("code_select"),
        "other_party": column("other_party_select"),
    }

    live_rows = []
    for position, data_row in enumerate(data_body):
        if not data_row or all(cell is None or cell == "" for cell in data_row):
            continue
        number = row_numbers[position] if row_numbers else position + 2
        live_rows.append((number, data_row))

    errors, warnings, skipped_zero = [], [], 0
    date_format = mapping.get("date_format") or "Auto"
    if date_format == "Auto":
        detection = detect_date_format(_cell(row, columns["date"]) for _n, row in live_rows)
        if detection["format"] is None:
            frappe.throw(detection["warning"])
        date_format = detection["format"]
        if detection["warning"]:
            warnings.append(detection["warning"])

    entries = []
    for number, data_row in live_rows:
        try:
            deposit, withdrawal = _read_amounts(data_row, columns, same_amount_field, positive_field)
        except StatementParseError as exc:
            errors.append({"row": number, "field": "amount", "message": str(exc)})
            continue
        if deposit and withdrawal:
            errors.append({
                "row": number,
                "field": "amount",
                "message": _("Both the deposit ({0}) and the withdrawal ({1}) have an amount.").format(
                    deposit, withdrawal
                ),
            })
            continue
        if not deposit and not withdrawal:
            skipped_zero += 1
            continue
        try:
            row_date = _parse_date_value(_cell(data_row, columns["date"]), date_format)
        except StatementParseError as exc:
            errors.append({"row": number, "field": "date", "message": str(exc)})
            continue

        entries.append({
            "source_row": number,
            "date": row_date,
            "deposit": deposit,
            "withdrawal": withdrawal,
            "description": _cell_text(_cell(data_row, columns["description"])),
            "reference_number": _cell_text(_cell(data_row, columns["reference"])),
            "particulars": _cell_text(_cell(data_row, columns["particulars"])) or "",
            "code": _cell_text(_cell(data_row, columns["code"])) or "",
            "other_party": _cell_text(_cell(data_row, columns["other_party"])) or "",
        })

    if check_duplicates:
        mark_duplicates(entries, bank_account.name)

    table = [list(TABLE_HEADER)]
    for entry in entries:
        table.append([
            entry["date"],
            entry["deposit"],
            entry["withdrawal"],
            entry["description"],
            entry["reference_number"],
            bank_account.name,
            currency,
            1 if entry.get("duplicate") else 0,
            entry["particulars"],
            entry["code"],
            entry["other_party"],
        ])

    report = {
        "errors": errors,
        "skipped_zero": skipped_zero,
        "warnings": warnings,
        "date_format": date_format,
        "row_numbers": [entry["source_row"] for entry in entries],
    }
    return table, report


def _announce_report(report):
    """Tell a desk user what was left out of the table (no JS needed)."""
    lines = list(report["warnings"])
    if report["skipped_zero"]:
        lines.append(
            _("{0} row(s) without an amount (for example balance lines) were skipped.").format(
                report["skipped_zero"]
            )
        )
    errors = report["errors"]
    if errors:
        lines.append(_("{0} row(s) could not be read and will not be imported:").format(len(errors)))
        for error in errors[:MAX_ROW_ERRORS_SHOWN]:
            lines.append(_("Row {0}: {1}").format(error["row"], error["message"]))
        if len(errors) > MAX_ROW_ERRORS_SHOWN:
            lines.append(_("...and {0} more.").format(len(errors) - MAX_ROW_ERRORS_SHOWN))
    if lines:
        frappe.msgprint(
            "<br>".join(frappe.utils.escape_html(line) for line in lines),
            title=_("Statement check"),
            indicator="orange",
        )


def build_table(mapping, data_headers, data_body, row_numbers=None):
    table, report = build_table_with_report(mapping, data_headers, data_body, row_numbers)
    _announce_report(report)
    return table


# ---------------------------------------------------------------------------
# Whitelisted desk-importer methods
# ---------------------------------------------------------------------------


def _load_json(value, label):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            frappe.throw(_("Could not read the {0} sent by the browser.").format(label))
    return value


@frappe.whitelist()
def form_start_import(data_import, bank_account):
    require_bank_rec_permission()
    assert_bank_account_access(bank_account)
    # start_import reads the file only through get_readable_file_doc
    return start_import(data_import, bank_account)


@frappe.whitelist()
def map_fields(data, data_headers, data_body):
    require_bank_rec_permission()
    mapping = _load_json(data, "mapping")
    if not isinstance(mapping, dict):
        frappe.throw(_("The column mapping is invalid."))
    assert_bank_account_access(mapping.get("bank_account"))
    return build_table(
        mapping, _load_json(data_headers, "headers"), _load_json(data_body, "rows")
    )


@frappe.whitelist()
def get_last_transaction(bank_account):
    require_bank_rec_permission()
    assert_bank_account_access(bank_account)
    logger.info("Getting last transaction for bank account: %s", bank_account)
    # get the last bank transaction doc ordered by date desc for the given bank account
    last_transaction = frappe.get_all(
        "Bank Transaction",
        filters={"bank_account": bank_account, "docstatus": 1},
        fields=["date", "deposit", "withdrawal"],
        order_by="date desc, creation desc",
        limit_page_length=1,
    )

    if last_transaction:
        return last_transaction[0]
    return None


def _authorised_bank_accounts(dataset, importer_data):
    """The bank accounts a dataset writes to, each checked for this user.

    The accounts come from the rows themselves; if the page also names one it has
    to be the same, so the saved mapping can never land on a different bank.
    """
    accounts = []
    for item in dataset:
        account = item[5] if len(item) > 5 else None
        if not account:
            frappe.throw(_("A row has no Bank Account."))
        if account not in accounts:
            accounts.append(account)

    requested = cstr(importer_data.get("bank_account")).strip()
    if requested and accounts and accounts != [requested]:
        frappe.throw(_("The rows do not belong to the selected Bank Account."))
    if requested and not accounts:
        accounts = [requested]

    for account in accounts:
        assert_bank_account_access(account)
    return accounts


@frappe.whitelist()
def publish_records(data_import, importer_data=None):
    require_bank_rec_permission()
    frappe.has_permission("Bank Transaction", "create", throw=True)
    parsed_importer_data = parse_json_if_required(importer_data)
    dataset = _load_json(data_import, "rows")[1:]
    bank_accounts = _authorised_bank_accounts(dataset, parsed_importer_data)

    import_success = False
    try:
        logger.info("Importing %s bank transactions", len(dataset))
        rows = [
            {
                "item": item,
                "bank_account": item[5],
                "date": item[0],
                "deposit": flt(item[1]),
                "withdrawal": flt(item[2]),
                "description": item[3],
                "reference_number": item[4],
                "flagged": len(item) > 7 and cint(item[7]) == 1,
            }
            for item in dataset
        ]
        # the browser's flag is advisory: check every row again, any order
        for account in bank_accounts:
            mark_duplicates([row for row in rows if row["bank_account"] == account], account)

        imported = skipped = 0
        for row in rows:
            if row["flagged"] or row["duplicate"]:
                skipped += 1
                continue
            item = row["item"]
            bank_transaction_dict = {
                "date": item[0],
                "bank_account": item[5],
                "currency": item[6],
                "deposit": item[1],
                "withdrawal": item[2],
                "reference_number": str(item[4]) if item[4] is not None else None,
                "description": str(item[3]) if item[3] else None,
            }

            particulars = item[8] if len(item) > 8 else None
            code = None
            other_party = None

            # New dataset format has: [.., is_duplicated, particulars, code, other_party]
            if len(item) > 10:
                code = item[9]
                other_party = item[10]
            # Legacy dataset format has: [.., is_duplicated, particulars, other_party]
            elif len(item) > 9:
                other_party = item[9]

            if particulars:
                bank_transaction_dict["custom_particulars"] = str(particulars)

            if code:
                bank_transaction_dict["custom_code"] = str(code)

            if other_party:
                bank_transaction_dict["bank_party_name"] = str(other_party)

            bank_transaction = frappe.new_doc("Bank Transaction")
            bank_transaction.update(bank_transaction_dict)
            flip_amount_for_credit_card(bank_transaction)
            bank_transaction.insert()
            bank_transaction.submit()
            imported += 1
        logger.info("Bank transactions submitted successfully")
        import_success = True
        frappe.msgprint(
            _("{0} transaction(s) imported, {1} duplicate(s) skipped.").format(imported, skipped),
            alert=True,
            indicator="green",
        )
    except Exception as e:
        logger.error("Publish records error: %s", str(e), exc_info=True)
        frappe.db.rollback()
        frappe.msgprint(
            _("Nothing was imported: {0}").format(frappe.utils.escape_html(cstr(e)[:300])),
            indicator="red",
        )

    # Save bank mapping independently of import outcome
    if is_truthy(parsed_importer_data.get("save_mapping_for_future_use")) and len(bank_accounts) == 1:
        try:
            save_bank_mapping_for_future_use(
                {**parsed_importer_data, "bank_account": bank_accounts[0]}
            )
        except Exception as e:
            logger.error("Failed to save bank mapping: %s", str(e), exc_info=True)
            frappe.msgprint(
                _("Saving the field mapping for future use failed: {0}").format(str(e)),
                title=_("Warning"),
                indicator="orange",
            )

    return import_success
