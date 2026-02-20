frappe.query_reports["Batch Cost Report"] = {
	filters: [
		{
			fieldname: "crop_cycle",
			label: __("Crop Cycle"),
			fieldtype: "Link",
			options: "Crop Cycle",
		},
		{
			fieldname: "crop",
			label: __("Crop"),
			fieldtype: "Link",
			options: "Crop",
		},
		{
			fieldname: "current_stage",
			label: __("Current Stage"),
			fieldtype: "Select",
			options: [
				"",
				"Sapling (0 cm)",
				"Step 1 (5 cm)",
				"Step 2 (20 cm)",
				"Step 3 (35 cm)",
				"Ready to Sell",
			],
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
		},
	],
};