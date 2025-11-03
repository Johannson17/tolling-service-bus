# schemas.py
ENVELOPE = {
    "type": "object",
    "required": ["event", "version", "data", "meta"],
    "properties": {
        "event": {"type": "string"},
        "version": {"type": "string"},
        "data": {"type": "object"},
        "meta": {
            "type": "object",
            "required": ["occurred_at", "producer"],
            "properties": {
                "occurred_at": {"type": "string", "format": "date-time"},
                "producer": {"type": "string"},
                "correlation_id": {"type": "string"},
                "causation_id": {"type": "string"}
            },
            "additionalProperties": True
        }
    },
    "additionalProperties": False
}

SCHEMAS = {
    # Operación (M3)
    "transit.recorded": {
        "type": "object",
        "required": ["transit_id","toll_id","toll_name","lane","vehicle_id","vehicle_type","timestamp"],
        "properties": {
            "transit_id":{"type":"string"},
            "toll_id":{"type":"string"},
            "toll_name":{"type":"string"},
            "lane":{"type":"string"},
            "vehicle_id":{"type":"string"},
            "vehicle_type":{"type":"string"},
            "timestamp":{"type":"string","format":"date-time"},
            "details":{"type":"string"}
        },
        "additionalProperties": False
    },
    "plate.captured": {
        "type":"object",
        "required":["toll_id","lane","image_id","plate","confidence","timestamp"],
        "properties":{
            "toll_id":{"type":"string"},
            "lane":{"type":"string"},
            "image_id":{"type":"string"},
            "plate":{"type":"string"},
            "confidence":{"type":"number"},
            "timestamp":{"type":"string","format":"date-time"}
        },
        "additionalProperties": False
    },
    "toll.status.updated": {
        "type":"object",
        "required":["toll_id","toll_name","open_lanes","closed_lanes","timestamp"],
        "properties":{
            "toll_id":{"type":"string"},
            "toll_name":{"type":"string"},
            "open_lanes":{"type":"integer"},
            "closed_lanes":{"type":"integer"},
            "timestamp":{"type":"string","format":"date-time"},
            "alerts":{
                "type":"array",
                "items":{
                    "type":"object",
                    "required":["type","time"],
                    "properties":{
                        "type":{"type":"string"},
                        "lane":{"type":["string","null"]},
                        "time":{"type":"string","format":"date-time"}
                    }
                }
            }
        },
        "additionalProperties": False
    },

    # Tarifas / categorías (M2)
    "rate.updated": {
        "type":"object",
        "required":["rate_id","category_id","peak_price","offpeak_price","valid_from","valid_to"],
        "properties":{
            "rate_id":{"type":"string"},
            "category_id":{"type":"string"},
            "peak_price":{"type":"number"},
            "offpeak_price":{"type":"number"},
            "valid_from":{"type":"string","format":"date"},
            "valid_to":{"type":"string","format":"date"}
        },
        "additionalProperties": False
    },
    "vehicle.category.changed": {
        "type":"object",
        "required":["vehicle_id","old_category_id","new_category_id","timestamp"],
        "properties":{
            "vehicle_id":{"type":"string"},
            "old_category_id":{"type":"string"},
            "new_category_id":{"type":"string"},
            "timestamp":{"type":"string","format":"date-time"}
        },
        "additionalProperties": False
    },

    # Pagos / prepago / deuda (M4)
    "payment.recorded": {
        "type":"object",
        "required":["payment_id","toll_id","toll_name","cashier_id","timestamp","payment_method","amount","reason"],
        "properties":{
            "payment_id":{"type":"string"},
            "toll_id":{"type":"string"},
            "toll_name":{"type":"string"},
            "cashier_id":{"type":"string"},
            "timestamp":{"type":"string","format":"date-time"},
            "payment_method":{"type":"string"},
            "amount":{"type":"number"},
            "reason":{"type":"string"}
        },
        "additionalProperties": False
    },
    "prepaid.balance.updated": {
        "type":"object",
        "required":["account_id","vehicle_id","delta","balance","timestamp","source"],
        "properties":{
            "account_id":{"type":"string"},
            "vehicle_id":{"type":"string"},
            "delta":{"type":"number"},
            "balance":{"type":"number"},
            "timestamp":{"type":"string","format":"date-time"},
            "source":{"type":"string"}
        },
        "additionalProperties": False
    },
    "debt.created": {
        "type":"object",
        "required":["debt_id","vehicle_id","amount","origin","timestamp"],
        "properties":{
            "debt_id":{"type":"string"},
            "vehicle_id":{"type":"string"},
            "amount":{"type":"number"},
            "origin":{"type":"string"},
            "timestamp":{"type":"string","format":"date-time"}
        },
        "additionalProperties": False
    },
    "debt.settled": {
        "type":"object",
        "required":["debt_id","vehicle_id","amount","timestamp"],
        "properties":{
            "debt_id":{"type":"string"},
            "vehicle_id":{"type":"string"},
            "amount":{"type":"number"},
            "timestamp":{"type":"string","format":"date-time"}
        },
        "additionalProperties": False
    },

    # Multas (M5)
    "fine.issued": {
        "type":"object",
        "required":["fine_id","vehicle_id","timestamp","amount","infraction_type","state"],
        "properties":{
            "fine_id":{"type":"string"},
            "vehicle_id":{"type":"string"},
            "timestamp":{"type":"string","format":"date-time"},
            "amount":{"type":"number"},
            "infraction_type":{"type":"string"},
            "state":{"type":"string"},
            "transit_id":{"type":["string","null"]}
        },
        "additionalProperties": False
    },

    # Maestros (M6)
    "customer.upserted": {
        "type":"object",
        "required":["customer_id","name","is_active"],
        "properties":{
            "customer_id":{"type":"string"},
            "name":{"type":"string"},
            "is_active":{"type":"boolean"}
        },
        "additionalProperties": True
    },
    "vehicle.upserted": {
        "type":"object",
        "required":["vehicle_id","plate","category_id"],
        "properties":{
            "vehicle_id":{"type":"string"},
            "plate":{"type":"string"},
            "category_id":{"type":"string"}
        },
        "additionalProperties": True
    },

    # Auditoría (Transversal)
    "audit.logged": {
        "type":"object",
        "required":["event_id","event_type","timestamp","toll_name","details"],
        "properties":{
            "event_id":{"type":"string"},
            "event_type":{"type":"string"},
            "timestamp":{"type":"string","format":"date-time"},
            "toll_name":{"type":"string"},
            "details":{"type":"string"},
            "vehicle_id":{"type":["string","null"]}
        },
        "additionalProperties": False
    }
}
