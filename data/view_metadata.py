"""
view_metadata.py — Column metadata for database views used by the SQL generator.

Add a new entry here whenever a new view is mapped in Intent_file.xlsx.

Structure per view:
  "view_name": {
      "date_column": "<column used for date range filtering>",
      "columns": [
          {"name": "<col>", "type": "<SQL type>", "description": "<plain English>"},
          ...
      ]
  }
"""

VIEW_METADATA = {
    "vw_ai_sales_invoice": {
        "date_column": "invoice_date",
        "columns": [
            {"name": "company_id",     "type": "INT",     "description": "Company identifier"},
            {"name": "invoice_date",   "type": "DATE",    "description": "Date of invoice"},
            {"name": "customer_name",  "type": "VARCHAR", "description": "Full name of the customer"},
            {"name": "product_name",   "type": "VARCHAR", "description": "Product purchased"},
            {"name": "region",         "type": "VARCHAR", "description": "Geographic region"},
            {"name": "total_amount",   "type": "DECIMAL", "description": "Total invoice revenue"},
            {"name": "invoice_number", "type": "VARCHAR", "description": "Unique invoice reference"},
        ],
    },
    "vw_ai_sales_invoice_lines": {
        "date_column": "invoice_date",
        "columns": [
            {"name": "company_id",    "type": "INT",     "description": "Company identifier"},
            {"name": "invoice_date",  "type": "DATE",    "description": "Date of invoice"},
            {"name": "product_name",  "type": "VARCHAR", "description": "Product name on line item"},
            {"name": "customer_name", "type": "VARCHAR", "description": "Customer who purchased"},
            {"name": "line_amount",   "type": "DECIMAL", "description": "Revenue for this line item"},
            {"name": "quantity",      "type": "INT",     "description": "Units sold"},
            {"name": "unit_price",    "type": "DECIMAL", "description": "Price per unit"},
        ],
    },
}
