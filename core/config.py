import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("zenie-ai")

DUMMY_FIELDS = {
    "customer_name":{"type": "text"},
    "customer_uuid":{"type": "text"},
    "invoice_number":{"type": "text"},
    "posting_date":{"type": "date"},
    "status":{"type": "text"}, # status : draft or posted or cancelled only
    "subtotal":{"type": "number"},
    "total_amount":{"type": "number"},
    "product":
    {   
        "product_name":{"type": "text"},
        "product_uuid":{"type": "text"},
        "ledger_name":{"type": "text"},
        "ledger_uuid":{"type": "text"},
        "quantity":{"type": "number"},
        "amount":{"type": "number"},
        "total_amount":{"type": "number"},
    },
}

DUMMY_APIS = {
    "create_invoice":{
        "customer_name":{"type": "text"},
        "customer_uuid":{"type": "text"},
        "invoice_number":{"type": "text"},
        "posting_date":{"type": "date"},
        "status":{"type": "text"}, # status : draft or posted or cancelled only
        "subtotal":{"type": "number"},
        "total_amount":{"type": "number"},
        "product":
        {   
            "product_name":{"type": "text"},
            "product_uuid":{"type": "text"},
            "ledger_name":{"type": "text"},
            "ledger_uuid":{"type": "text"},
            "quantity":{"type": "number"},
            "amount":{"type": "number"},
            "total_amount":{"type": "number"},
        },
    }
}