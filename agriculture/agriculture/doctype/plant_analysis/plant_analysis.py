# Copyright (c) 2017, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import date_diff, today


def get_stage_from_height(height):
	"""Return growth stage label based on measured plant height."""
	if height is None:
		return None
	if height >= 35:
		return "Step 3 (35 cm)"
	elif height >= 20:
		return "Step 2 (20 cm)"
	elif height >= 5:
		return "Step 1 (5 cm)"
	else:
		return "Sapling (0 cm)"


class PlantAnalysis(Document):
	def validate(self):
		self.set_growth_stage_from_height()

	def on_update(self):
		self.update_crop_cycle()

	def set_growth_stage_from_height(self):
		"""Auto-set growth_stage_reached based on plant_height."""
		if self.plant_height is not None:
			self.growth_stage_reached = get_stage_from_height(self.plant_height)

	def update_crop_cycle(self):
		"""Push current stage, avg height, and days_since_start back to the Crop Cycle."""
		if not self.crop_cycle:
			return

		crop_cycle = frappe.get_doc("Crop Cycle", self.crop_cycle)

		# Calculate days since start
		days = 0
		if crop_cycle.start_date:
			measurement_date = (
				self.collection_datetime.date()
				if self.collection_datetime
				else frappe.utils.getdate(today())
			)
			days = date_diff(measurement_date, crop_cycle.start_date)

		# Only update stage if this measurement shows progression
		# (don't allow going backwards)
		stage_order = [
			"Sapling (0 cm)",
			"Step 1 (5 cm)",
			"Step 2 (20 cm)",
			"Step 3 (35 cm)",
			"Ready to Sell",
		]

		new_stage = self.growth_stage_reached
		current_stage = crop_cycle.current_stage or ""

		current_idx = stage_order.index(current_stage) if current_stage in stage_order else -1
		new_idx = stage_order.index(new_stage) if new_stage in stage_order else -1

		update_fields = {
			"avg_plant_height": self.plant_height,
			"days_since_start": days,
		}

		# Only advance stage, never go backward
		if new_idx > current_idx:
			update_fields["current_stage"] = new_stage

		frappe.db.set_value("Crop Cycle", self.crop_cycle, update_fields)
		frappe.msgprint(
			f"Crop Cycle <b>{self.crop_cycle}</b> updated — "
			f"Height: {self.plant_height} cm | Stage: {new_stage} | Day: {days}",
			indicator="green",
			alert=True,
		)

	@frappe.whitelist()
	def load_contents(self):
		docs = frappe.get_all(
			"Agriculture Analysis Criteria",
			filters={"linked_doctype": "Plant Analysis"},
		)
		for doc in docs:
			self.append("plant_analysis_criteria", {"title": str(doc.name)})