"""
Batch Cost Report
=================
Combines stock valuation + Journal Entry costs to show
true cost per plant for each Crop Cycle.

Columns:
- Crop Cycle
- Crop
- Warehouse (Start)
- Current Warehouse
- Start Date
- Current Stage
- Estimated Qty
- Surviving Plants
- Mortality %
- Stock Material Cost (from Stock Ledger)
- Labor & Overhead Cost (from GL - Journal Entries)
- Fixed Cost Allocated (from GL - Cost Center)
- Total Cost
- Cost per Plant (Stock only)
- True Cost per Plant (Stock + JE)
- Selling Price per Plant
- Total Revenue
- Gross Profit
- Gross Margin %
"""

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns():
	return [
		{
			"label": _("Crop Cycle"),
			"fieldname": "crop_cycle",
			"fieldtype": "Link",
			"options": "Crop Cycle",
			"width": 220,
		},
		{
			"label": _("Crop"),
			"fieldname": "crop",
			"fieldtype": "Data",
			"width": 150,
		},
		{
			"label": _("Start Date"),
			"fieldname": "start_date",
			"fieldtype": "Date",
			"width": 100,
		},
		{
			"label": _("Current Stage"),
			"fieldname": "current_stage",
			"fieldtype": "Data",
			"width": 130,
		},
		{
			"label": _("Current Warehouse"),
			"fieldname": "current_warehouse",
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 150,
		},
		{
			"label": _("Estimated Qty"),
			"fieldname": "estimated_quantity",
			"fieldtype": "Int",
			"width": 100,
		},
		{
			"label": _("Surviving Plants"),
			"fieldname": "surviving_plants",
			"fieldtype": "Int",
			"width": 110,
		},
		{
			"label": _("Mortality %"),
			"fieldname": "mortality_percentage",
			"fieldtype": "Percent",
			"width": 90,
		},
		{
			"label": _("Material Cost (SAR)"),
			"fieldname": "material_cost",
			"fieldtype": "Currency",
			"width": 140,
		},
		{
			"label": _("Labor & Overhead (SAR)"),
			"fieldname": "labor_cost",
			"fieldtype": "Currency",
			"width": 160,
		},
		{
			"label": _("Fixed Cost Allocated (SAR)"),
			"fieldname": "fixed_cost",
			"fieldtype": "Currency",
			"width": 160,
		},
		{
			"label": _("Total Cost (SAR)"),
			"fieldname": "total_cost",
			"fieldtype": "Currency",
			"width": 130,
		},
		{
			"label": _("Stock Cost / Plant (SAR)"),
			"fieldname": "stock_cost_per_plant",
			"fieldtype": "Currency",
			"width": 150,
		},
		{
			"label": _("True Cost / Plant (SAR)"),
			"fieldname": "true_cost_per_plant",
			"fieldtype": "Currency",
			"width": 150,
		},
		{
			"label": _("Revenue (SAR)"),
			"fieldname": "total_revenue",
			"fieldtype": "Currency",
			"width": 130,
		},
		{
			"label": _("Gross Profit (SAR)"),
			"fieldname": "gross_profit",
			"fieldtype": "Currency",
			"width": 130,
		},
		{
			"label": _("Gross Margin %"),
			"fieldname": "gross_margin",
			"fieldtype": "Percent",
			"width": 110,
		},
	]


def get_data(filters):
	filters = filters or {}

	# Build crop cycle filter
	cc_filters = {}
	if filters.get("crop_cycle"):
		cc_filters["name"] = filters.get("crop_cycle")
	if filters.get("crop"):
		cc_filters["crop"] = filters.get("crop")
	if filters.get("current_stage"):
		cc_filters["current_stage"] = filters.get("current_stage")
	if filters.get("from_date"):
		cc_filters["start_date"] = [">=", filters.get("from_date")]
	if filters.get("to_date"):
		cc_filters["start_date"] = ["<=", filters.get("to_date")]

	crop_cycles = frappe.get_all(
		"Crop Cycle",
		filters=cc_filters,
		fields=[
			"name", "crop", "start_date", "current_stage",
			"warehouse", "current_warehouse", "cost_center",
			"estimated_quantity", "surviving_plants",
			"mortality_percentage", "project",
		],
		order_by="start_date desc",
	)

	data = []

	for cc in crop_cycles:
		row = frappe._dict(cc)
		row.crop_cycle = cc.name

		# 1. Material Cost — from Stock Ledger Entry
		# Sum all incoming stock value minus outgoing (COGS already deducted on sale)
		material_cost = get_material_cost(cc)
		row.material_cost = flt(material_cost, 2)

		# 2. Labor & Variable Overhead — from GL entries on Variable Cost accounts
		labor_cost = get_je_cost(cc, cost_type="variable")
		row.labor_cost = flt(labor_cost, 2)

		# 3. Fixed Cost — from GL entries on Fixed Cost accounts
		fixed_cost = get_je_cost(cc, cost_type="fixed")
		row.fixed_cost = flt(fixed_cost, 2)

		# 4. Total Cost
		row.total_cost = flt(row.material_cost + row.labor_cost + row.fixed_cost, 2)

		# 5. Stock Cost per Plant (warehouse valuation only)
		surviving = flt(cc.surviving_plants or cc.estimated_quantity or 1)
		stock_val = get_current_stock_value(cc)
		row.stock_cost_per_plant = flt(stock_val / surviving, 4) if surviving else 0

		# 6. True Cost per Plant (all costs combined)
		row.true_cost_per_plant = flt(row.total_cost / surviving, 4) if surviving else 0

		# 7. Revenue — from Sales Invoice / Delivery Note GL entries
		revenue = get_revenue(cc)
		row.total_revenue = flt(revenue, 2)

		# 8. Gross Profit & Margin
		row.gross_profit = flt(row.total_revenue - row.total_cost, 2)
		row.gross_margin = (
			flt((row.gross_profit / row.total_revenue) * 100, 2)
			if row.total_revenue
			else 0
		)

		data.append(row)

	return data


def get_material_cost(cc):
	"""
	Get total material cost from Stock Ledger Entry.
	Sum of all incoming stock value posted to the crop's warehouses.
	Excludes transfers between blocks (those are internal movements).
	"""
	if not cc.project:
		return 0

	# Get all stock entries linked to this project
	stock_entries = frappe.get_all(
		"Stock Entry",
		filters={
			"project": cc.project,
			"docstatus": 1,
			"stock_entry_type": ["in", ["Material Receipt", "Material Issue"]],
		},
		fields=["name", "stock_entry_type"],
	)

	total = 0
	for se in stock_entries:
		items = frappe.get_all(
			"Stock Entry Detail",
			filters={"parent": se.name},
			fields=["amount", "basic_amount"],
		)
		for item in items:
			total += flt(item.basic_amount or item.amount)

	return total


def get_je_cost(cc, cost_type="variable"):
	"""
	Get Journal Entry costs from GL entries.
	cost_type = 'variable' → Variable Cost accounts
	cost_type = 'fixed' → Fixed Cost accounts
	"""
	if not cc.cost_center:
		return 0

	if cost_type == "variable":
		account_filter = ["like", "Variable Cost%"]
	else:
		account_filter = ["like", "%Fixed Cost%"]

	# Get matching accounts
	accounts = frappe.get_all(
		"Account",
		filters={
			"account_name": account_filter,
			"is_group": 0,
		},
		pluck="name",
	)

	if not accounts:
		return 0

	total = 0
	for account in accounts:
		result = frappe.db.get_value(
			"GL Entry",
			filters={
				"account": account,
				"cost_center": cc.cost_center,
				"is_cancelled": 0,
				"voucher_type": "Journal Entry",
			},
			fieldname="sum(debit) - sum(credit)",
		)
		total += flt(result)

	return total


def get_current_stock_value(cc):
	"""Get current stock value from Bin for the current warehouse."""
	warehouse = cc.current_warehouse or cc.warehouse
	if not warehouse:
		return 0

	# Find the plant item in this warehouse
	bin_data = frappe.db.get_value(
		"Bin",
		{"warehouse": warehouse},
		["actual_qty", "valuation_rate"],
		as_dict=True,
	)

	if not bin_data:
		return 0

	return flt(bin_data.actual_qty) * flt(bin_data.valuation_rate)


def get_revenue(cc):
	"""Get total sales revenue from GL entries linked to this project."""
	if not cc.project:
		return 0

	# Get sales accounts
	sales_accounts = frappe.get_all(
		"Account",
		filters={
			"account_type": "Income Account",
			"is_group": 0,
		},
		pluck="name",
	)

	if not sales_accounts:
		return 0

	# Get delivery notes linked to this project
	delivery_notes = frappe.get_all(
		"Delivery Note",
		filters={"project": cc.project, "docstatus": 1},
		pluck="name",
	)

	sales_invoices = frappe.get_all(
		"Sales Invoice",
		filters={"project": cc.project, "docstatus": 1},
		pluck="name",
	)

	total = 0

	for dn in delivery_notes:
		result = frappe.db.get_value(
			"GL Entry",
			filters={
				"voucher_no": dn,
				"account": ["in", sales_accounts],
				"is_cancelled": 0,
			},
			fieldname="sum(credit) - sum(debit)",
		)
		total += flt(result)

	for si in sales_invoices:
		result = frappe.db.get_value(
			"GL Entry",
			filters={
				"voucher_no": si,
				"account": ["in", sales_accounts],
				"is_cancelled": 0,
			},
			fieldname="sum(credit) - sum(debit)",
		)
		total += flt(result)

	return total