# Copyright (c) 2026, High Flyer and Contributors
# See license.txt

"""Unit tests for the statement import logic that need no site and no database.

Run with the bench interpreter from the app repo:

    PYTHONPATH=. ~/frappe-bench/env/bin/python -m unittest \
        bank_reco.bank_reco.doctype.bank_statement_importer.test_statement_parsing -v

frappe.db / get_all / get_doc / throw / msgprint and the translation function
are replaced by mocks; nothing here touches a bench.
"""

import json
import logging
import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest import mock

import frappe

# the module opens a log file at import time: give it a plain logger instead
with mock.patch("frappe.logger", return_value=logging.getLogger("bank_reco_test")):
    from bank_reco.bank_reco.doctype.bank_statement_importer import (
        bank_statement_importer as importer,
    )
    from bank_reco.api import statement_import


def _translate(message, *args, **kwargs):
    return message


def _throw(message, *args, **kwargs):
    raise frappe.ValidationError(message)


class MockedFrappeCase(unittest.TestCase):
    """Replaces what needs a site: translation, throw, msgprint, logging."""

    def setUp(self):
        # the @frappe.whitelist() wrapper reads local.flags; a bench run has it already
        if not hasattr(frappe.local, "flags"):
            frappe.local.flags = frappe._dict()
            self.addCleanup(lambda: delattr(frappe.local, "flags"))
        self.msgprint = mock.MagicMock()
        for target, new in (
            (importer, "_"),
            (statement_import, "_"),
        ):
            patcher = mock.patch.object(target, new, _translate)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, replacement in (("throw", _throw), ("msgprint", self.msgprint)):
            patcher = mock.patch("frappe." + name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)


class TestAmountParser(MockedFrappeCase):
    """Fix 1: one amount parser for every screen."""

    def test_valid_amounts(self):
        cases = {
            "250,50": 250.5,
            "1.250,50": 1250.5,
            "1,250.50": 1250.5,
            "1,250": 1250.0,  # ambiguous 3-digit group stays a thousands separator
            "12,345": 12345.0,
            "1.250": 1.25,  # a single dot is always a decimal point, as before
            "1,250,000": 1250000.0,
            "1.250.000": 1250000.0,
            "1,25,000.50": 125000.5,  # Indian grouping
            "(1,250.00)": -1250.0,
            "1,250.00-": -1250.0,
            "-1,250.00": -1250.0,
            "+5": 5.0,
            "5-": -5.0,
            "(5)": -5.0,
            "SAR 1,250.00": 1250.0,
            "Rs. 1,250.00": 1250.0,
            "$ 1,250.00": 1250.0,
            "€1.250,00": 1250.0,
            "1 250,50": 1250.5,
            "1'250.50": 1250.5,
            "12 345": 12345.0,
            "0,500": 0.5,  # a zero before the comma cannot be a thousands group
            "0.000": 0.0,
            "0.00": 0.0,
            "0,00": 0.0,
            "-0.00": 0.0,
            ".50": 0.5,
            "-": 0.0,
            "": 0.0,
            "  ": 0.0,
            "١٢٣٫٤٥": 123.45,  # Arabic-Indic digits
            "1,250.00‏": 1250.0,  # direction mark after the number
            "−1,250.00": -1250.0,  # unicode minus
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(importer.parse_amount(text), expected)

    def test_numbers_and_blanks(self):
        self.assertEqual(importer.parse_amount(None), 0.0)
        self.assertEqual(importer.parse_amount(12.5), 12.5)
        self.assertEqual(importer.parse_amount(3), 3.0)
        self.assertEqual(importer.parse_amount(0), 0.0)

    def test_negative_zero_is_zero(self):
        self.assertEqual(str(importer.parse_amount("-0.000")), "0.0")

    def test_unparseable_text_throws_with_value_and_row(self):
        for text in (
            "abc",
            "12/03/2024",
            "1.2.3",
            "1,2,3",
            "--5",
            "-5-",
            "PAYMENT 5",
            "1 2",
            "12abc34",
            "1.250,5.0",
        ):
            with self.subTest(text=text):
                with self.assertRaises(frappe.ValidationError) as caught:
                    importer.parse_amount(text, row=7)
                self.assertIn(text, str(caught.exception))
                self.assertIn("Row 7", str(caught.exception))

    def test_bad_numbers_throw(self):
        for value in (True, float("nan"), float("inf")):
            with self.subTest(value=value):
                with self.assertRaises(frappe.ValidationError):
                    importer.parse_amount(value)

    def test_amount_without_row_has_no_row_prefix(self):
        with self.assertRaises(frappe.ValidationError) as caught:
            importer.parse_amount("abc")
        self.assertNotIn("Row", str(caught.exception))


class TestDateDetection(MockedFrappeCase):
    """Fix 2: the format comes from ALL rows; ambiguity is reported."""

    def test_first_part_above_12_is_day_first(self):
        result = importer.detect_date_format(["05/03/2024", "13/03/2024"])
        self.assertEqual(result["format"], "d/m/Y")
        self.assertFalse(result["ambiguous"])
        self.assertIsNone(result["warning"])

    def test_second_part_above_12_is_month_first(self):
        result = importer.detect_date_format(["03/05/2024", "03/13/2024"])
        self.assertEqual(result["format"], "m/d/Y")
        self.assertFalse(result["ambiguous"])

    def test_ambiguous_defaults_to_day_first_with_a_warning(self):
        result = importer.detect_date_format(["05/03/2024", "06/04/2024", "01/02/2024"])
        self.assertEqual(result["format"], "d/m/Y")
        self.assertTrue(result["ambiguous"])
        self.assertTrue(result["warning"])
        self.assertEqual(result["candidates"], ["d/m/Y", "m/d/Y"])

    def test_evidence_after_row_25_is_used(self):
        values = ["%02d/03/2024" % day for day in range(1, 12)] * 4  # 44 ambiguous rows
        self.assertEqual(importer.detect_date_format(values)["format"], "d/m/Y")
        self.assertTrue(importer.detect_date_format(values)["ambiguous"])
        self.assertEqual(importer.detect_date_format(values + ["27/03/2024"])["format"], "d/m/Y")
        self.assertFalse(importer.detect_date_format(values + ["27/03/2024"])["ambiguous"])
        month_first = ["03/%02d/2024" % day for day in range(1, 12)] * 4
        self.assertEqual(importer.detect_date_format(month_first + ["03/27/2024"])["format"], "m/d/Y")

    def test_contradicting_evidence_needs_a_manual_choice(self):
        result = importer.detect_date_format(["13/03/2024", "03/13/2024"])
        self.assertIsNone(result["format"])
        self.assertIn("13/03/2024", result["warning"])

    def test_mixed_layouts_need_a_manual_choice(self):
        result = importer.detect_date_format(["13/03/2024", "13-03-2024"])
        self.assertIsNone(result["format"])
        self.assertTrue(result["warning"])

    def test_rows_that_are_not_dates_are_ignored(self):
        result = importer.detect_date_format(["Date", "Total", None, "", "13/03/2024"])
        self.assertEqual(result["format"], "d/m/Y")

    def test_dates_and_iso_text_need_no_format(self):
        values = [date(2024, 3, 5), datetime(2024, 3, 6, 10, 0), "2024-03-07", "2024-03-08 10:11:12"]
        self.assertEqual(importer.detect_date_format(values)["format"], "Auto")
        self.assertEqual(importer.detect_date_format([])["format"], "Auto")

    def test_separators_and_year_length(self):
        self.assertEqual(importer.detect_date_format(["13/03/24"])["format"], "dd/mm/YY")
        self.assertEqual(importer.detect_date_format(["03-13-2024"])["format"], "m-d-Y")
        self.assertEqual(importer.detect_date_format(["13.03.2024"])["format"], "d.m.Y")
        self.assertEqual(importer.detect_date_format(["03.13.24"])["format"], "m.d.YY")
        self.assertEqual(importer.detect_date_format(["2024/03/05"])["format"], "Y/m/d")
        self.assertEqual(importer.detect_date_format(["2024/13/05"])["format"], "Y/d/m")

    def test_time_after_the_date_is_ignored(self):
        self.assertEqual(importer.detect_date_format(["13/03/2024 14:22"])["format"], "d/m/Y")
        self.assertEqual(importer.detect_date_format(["03/13/2024 02:22:10 PM"])["format"], "m/d/Y")


class TestParseDate(MockedFrappeCase):
    """Fix 2: no silent month-first fallback; unreadable dates are errors."""

    def test_explicit_formats(self):
        expected = date(2024, 5, 9)
        self.assertEqual(importer.parse_date("9/05/2024", "d/m/Y"), expected)
        self.assertEqual(importer.parse_date("5/09/2024", "m/d/Y"), expected)
        self.assertEqual(importer.parse_date("2024/05/09", "Y/m/d"), expected)
        self.assertEqual(importer.parse_date("2024/09/05", "Y/d/m"), expected)
        self.assertEqual(importer.parse_date("09.05.24", "d.m.YY"), expected)
        self.assertEqual(importer.parse_date("09/05/2024 14:22", "d/m/Y"), expected)

    def test_explicit_format_that_fails_is_a_row_error_not_a_fallback(self):
        # the old code fell back to month-first getdate() and returned 13 May here
        with self.assertRaises(frappe.ValidationError) as caught:
            importer.parse_date("05/13/2024", "d/m/Y", row=4)
        message = str(caught.exception)
        self.assertIn("Row 4", message)
        self.assertIn("05/13/2024", message)
        self.assertIn("d/m/Y", message)

    def test_two_digit_year_with_a_four_digit_format_is_an_error(self):
        with self.assertRaises(frappe.ValidationError):
            importer.parse_date("05/03/24", "d/m/Y")

    def test_iso_text_under_a_non_iso_format_is_an_error(self):
        with self.assertRaises(frappe.ValidationError):
            importer.parse_date("2024-03-05", "d/m/Y")

    def test_unknown_format_name(self):
        with self.assertRaises(frappe.ValidationError):
            importer.parse_date("05/03/2024", "x/y/z")

    def test_auto_reads_unambiguous_forms(self):
        self.assertEqual(importer.parse_date("2024-03-05", "Auto"), date(2024, 3, 5))
        self.assertEqual(importer.parse_date("2024-03-05 10:11:12", "Auto"), date(2024, 3, 5))
        self.assertEqual(importer.parse_date("20240305", "Auto"), date(2024, 3, 5))
        self.assertEqual(importer.parse_date("5 Mar 2024", "Auto"), date(2024, 3, 5))
        self.assertEqual(importer.parse_date("05-Mar-24", "Auto"), date(2024, 3, 5))
        self.assertEqual(importer.parse_date(datetime(2024, 3, 5, 9, 30), "Auto"), date(2024, 3, 5))
        self.assertEqual(importer.parse_date(date(2024, 3, 5), "d/m/Y"), date(2024, 3, 5))

    def test_auto_numeric_dates_are_day_first_not_month_first(self):
        self.assertEqual(importer.parse_date("05/03/2024", "Auto"), date(2024, 3, 5))

    def test_blank_is_none(self):
        self.assertIsNone(importer.parse_date(None, "d/m/Y"))
        self.assertIsNone(importer.parse_date("  ", "Auto"))

    def test_text_that_is_not_a_date_throws_a_validation_error(self):
        for text in ("Total", "Date", "Opening Balance", "5", "12345", "13/13/2024"):
            with self.subTest(text=text):
                with self.assertRaises(frappe.ValidationError):
                    importer.parse_date(text, "Auto", row=9)


class BuildTableCase(MockedFrappeCase):
    HEADERS = ["Date", "Description", "Reference", "Debit", "Credit"]

    def setUp(self):
        super().setUp()

        def get_doc(doctype, name=None):
            if doctype == "Bank Account":
                return SimpleNamespace(name="BA-1", account="ACC-1", bank="BANK-1")
            return SimpleNamespace(account_currency="BHD")

        patcher = mock.patch("frappe.get_doc", get_doc)
        patcher.start()
        self.addCleanup(patcher.stop)

    def mapping(self, **extra):
        mapping = {
            "bank_account": "BA-1",
            "date_select": "Date",
            "description_select": "Description",
            "reference_number_select": "Reference",
            "same_amount_field": 0,
            "deposit_select": "Credit",
            "withdrawal_select": "Debit",
            "date_format": "Auto",
        }
        mapping.update(extra)
        return mapping

    def build(self, body, headers=None, row_numbers=None, **extra):
        return importer.build_table_with_report(
            self.mapping(**extra),
            headers or self.HEADERS,
            body,
            row_numbers=row_numbers,
            check_duplicates=False,
        )


class TestDebitCreditHandling(BuildTableCase):
    """Fix 4 and fix 2 (row errors): separate Debit/Credit columns."""

    BODY = [
        ["13/03/2024", "Rent", "R1", "1,250.00", ""],  # row 2: withdrawal
        ["14/03/2024", "Salary", "R2", "", "2,000.00"],  # row 3: deposit
        ["15/03/2024", "Debit shown negative", "R3", "-500.00", ""],  # row 4: abs()
        ["16/03/2024", "Both columns", "R4", "10.00", "20.00"],  # row 5: error
        ["", "B/F", "", "0.000", "0.000"],  # row 6: no amount -> skipped
        ["Total", "", "", "9,999.00", ""],  # row 7: date error, file goes on
        ["17/03/2024", "Bad amount", "R7", "abc", ""],  # row 8: amount error
        ["18/03/2024", "Short row"],  # row 9: missing cells, no amount -> skipped
        ["19/03/2024", "Negative credit", "R9", "", "(75.50)"],  # row 10: abs() -> deposit
    ]

    def test_rows_are_split_into_rows_and_errors(self):
        table, report = self.build(self.BODY)
        rows = {row[4]: row for row in table[1:]}
        self.assertEqual(sorted(rows), ["R1", "R2", "R3", "R9"])
        self.assertEqual((rows["R1"][1], rows["R1"][2]), (0.0, 1250.0))
        self.assertEqual((rows["R2"][1], rows["R2"][2]), (2000.0, 0.0))
        self.assertEqual((rows["R3"][1], rows["R3"][2]), (0.0, 500.0))
        self.assertEqual((rows["R9"][1], rows["R9"][2]), (75.5, 0.0))
        for row in table[1:]:
            self.assertGreaterEqual(row[1], 0)
            self.assertGreaterEqual(row[2], 0)
            self.assertFalse(row[1] and row[2])

    def test_every_error_names_its_row(self):
        _table, report = self.build(self.BODY)
        errors = {e["row"]: e for e in report["errors"]}
        self.assertEqual(sorted(errors), [5, 7, 8])
        self.assertEqual(errors[5]["field"], "amount")  # both non-zero
        self.assertEqual(errors[7]["field"], "date")  # "Total" row
        self.assertIn("Total", errors[7]["message"])
        self.assertEqual(errors[8]["field"], "amount")
        self.assertIn("abc", errors[8]["message"])

    def test_zero_rows_are_skipped_and_counted(self):
        _table, report = self.build(self.BODY)
        self.assertEqual(report["skipped_zero"], 2)  # the B/F line and the short row

    def test_row_numbers_of_the_file_are_used(self):
        body = [["13/03/2024", "A", "1", "10", "20"], ["14/03/2024", "B", "2", "5", ""]]
        _table, report = self.build(body, row_numbers=[14, 40])
        self.assertEqual([e["row"] for e in report["errors"]], [14])
        table, report = self.build(body, row_numbers=[14, 40])
        self.assertEqual(report["row_numbers"], [40])

    def test_single_amount_column_with_signs_and_markers(self):
        headers = ["Date", "Description", "Reference", "Amount"]
        body = [
            ["13/03/2024", "in", "A", "100.00"],
            ["13/03/2024", "out", "B", "-50.00"],
            ["13/03/2024", "marker credit", "C", "75 CR"],
            ["13/03/2024", "marker debit", "D", "30 DR"],
            ["13/03/2024", "zero", "E", "0.000"],
        ]
        table, report = self.build(
            body, headers=headers, same_amount_field=1, amount_select="Amount",
            deposit_select="", withdrawal_select="", positive_field="Deposit",
        )
        rows = {row[4]: (row[1], row[2]) for row in table[1:]}
        self.assertEqual(rows, {"A": (100.0, 0.0), "B": (0.0, 50.0), "C": (75.0, 0.0), "D": (0.0, 30.0)})
        self.assertEqual(report["skipped_zero"], 1)
        table, _report = self.build(
            body[:2], headers=headers, same_amount_field=1, amount_select="Amount",
            deposit_select="", withdrawal_select="", positive_field="Withdrawal",
        )
        self.assertEqual({row[4]: (row[1], row[2]) for row in table[1:]}, {"A": (0.0, 100.0), "B": (50.0, 0.0)})


class TestDateColumnInBuildTable(BuildTableCase):
    def rows(self, dates):
        return [[text, "x", "R%s" % index, "10.00", ""] for index, text in enumerate(dates)]

    def test_auto_detects_from_all_rows(self):
        dates = ["%02d/03/2024" % day for day in range(1, 12)] * 3 + ["27/03/2024"]
        table, report = self.build(self.rows(dates))
        self.assertEqual(report["date_format"], "d/m/Y")
        self.assertEqual(report["warnings"], [])
        self.assertEqual(table[1][0], date(2024, 3, 1))
        self.assertEqual(table[-1][0], date(2024, 3, 27))

    def test_ambiguous_dates_warn(self):
        table, report = self.build(self.rows(["05/03/2024", "06/04/2024"]))
        self.assertEqual(report["date_format"], "d/m/Y")
        self.assertEqual(len(report["warnings"]), 1)
        self.assertEqual(table[1][0], date(2024, 3, 5))

    def test_explicit_format_that_fails_on_a_row_is_a_row_error(self):
        table, report = self.build(self.rows(["13/03/2024", "03/13/2024"]), date_format="m/d/Y")
        self.assertEqual(len(table) - 1, 1)  # the month-first row only
        self.assertEqual(table[1][0], date(2024, 3, 13))
        self.assertEqual([(e["row"], e["field"]) for e in report["errors"]], [(2, "date")])
        self.assertIn("13/03/2024", report["errors"][0]["message"])

    def test_contradicting_dates_stop_with_a_clear_message(self):
        with self.assertRaises(frappe.ValidationError) as caught:
            self.build(self.rows(["13/03/2024", "03/13/2024"]))
        self.assertIn("mix", str(caught.exception))

    def test_unreadable_date_row_does_not_abort_the_file(self):
        table, report = self.build(self.rows(["13/03/2024", "Total", "14/03/2024"]))
        self.assertEqual(len(table) - 1, 2)
        self.assertEqual([e["row"] for e in report["errors"]], [3])

    def test_legacy_build_table_tells_the_desk_user(self):
        with mock.patch.object(importer, "mark_duplicates") as marking:
            table = importer.build_table(
                self.mapping(), self.HEADERS, self.rows(["13/03/2024", "Total"])
            )
        # the desk table is marked with the shared duplicate rule too
        marking.assert_called_once()
        self.assertEqual(marking.call_args.args[1], "BA-1")
        self.assertEqual(len(table), 2)
        self.msgprint.assert_called_once()
        self.assertIn("Row 3", self.msgprint.call_args[0][0])

    def test_cells_are_text_and_sheet_numbers_lose_the_dot_zero(self):
        body = [["13/03/2024", "  Rent  ", 12345.0, "10.00", ""]]
        table, _report = self.build(body)
        self.assertEqual(table[1][3], "Rent")
        self.assertEqual(table[1][4], "12345")


class TestDuplicateKey(MockedFrappeCase):
    """Fix 3: one duplicate rule for both screens, over every row."""

    def existing(self, *records):
        return [frappe._dict(r) for r in records]

    def run_marking(self, rows, existing, is_card=0):
        with mock.patch("frappe.db", mock.MagicMock()) as db, mock.patch(
            "frappe.get_all", return_value=self.existing(*existing)
        ) as get_all:
            db.get_value.return_value = is_card
            importer.mark_duplicates(rows, "BA-1")
        return get_all

    @staticmethod
    def line(day, amount, ref, description="x", deposit=True):
        return {
            "date": "2024-03-%02d" % day,
            "deposit": amount if deposit else 0,
            "withdrawal": 0 if deposit else amount,
            "reference_number": ref,
            "description": description,
        }

    def test_key_ignores_padding_and_rounding_noise(self):
        self.assertEqual(
            importer.row_key("2024-03-05", 100.0, 0, " R1 ", " Rent "),
            importer.row_key(date(2024, 3, 5), 100.0000001, 0.0, "R1", "Rent"),
        )
        self.assertNotEqual(
            importer.row_key("2024-03-05", 100, 0, "R1", "Rent"),
            importer.row_key("2024-03-05", 100, 0, "R2", "Rent"),
        )

    def test_screen_helpers_are_the_importers_own(self):
        self.assertIs(statement_import._mark_duplicates, importer.mark_duplicates)
        self.assertIs(statement_import._row_key, importer.row_key)

    def test_newest_first_file_flags_the_old_rows_not_only_a_leading_prefix(self):
        # the old desk check stopped at the first new row: nothing below it was flagged
        rows = [
            self.line(14, 30, "N1"),  # new
            self.line(13, 20, "B"),  # already in the system
            self.line(12, 10, "C"),  # already in the system
        ]
        existing = [
            {"date": date(2024, 3, 13), "deposit": 20, "withdrawal": 0, "reference_number": "B", "description": "x"},
            {"date": date(2024, 3, 12), "deposit": 10, "withdrawal": 0, "reference_number": "C", "description": "x"},
        ]
        self.run_marking(rows, existing)
        self.assertEqual([r["duplicate"] for r in rows], [False, True, True])

    def test_identical_lines_in_one_file_are_separate_transactions(self):
        rows = [self.line(5, 500, "", "ATM", deposit=False), self.line(5, 500, "", "ATM", deposit=False)]
        existing = [
            {"date": date(2024, 3, 5), "deposit": 0, "withdrawal": 500, "reference_number": None, "description": "ATM"}
        ]
        self.run_marking(rows, existing)
        self.assertEqual([r["duplicate"] for r in rows], [True, False])

    def test_cancelled_transactions_do_not_count(self):
        rows = [self.line(5, 100, "R1")]
        get_all = self.run_marking(rows, [])
        filters = get_all.call_args.kwargs["filters"]
        self.assertEqual(filters["docstatus"], ["!=", 2])
        self.assertEqual(filters["bank_account"], "BA-1")
        self.assertEqual(filters["date"], ["between", ["2024-03-05", "2024-03-05"]])

    def test_credit_card_rows_are_compared_after_the_flip(self):
        rows = [self.line(5, 100, "R1", deposit=True)]
        stored = [{"date": date(2024, 3, 5), "deposit": 0, "withdrawal": 100, "reference_number": "R1", "description": "x"}]
        self.run_marking(rows, stored, is_card=1)
        self.assertTrue(rows[0]["duplicate"])
        rows = [self.line(5, 100, "R1", deposit=True)]
        self.run_marking(rows, stored, is_card=0)
        self.assertFalse(rows[0]["duplicate"])

    def test_no_dates_no_query(self):
        rows = [{"date": None, "deposit": 1, "withdrawal": 0, "reference_number": None, "description": None}]
        with mock.patch("frappe.get_all") as get_all:
            importer.mark_duplicates(rows, "BA-1")
        get_all.assert_not_called()
        self.assertFalse(rows[0]["duplicate"])


class FakeTransaction:
    created = []

    def __init__(self):
        self.values = {}

    def update(self, values):
        self.values.update(values)

    def insert(self):
        FakeTransaction.created.append(self)

    def submit(self):
        pass


class PublishCase(MockedFrappeCase):
    def setUp(self):
        super().setUp()
        FakeTransaction.created = []
        self.require = self.patch(importer, "require_bank_rec_permission")
        self.assert_account = self.patch(importer, "assert_bank_account_access")
        self.patch(importer, "flip_amount_for_credit_card")
        self.patch(importer.frappe, "has_permission", return_value=True)
        self.patch(importer.frappe, "new_doc", side_effect=lambda doctype: FakeTransaction())
        self.rollback = mock.MagicMock()
        # is_credit_card reads as 0: not a card account
        self.patch(
            importer.frappe,
            "db",
            mock.MagicMock(rollback=self.rollback, get_value=mock.MagicMock(return_value=0)),
        )

    def patch(self, target, name, new=mock.DEFAULT, **kwargs):
        patcher = mock.patch.object(target, name, new, **kwargs)
        patched = patcher.start()
        self.addCleanup(patcher.stop)
        return patched

    @staticmethod
    def dataset(*rows):
        header = ["Date", "Deposit", "Withdrawal", "Description", "Reference Number",
                  "Bank Account", "Currency", "Is Duplicated"]
        return json.dumps([header, *rows])


class TestPublishRecords(PublishCase):
    """Fix 3 and fix 5 (permissions) on the desk importer's publish step."""

    def test_flagged_and_already_imported_rows_are_skipped(self):
        dataset = self.dataset(
            ["2024-03-14", 10, 0, "new", "N1", "BA-1", "BHD", 0],
            ["2024-03-13", 20, 0, "flagged by the browser", "F1", "BA-1", "BHD", 1],
            ["2024-03-12", 30, 0, "unflagged but in the system", "S1", "BA-1", "BHD", 0],
        )
        in_system = [frappe._dict(
            date=date(2024, 3, 12), deposit=30, withdrawal=0, reference_number="S1",
            description="unflagged but in the system",
        )]
        with mock.patch.object(importer.frappe, "get_all", return_value=in_system):
            self.assertTrue(importer.publish_records(dataset))
        self.assertEqual([t.values["reference_number"] for t in FakeTransaction.created], ["N1"])
        self.assertIn("1 transaction(s) imported, 2 duplicate(s) skipped.", self.msgprint.call_args[0][0])

    def test_every_account_in_the_rows_is_checked(self):
        dataset = self.dataset(
            ["2024-03-14", 10, 0, "a", "A", "BA-1", "BHD", 0],
            ["2024-03-14", 10, 0, "b", "B", "BA-2", "BHD", 0],
        )
        with mock.patch.object(importer.frappe, "get_all", return_value=[]):
            importer.publish_records(dataset)
        self.require.assert_called_once_with()
        self.assertEqual([c.args[0] for c in self.assert_account.call_args_list], ["BA-1", "BA-2"])

    def test_no_access_to_the_account_means_nothing_is_inserted(self):
        self.assert_account.side_effect = frappe.PermissionError("no access")
        dataset = self.dataset(["2024-03-14", 10, 0, "a", "A", "BA-9", "BHD", 0])
        with self.assertRaises(frappe.PermissionError):
            importer.publish_records(dataset)
        self.assertEqual(FakeTransaction.created, [])

    def test_missing_role_stops_before_anything_else(self):
        self.require.side_effect = frappe.PermissionError("no role")
        with self.assertRaises(frappe.PermissionError):
            importer.publish_records(self.dataset(["2024-03-14", 10, 0, "a", "A", "BA-1", "BHD", 0]))
        self.assert_account.assert_not_called()

    def test_page_account_must_match_the_rows(self):
        dataset = self.dataset(["2024-03-14", 10, 0, "a", "A", "BA-1", "BHD", 0])
        with self.assertRaises(frappe.ValidationError):
            importer.publish_records(dataset, json.dumps({"bank_account": "BA-OTHER"}))
        self.assertEqual(FakeTransaction.created, [])

    def test_row_without_an_account_is_refused(self):
        with self.assertRaises(frappe.ValidationError):
            importer.publish_records(self.dataset(["2024-03-14", 10, 0, "a", "A", "", "BHD", 0]))

    def test_a_failing_row_rolls_back_and_says_why(self):
        def broken(doctype):
            raise frappe.ValidationError("row is invalid")

        dataset = self.dataset(["2024-03-14", 10, 0, "a", "A", "BA-1", "BHD", 0])
        with mock.patch.object(importer.frappe, "get_all", return_value=[]), mock.patch.object(
            importer.frappe, "new_doc", side_effect=broken
        ):
            self.assertFalse(importer.publish_records(dataset))
        self.rollback.assert_called_once()
        self.assertIn("row is invalid", self.msgprint.call_args[0][0])

    def test_saved_mapping_is_written_for_the_authorised_account_only(self):
        dataset = self.dataset(["2024-03-14", 10, 0, "a", "A", "BA-1", "BHD", 0])
        importer_data = json.dumps(
            {"bank_account": "BA-1", "bank": "EVIL BANK", "save_mapping_for_future_use": 1,
             "date_select": "Date"}
        )
        with mock.patch.object(importer.frappe, "get_all", return_value=[]), mock.patch.object(
            importer, "save_bank_mapping_for_future_use"
        ) as save:
            importer.publish_records(dataset, importer_data)
        saved = save.call_args.args[0]
        self.assertEqual(saved["bank_account"], "BA-1")


class TestBankMappingSave(PublishCase):
    """Fix 5: the Bank is derived from the authorised Bank Account."""

    def setUp(self):
        super().setUp()
        self.bank = mock.MagicMock()
        self.bank.bank_transaction_mapping = []
        self.assert_account.return_value = SimpleNamespace(bank="BANK-A")
        self.get_doc = self.patch(importer.frappe, "get_doc", return_value=self.bank)

    def test_bank_comes_from_the_account_not_from_the_client(self):
        importer.save_bank_mapping_for_future_use(
            {"bank_account": "BA-1", "bank": "BANK-EVIL", "date_select": "Date", "date_format": "d/m/Y",
             "same_amount_field": 0, "deposit_select": "Credit", "withdrawal_select": "Debit"}
        )
        self.assert_account.assert_called_once_with("BA-1")
        self.get_doc.assert_called_once_with("Bank", "BANK-A")
        self.assertEqual(self.bank.bank_statement_date_format, "d/m/Y")
        self.bank.save.assert_called_once_with(ignore_permissions=True)

    def test_no_access_means_no_bank_is_loaded(self):
        self.assert_account.side_effect = frappe.PermissionError("no access")
        with self.assertRaises(frappe.PermissionError):
            importer.save_bank_mapping_for_future_use({"bank_account": "BA-9", "date_format": "m/d/Y"})
        self.get_doc.assert_not_called()
        self.bank.save.assert_not_called()

    def test_only_known_date_formats_are_stored(self):
        self.bank.bank_statement_date_format = "Auto"
        importer.save_bank_mapping_for_future_use({"bank_account": "BA-1", "date_format": "<script>"})
        self.assertEqual(self.bank.bank_statement_date_format, "Auto")

    def test_placeholder_reference_column_is_not_saved(self):
        importer.save_bank_mapping_for_future_use(
            {"bank_account": "BA-1", "date_select": "Date", "reference_number_select": "(none)"}
        )
        fields = [call.args[1]["bank_transaction_field"] for call in self.bank.append.call_args_list]
        self.assertEqual(fields, ["date"])


class TestWhitelistedMethodsAreGuarded(PublishCase):
    """Fix 5: every whitelisted method checks the role and the bank account."""

    def test_form_start_import(self):
        with mock.patch.object(importer, "start_import", return_value={"header": []}) as start:
            importer.form_start_import("/private/files/a.csv", "BA-1")
        self.require.assert_called_once_with()
        self.assert_account.assert_called_once_with("BA-1")
        start.assert_called_once_with("/private/files/a.csv", "BA-1")

    def test_form_start_import_without_access_never_reads_the_file(self):
        self.assert_account.side_effect = frappe.PermissionError("no access")
        with mock.patch.object(importer, "start_import") as start:
            with self.assertRaises(frappe.PermissionError):
                importer.form_start_import("/private/files/payroll.xlsx", "BA-1")
        start.assert_not_called()

    def test_get_last_transaction(self):
        with mock.patch.object(importer.frappe, "get_all", return_value=[{"date": "2024-03-01"}]) as get_all:
            self.assertEqual(importer.get_last_transaction("BA-1"), {"date": "2024-03-01"})
        self.require.assert_called_once_with()
        self.assert_account.assert_called_once_with("BA-1")
        self.assertIn("creation", get_all.call_args.kwargs["order_by"])

    def test_get_last_transaction_without_access_reads_nothing(self):
        self.assert_account.side_effect = frappe.PermissionError("no access")
        with mock.patch.object(importer.frappe, "get_all") as get_all:
            with self.assertRaises(frappe.PermissionError):
                importer.get_last_transaction("BA-9")
        get_all.assert_not_called()

    def test_map_fields(self):
        with mock.patch.object(importer, "build_table", return_value=[["Date"]]) as build:
            result = importer.map_fields(
                json.dumps({"bank_account": "BA-1"}), json.dumps(["Date"]), json.dumps([["1"]])
            )
        self.assertEqual(result, [["Date"]])
        self.require.assert_called_once_with()
        self.assert_account.assert_called_once_with("BA-1")
        build.assert_called_once_with({"bank_account": "BA-1"}, ["Date"], [["1"]])

    def test_map_fields_without_access_probes_nothing(self):
        self.assert_account.side_effect = frappe.PermissionError("no access")
        with mock.patch.object(importer, "build_table") as build:
            with self.assertRaises(frappe.PermissionError):
                importer.map_fields(json.dumps({"bank_account": "BA-9"}), "[]", "[]")
        build.assert_not_called()

    def test_every_whitelisted_method_checks_the_role_first(self):
        self.require.side_effect = frappe.PermissionError("no role")
        calls = (
            lambda: importer.form_start_import("f", "BA-1"),
            lambda: importer.map_fields("{}", "[]", "[]"),
            lambda: importer.get_last_transaction("BA-1"),
            lambda: importer.publish_records("[[]]"),
        )
        for call in calls:
            with self.subTest(call=call):
                with self.assertRaises(frappe.PermissionError):
                    call()
        self.assert_account.assert_not_called()


class TestFileAccess(MockedFrappeCase):
    def test_first_readable_file_wins(self):
        files = {"F1": SimpleNamespace(name="F1"), "F2": SimpleNamespace(name="F2")}
        with mock.patch("frappe.get_all", return_value=["F1", "F2"]), mock.patch(
            "frappe.get_doc", side_effect=lambda doctype, name: files[name]
        ), mock.patch("frappe.has_permission", side_effect=lambda dt, pt, doc: doc.name == "F2"):
            self.assertIs(importer.get_readable_file_doc("/private/files/a.csv"), files["F2"])

    def test_file_nobody_may_read(self):
        with mock.patch("frappe.get_all", return_value=["F1"]), mock.patch(
            "frappe.get_doc", return_value=SimpleNamespace(name="F1")
        ), mock.patch("frappe.has_permission", return_value=False):
            with self.assertRaises(frappe.PermissionError):
                importer.get_readable_file_doc("/private/files/a.csv")

    def test_unknown_file_and_no_file(self):
        with mock.patch("frappe.get_all", return_value=[]):
            with self.assertRaises(frappe.ValidationError):
                importer.get_readable_file_doc("/private/files/none.csv")
        with self.assertRaises(frappe.ValidationError):
            importer.get_readable_file_doc("")


class TestExtensionCase(MockedFrappeCase):
    """Fix 6: .CSV / .XLSX are accepted by start_import and the import screen."""

    def file_doc(self):
        return SimpleNamespace(get_content=lambda: b"content")

    def test_start_import_accepts_upper_case_extensions(self):
        bank_account = SimpleNamespace(bank=None)
        for url, reader in (
            ("/private/files/Statement.CSV", "read_csv_content"),
            ("/private/files/Statement.XLSX", "read_xlsx_file_from_attached_file"),
        ):
            with self.subTest(url=url):
                with mock.patch("frappe.db", mock.MagicMock(exists=mock.MagicMock(return_value=True))), mock.patch(
                    "frappe.get_doc", return_value=bank_account
                ), mock.patch.object(importer, "get_readable_file_doc", return_value=self.file_doc()), mock.patch.object(
                    importer, reader, return_value=[["﻿Date", "Amount"], ["1", "2"]]
                ) as read:
                    result = importer.start_import(url, "BA-1")
                read.assert_called_once()
                self.assertEqual(result["header"], ["Date", "Amount"])  # BOM removed too
                self.assertEqual(result["body"], [["1", "2"]])

    def test_start_import_can_skip_reading_the_file(self):
        with mock.patch("frappe.db", mock.MagicMock(exists=mock.MagicMock(return_value=True))), mock.patch(
            "frappe.get_doc", return_value=SimpleNamespace(bank=None)
        ), mock.patch.object(importer, "get_readable_file_doc") as get_file:
            result = importer.start_import("/private/files/a.CSV", "BA-1", include_rows=False)
        get_file.assert_not_called()
        self.assertEqual(result["header"], [])
        self.assertEqual(result["bank_mapping"]["date_format"], "Auto")

    def test_import_screen_reads_upper_case_and_keeps_file_row_numbers(self):
        rows = [["Date", "Amount"], [None, None], ["13/03/2024", "5"], ["", ""], ["14/03/2024", "6"]]
        with mock.patch.object(
            statement_import, "get_readable_file_doc", return_value=self.file_doc()
        ), mock.patch.object(statement_import, "read_content", return_value=rows) as read:
            headers, body, numbers = statement_import._read_file("/private/files/X.XLSX")
        self.assertEqual(read.call_args.args[1], "xlsx")
        self.assertEqual(headers, ["Date", "Amount"])
        self.assertEqual(len(body), 2)
        self.assertEqual(numbers, [3, 5])


class TestImportScreenDateChoice(MockedFrappeCase):
    """Fix 2 on the Bank Reco import screen: detection beats the saved format."""

    HEADERS = ["Date", "Description", "Debit", "Credit"]

    def parse(self, dates, saved):
        body = [[d, "x", "1.00", ""] for d in dates]
        patches = (
            mock.patch.object(statement_import, "require_bank_rec_permission"),
            mock.patch.object(statement_import, "assert_bank_account_access"),
            mock.patch.object(statement_import, "_read_file", return_value=(self.HEADERS, body, [])),
            mock.patch.object(statement_import, "start_import", return_value={"bank_mapping": saved}),
        )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        return statement_import.parse_statement("/private/files/a.csv", "BA-1")

    def test_ambiguous_without_a_saved_format_warns(self):
        result = self.parse(["05/03/2024", "06/04/2024"], {})
        self.assertEqual(result["mapping"]["date_format"], "d/m/Y")
        self.assertEqual(len(result["warnings"]), 1)

    def test_saved_format_breaks_a_tie_and_then_no_warning(self):
        result = self.parse(["05/03/2024", "06/04/2024"], {"date": "Date", "date_format": "m/d/Y"})
        self.assertEqual(result["mapping"]["date_format"], "m/d/Y")
        self.assertEqual(result["warnings"], [])

    def test_saved_format_never_overrides_conclusive_dates(self):
        result = self.parse(["05/03/2024", "13/03/2024"], {"date": "Date", "date_format": "m/d/Y"})
        self.assertEqual(result["mapping"]["date_format"], "d/m/Y")
        self.assertEqual(result["warnings"], [])

    def test_saved_format_of_another_layout_is_not_used(self):
        result = self.parse(["05/03/2024", "06/04/2024"], {"date": "Date", "date_format": "d-m-Y"})
        self.assertEqual(result["mapping"]["date_format"], "d/m/Y")
        self.assertEqual(len(result["warnings"]), 1)

    def test_contradicting_dates_ask_for_a_manual_choice(self):
        result = self.parse(["13/03/2024", "03/13/2024"], {})
        self.assertEqual(result["mapping"]["date_format"], "Auto")
        self.assertEqual(len(result["warnings"]), 1)

    def test_table_rows_carry_the_file_row_and_the_report(self):
        body = [["13/03/2024", "x", "5.00", ""], ["Total", "", "9.00", ""], ["14/03/2024", "y", "", "7.00"]]
        mapping = {
            "bank_account": "BA-1", "date_select": "Date", "description_select": "Description",
            "reference_number_select": "(none)", "same_amount_field": 0, "deposit_select": "Credit",
            "withdrawal_select": "Debit", "date_format": "Auto",
        }
        headers = self.HEADERS + ["(none)"]
        body = [row + [""] for row in body]

        def get_doc(doctype, name=None):
            if doctype == "Bank Account":
                return SimpleNamespace(name="BA-1", account="ACC-1")
            return SimpleNamespace(account_currency="BHD")

        with mock.patch("frappe.get_doc", get_doc):
            rows, report = statement_import._table_rows(headers, body, mapping, [2, 3, 4])
        self.assertEqual([(r["source_row"], r["date"], r["withdrawal"], r["deposit"]) for r in rows],
                         [(2, "2024-03-13", 5.0, 0.0), (4, "2024-03-14", 0.0, 7.0)])
        self.assertEqual([(e["row"], e["field"]) for e in report["errors"]], [(3, "date")])


if __name__ == "__main__":
    unittest.main()
