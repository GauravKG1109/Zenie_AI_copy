"""
view_metadata.py — Column metadata for database views used by the SQL generator.

Add a new entry here whenever a new view is mapped in Intent_file.xlsx.

Structure per view:
  "view_name": {
      "description": "<what this view represents>",
      "date_column":  "<column used for date range filtering>",
      "join_keys":    ["<col1>", "<col2>"],   # columns shared with other views for JOINs
      "columns": [
          {"name": "<col>", "type": "<SQL type>", "description": "<plain English>"},
          ...
      ]
  }
"""

VIEW_METADATA = {
    "vw_ai_sales_invoice": {
        "description": (
            "Invoice-level fact view. One row per invoice. "
            "Contains financial amounts, customer info, status, and payment tracking. "
            "Use for revenue analysis, outstanding balances, payment tracking, and aging reports."
        ),
        "date_column": "invoice_date",
        "join_keys": ["invoice_number", "company_uuid"],
        "columns": [
            {"name": "company_uuid", "type": "UUID", "description": "Company identifier — always filter by this"},
            {"name": "invoice_number", "type": "VARCHAR", "description": "Unique invoice ID — primary join key"},
            {"name": "customer_name", "type": "VARCHAR", "description": "Customer name for grouping and filtering"},

            {"name": "invoice_date", "type": "TIMESTAMP", "description": "Invoice creation date — primary time dimension"},
            {"name": "due_date", "type": "TIMESTAMP", "description": "Payment due date — used for aging analysis"},
            {"name": "posting_date", "type": "TIMESTAMP", "description": "Accounting posting date"},

            {"name": "subtotal", "type": "DECIMAL", "description": "Amount before taxes, discounts, and charges"},
            {"name": "discount", "type": "DECIMAL", "description": "Total discount applied on invoice"},
            {"name": "sales_tax", "type": "DECIMAL", "description": "Total tax applied"},
            {"name": "shipping_amount", "type": "DECIMAL", "description": "Shipping charges"},
            {"name": "rounding_off", "type": "DECIMAL", "description": "Rounding adjustment"},
            {"name": "total_amount", "type": "DECIMAL", "description": "Final invoice amount after all adjustments"},

            {"name": "allocated_amount", "type": "DECIMAL", "description": "Amount allocated/settled against invoice"},
            {"name": "payments", "type": "DECIMAL", "description": "Total payments received"},
            {"name": "outstanding_amount", "type": "DECIMAL", "description": "Unpaid amount remaining"},
            {"name": "balance_due", "type": "DECIMAL", "description": "Final payable amount after adjustments"},
            {"name": "deposit", "type": "DECIMAL", "description": "Advance or deposit applied"},

            {"name": "status", "type": "ENUM", "description": "Invoice lifecycle status (e.g., POSTED)"},
            {"name": "state", "type": "ENUM", "description": "Payment state (e.g., PAID, UNPAID)"},

            {"name": "ship_via", "type": "VARCHAR", "description": "Shipping method"},
            {"name": "ship_to", "type": "VARCHAR", "description": "Shipping destination"},
            {"name": "shipping_date", "type": "DATE", "description": "Date of shipment"},
            {"name": "tracking_number", "type": "VARCHAR", "description": "Shipment tracking reference"},

            {"name": "notes", "type": "TEXT", "description": "Free-text notes on invoice"},
            {"name": "created_by_name", "type": "VARCHAR", "description": "User who created the invoice"},
            {"name": "updated_by_name", "type": "VARCHAR", "description": "Last user who updated the invoice"},

            {"name": "tags", "type": "JSONB", "description": "Structured tags for categorization"},
            {"name": "bank_details", "type": "JSONB", "description": "Bank/payment metadata"}
        ],
    },
    "vw_ai_sales_invoice_lines": {
        "description": (
            "Invoice line-level fact view. Multiple rows per invoice (one per product/service/ledger entry). "
            "Contains quantity, rate, and line-level financials. "
            "Use for product-level analysis, revenue breakdowns, and detailed drilldowns."
        ),
        "date_column": None,
        "join_keys": ["invoice_number", "company_uuid"],
        "columns": [
            {"name": "company_uuid", "type": "UUID", "description": "Company identifier — always filter by this"},
            {"name": "invoice_number", "type": "VARCHAR", "description": "Invoice ID — join to invoice header"},
            {"name": "customer_name", "type": "VARCHAR", "description": "Customer name for grouping"},

            {"name": "product_service_name", "type": "VARCHAR", "description": "Product or service name — primary dimension for item-level analysis"},
            {"name": "ledger_name", "type": "VARCHAR", "description": "Accounting ledger associated with this line"},

            {"name": "quantity", "type": "DECIMAL", "description": "Units sold"},
            {"name": "rate", "type": "DECIMAL", "description": "Price per unit"},
            {"name": "amount", "type": "DECIMAL", "description": "Line total before tax/discount (quantity × rate)"},

            {"name": "discount", "type": "DECIMAL", "description": "Discount applied at line level"},
            {"name": "sales_tax", "type": "DECIMAL", "description": "Tax applied at line level"},

            {"name": "discount_ledger_name", "type": "VARCHAR", "description": "Ledger for discount"},
            {"name": "sales_tax_ledger_name", "type": "VARCHAR", "description": "Ledger for tax"},

            {"name": "unit_name", "type": "VARCHAR", "description": "Unit of measurement (e.g., pcs, hours)"},
            {"name": "tag_name", "type": "VARCHAR", "description": "Tag/category for classification"},

            {"name": "line_description", "type": "VARCHAR", "description": "Additional details for the line item"},
            {"name": "created_by_name", "type": "VARCHAR", "description": "User who created the line"},
            {"name": "updated_by_name", "type": "VARCHAR", "description": "Last user who updated the line"}
        ],
    }
}
