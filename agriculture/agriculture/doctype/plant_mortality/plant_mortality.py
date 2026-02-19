# Copyright (c) 2024, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import flt, nowdate


class PlantMortality(Document):

	def validate(self):
		self.fetch_stock_details()
		self.calculate_mortality_figures()

	def on_submit(self):
		self.create_stock_reconciliation()
		self.create_mortality_journal_entry()
		self.update_crop_cycle()

	def on_cancel(self):
		self.cancel_linked_documents()
		self.reverse_crop_cycle_update()

	# ------------------------------------------------------------------
	# VALIDATION HELPERS
	# ------------------------------------------------------------------

	def fetch_stock_details(self):
		"""Fetch current qty and valuation rate from the warehouse."""
		if not self.warehouse or not self.crop_cycle:
			return

		crop_cycle = frappe.get_doc("Crop Cycle", self.crop_cycle)
		item_code = self.get_plant_item(crop_cycle)

		if not item_code:
			frappe.throw("Could not determine plant item from Crop Cycle. "
						 "Make sure stock exists in the selected warehouse.")

		stock = frappe.db.get_value(
			"Bin",
			{"item_code": item_code, "warehouse": self.warehouse},
			["actual_qty", "valuation_rate"],
			as_dict=True,
		)

		if not stock:
			frappe.throw(f"No stock found for item <b>{item_code}</b> "
						 f"in warehouse <b>{self.warehouse}</b>.")

		if flt(stock.actual_qty) <= 0:
			frappe.throw(f"Stock qty in <b>{self.warehouse}</b> is zero or negative. "
						 "Cannot record mortality.")

		if self.dead_plant_qty >= flt(stock.actual_qty):
			frappe.throw(
				f"Dead plant qty ({self.dead_plant_qty}) cannot be equal to or "
				f"greater than current stock ({flt(stock.actual_qty)})."
			)

		self.current_valuation_rate = flt(stock.valuation_rate, 4)
		self._current_qty = flt(stock.actual_qty)
		self._item_code = item_code

	def calculate_mortality_figures(self):
		"""Calculate surviving qty, absorbed cost and new rate."""
		if not self.current_valuation_rate or not self.dead_plant_qty:
			return

		current_qty = getattr(self, "_current_qty", 0)
		if not current_qty:
			# Re-fetch if coming from a reload
			stock = frappe.db.get_value(
				"Bin",
				{"item_code": self._get_item_code(), "warehouse": self.warehouse},
				["actual_qty", "valuation_rate"],
				as_dict=True,
			) or {}
			current_qty = flt(stock.get("actual_qty", 0))

		total_value = flt(self.current_valuation_rate) * current_qty
		dead_cost = flt(self.current_valuation_rate) * self.dead_plant_qty

		self.surviving_qty = int(current_qty - self.dead_plant_qty)
		self.total_cost_absorbed = flt(dead_cost, 2)

		if self.surviving_qty > 0:
			# Full total value stays with survivors
			self.new_rate_after_absorption = flt(total_value / self.surviving_qty, 4)
		else:
			self.new_rate_after_absorption = 0

	def get_plant_item(self, crop_cycle=None):
		"""Get the plant item code from stock in the warehouse."""
		if hasattr(self, "_item_code") and self._item_code:
			return self._item_code

		if not crop_cycle:
			crop_cycle = frappe.get_doc("Crop Cycle", self.crop_cycle)

		# Find item with stock in this warehouse from Bin
		bins = frappe.get_all(
			"Bin",
			filters={"warehouse": self.warehouse, "actual_qty": [">", 0]},
			fields=["item_code", "actual_qty"],
			order_by="actual_qty desc",
			limit=1,
		)

		if bins:
			return bins[0].item_code

		return None

	def _get_item_code(self):
		if hasattr(self, "_item_code") and self._item_code:
			return self._item_code
		return self.get_plant_item()

	# ------------------------------------------------------------------
	# STOCK RECONCILIATION
	# ------------------------------------------------------------------

	def create_stock_reconciliation(self):
		"""
		Create a Stock Reconciliation that:
		- Reduces qty to surviving_qty
		- Sets valuation rate to new_rate_after_absorption
		- This keeps the FULL inventory value with survivors
		"""
		item_code = self._get_item_code()
		if not item_code:
			frappe.throw("Could not determine plant item for Stock Reconciliation.")

		sr = frappe.get_doc({
			"doctype": "Stock Reconciliation",
			"purpose": "Stock Reconciliation",
			"posting_date": self.posting_date,
			"posting_time": "00:00:01",
			"company": frappe.defaults.get_user_default("Company"),
			"remarks": (
				f"Mortality absorption – {self.dead_plant_qty} dead plants – "
				f"{self.crop_cycle} – {self.stage_at_mortality}"
			),
			"items": [
				{
					"item_code": item_code,
					"warehouse": self.warehouse,
					"qty": self.surviving_qty,
					"valuation_rate": self.new_rate_after_absorption,
				}
			],
		})

		sr.insert(ignore_permissions=True)
		sr.submit()

		self.db_set("stock_reconciliation", sr.name)
		frappe.msgprint(
			f"Stock Reconciliation <b>{sr.name}</b> created — "
			f"Qty set to {self.surviving_qty} @ {self.new_rate_after_absorption} SAR",
			indicator="green",
			alert=True,
		)

	# ------------------------------------------------------------------
	# JOURNAL ENTRY (informational mortality tracking)
	# ------------------------------------------------------------------

	def create_mortality_journal_entry(self):
		"""
		Journal Entry to record mortality cost in P&L for reporting.
		Debit: Plant Mortality / Crop Loss
		Credit: Stock Adjustment (to offset the reconciliation value change)
		"""
		crop_cycle_doc = frappe.get_doc("Crop Cycle", self.crop_cycle)
		cost_center = crop_cycle_doc.cost_center

		mortality_account = frappe.db.get_value(
			"Account",
			{"account_name": ["like", "Plant Mortality%"], "is_group": 0},
			"name",
		)

		if not mortality_account:
			frappe.msgprint(
				"Plant Mortality account not found — skipping Journal Entry. "
				"Please create 'Plant Mortality / Crop Loss' account in Chart of Accounts.",
				indicator="orange",
			)
			return

		# Stock adjustment account
		stock_adj_account = frappe.db.get_value(
			"Account",
			{"account_type": "Stock Adjustment", "is_group": 0,
			 "company": frappe.defaults.get_user_default("Company")},
			"name",
		)

		if not stock_adj_account:
			# Fallback to temporary account
			stock_adj_account = frappe.db.get_value(
				"Account",
				{"account_name": ["like", "%Stock Adjustment%"], "is_group": 0},
				"name",
			)

		if not stock_adj_account:
			frappe.msgprint(
				"Stock Adjustment account not found — skipping Journal Entry.",
				indicator="orange",
			)
			return

		je = frappe.get_doc({
			"doctype": "Journal Entry",
			"voucher_type": "Journal Entry",
			"posting_date": self.posting_date,
			"company": frappe.defaults.get_user_default("Company"),
			"remark": (
				f"Plant mortality – {self.dead_plant_qty} plants – "
				f"{self.crop_cycle} – {self.stage_at_mortality}"
			),
			"accounts": [
				{
					"account": mortality_account,
					"debit_in_account_currency": self.total_cost_absorbed,
					"credit_in_account_currency": 0,
					"cost_center": cost_center,
					"project": crop_cycle_doc.project,
				},
				{
					"account": stock_adj_account,
					"debit_in_account_currency": 0,
					"credit_in_account_currency": self.total_cost_absorbed,
					"cost_center": cost_center,
					"project": crop_cycle_doc.project,
				},
			],
		})

		je.insert(ignore_permissions=True)
		je.submit()

		self.db_set("mortality_journal_entry", je.name)
		frappe.msgprint(
			f"Mortality Journal Entry <b>{je.name}</b> posted — "
			f"{self.total_cost_absorbed} SAR to Plant Mortality account",
			indicator="green",
			alert=True,
		)

	# ------------------------------------------------------------------
	# CROP CYCLE UPDATE
	# ------------------------------------------------------------------

	def update_crop_cycle(self):
		"""Update Crop Cycle with cumulative mortality figures."""
		crop_cycle = frappe.get_doc("Crop Cycle", self.crop_cycle)

		current_dead = flt(crop_cycle.get("total_dead_plants") or 0)
		current_initial = flt(crop_cycle.get("estimated_quantity") or 0)

		new_total_dead = current_dead + self.dead_plant_qty
		mortality_pct = 0
		if current_initial > 0:
			mortality_pct = flt((new_total_dead / current_initial) * 100, 2)

		frappe.db.set_value("Crop Cycle", self.crop_cycle, {
			"total_dead_plants": int(new_total_dead),
			"mortality_percentage": mortality_pct,
			"surviving_plants": int(current_initial - new_total_dead),
		})

		frappe.msgprint(
			f"Crop Cycle updated — Total dead: {int(new_total_dead)} | "
			f"Mortality: {mortality_pct}% | Survivors: {int(current_initial - new_total_dead)}",
			indicator="blue",
			alert=True,
		)

	# ------------------------------------------------------------------
	# CANCEL HANDLERS
	# ------------------------------------------------------------------

	def cancel_linked_documents(self):
		"""Cancel Stock Reconciliation and Journal Entry on cancel."""
		if self.stock_reconciliation:
			sr = frappe.get_doc("Stock Reconciliation", self.stock_reconciliation)
			if sr.docstatus == 1:
				sr.cancel()

		if self.mortality_journal_entry:
			je = frappe.get_doc("Journal Entry", self.mortality_journal_entry)
			if je.docstatus == 1:
				je.cancel()

	def reverse_crop_cycle_update(self):
		"""Reverse mortality figures on Crop Cycle when cancelled."""
		crop_cycle = frappe.get_doc("Crop Cycle", self.crop_cycle)

		current_dead = flt(crop_cycle.get("total_dead_plants") or 0)
		current_initial = flt(crop_cycle.get("estimated_quantity") or 0)

		new_total_dead = max(0, current_dead - self.dead_plant_qty)
		mortality_pct = 0
		if current_initial > 0:
			mortality_pct = flt((new_total_dead / current_initial) * 100, 2)

		frappe.db.set_value("Crop Cycle", self.crop_cycle, {
			"total_dead_plants": int(new_total_dead),
			"mortality_percentage": mortality_pct,
			"surviving_plants": int(current_initial - new_total_dead),
		})