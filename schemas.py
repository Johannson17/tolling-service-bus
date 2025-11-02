# schemas.py
# Envelope y esquemas de eventos. Sólo entra al BUS lo que requiere realtime.
ENVELOPE = {
    "type": "object",
    "required": ["event", "version", "data", "meta"],
    "properties": {
        "event": {"type": "string"},               # routing key sugerida = event
        "version": {"type": "string"},             # "1.0"
        "data": {"type": "object"},                # payload validado contra schema del evento
        "meta": {                                  # trazabilidad
            "type": "object",
            "required": ["occurred_at", "producer"],
            "properties": {
                "occurred_at": {"type": "string", "format": "date-time"},
                "producer": {"type": "string"},    # moduloX
                "correlation_id": {"type": "string"},
                "causation_id": {"type": "string"}
            },
            "additionalProperties": True
        }
    },
    "additionalProperties": False
}

SCHEMAS = {
    # ====================== OPERACIONES (Módulo 3) ======================
    # Paso por cabina detectado (contabilización en tiempo real)
    "transit.recorded": {
        "type": "object",
        "required": ["transit_id", "toll_id", "toll_name", "lane", "vehicle_id", "vehicle_type", "timestamp"],
        "properties": {
            "transit_id": {"type": "string"},
            "toll_id": {"type": "string"},
            "toll_name": {"type": "string"},
            "lane": {"type": "string"},
            "vehicle_id": {"type": "string"},
            "vehicle_type": {"type": "string"},     # car/truck/motorcycle/bus/etc
            "timestamp": {"type": "string", "format": "date-time"},
            "details": {"type": "string"}
        },
        "additionalProperties": False
    },
    # Lectura de patente/visión
    "plate.captured": {
        "type": "object",
        "required": ["toll_id", "lane", "image_id", "plate", "confidence", "timestamp"],
        "properties": {
            "toll_id": {"type": "string"},
            "lane": {"type": "string"},
            "image_id": {"type": "string"},
            "plate": {"type": "string"},
            "confidence": {"type": "number"},
            "timestamp": {"type": "string", "format": "date-time"}
        },
        "additionalProperties": False
    },
    # Estado de peaje/alertas (apertura/cierre/cola/incidente)
    "toll.status.updated": {
        "type": "object",
        "required": ["toll_id", "toll_name", "open_lanes", "closed_lanes", "timestamp"],
        "properties": {
            "toll_id": {"type": "string"},
            "toll_name": {"type": "string"},
            "open_lanes": {"type": "integer"},
            "closed_lanes": {"type": "integer"},
            "timestamp": {"type": "string", "format": "date-time"},
            "alerts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["type", "time"],
                    "properties": {
                        "type": {"type": "string"},   # accident/queue/device_down
                        "lane": {"type": ["string","null"]},
                        "time": {"type": "string", "format": "date-time"}
                    }
                }
            }
        },
        "additionalProperties": False
    },

    # ====================== TARIFAS/CATEGORÍAS (Módulo 2) ======================
    "rate.updated": {
        "type": "object",
        "required": ["rate_id", "category_id", "peak_price", "offpeak_price", "valid_from", "valid_to"],
        "properties": {
            "rate_id": {"type": "string"},
            "category_id": {"type": "string"},
            "peak_price": {"type": "number"},
            "offpeak_price": {"type": "number"},
            "valid_from": {"type": "string", "format": "date"},
            "valid_to": {"type": "string", "format": "date"}
        },
        "additionalProperties": False
    },
    "vehicle.category.changed": {
        "type": "object",
        "required": ["vehicle_id", "old_category_id", "new_category_id", "timestamp"],
        "properties": {
            "vehicle_id": {"type": "string"},
            "old_category_id": {"type": "string"},
            "new_category_id": {"type": "string"},
            "timestamp": {"type": "string", "format": "date-time"}
        },
        "additionalProperties": False
    },

    # ====================== PAGOS/PREPAGO/DEUDA (Módulo 4) ======================
    "payment.recorded": {
        "type": "object",
        "required": ["payment_id", "toll_id", "toll_name", "cashier_id", "timestamp", "payment_method", "amount", "reason"],
        "properties": {
            "payment_id": {"type": "string"},
            "toll_id": {"type": "string"},
            "toll_name": {"type": "string"},
            "cashier_id": {"type": "string"},
            "timestamp": {"type": "string", "format": "date-time"},
            "payment_method": {"type": "string"},     # cash/card/tag/prepaid/online
            "amount": {"type": "number"},
            "reason": {"type": "string"}              # toll/fine/other
        },
        "additionalProperties": False
    },
    "prepaid.balance.updated": {
        "type": "object",
        "required": ["account_id", "vehicle_id", "delta", "balance", "timestamp", "source"],
        "properties": {
            "account_id": {"type": "string"},
            "vehicle_id": {"type": "string"},
            "delta": {"type": "number"},
            "balance": {"type": "number"},
            "timestamp": {"type": "string", "format": "date-time"},
            "source": {"type": "string"}              # recharge/debit
        },
        "additionalProperties": False
    },
    "debt.created": {
        "type": "object",
        "required": ["debt_id", "vehicle_id", "amount", "origin", "timestamp"],
        "properties": {
            "debt_id": {"type": "string"},
            "vehicle_id": {"type": "string"},
            "amount": {"type": "number"},
            "origin": {"type": "string"},            # toll_evasion/fine
            "timestamp": {"type": "string", "format": "date-time"}
        },
        "additionalProperties": False
    },
    "debt.settled": {
        "type": "object",
        "required": ["debt_id", "vehicle_id", "amount", "timestamp"],
        "properties": {
            "debt_id": {"type": "string"},
            "vehicle_id": {"type": "string"},
            "amount": {"type": "number"},
            "timestamp": {"type": "string", "format": "date-time"}
        },
        "additionalProperties": False
    },

    # ====================== MULTAS (Módulo 5) ======================
    "fine.issued": {
        "type": "object",
        "required": ["fine_id", "vehicle_id", "timestamp", "amount", "infraction_type", "state"],
        "properties": {
            "fine_id": {"type": "string"},
            "vehicle_id": {"type": "string"},
            "timestamp": {"type": "string", "format": "date-time"},
            "amount": {"type": "number"},
            "infraction_type": {"type": "string"},    # evasion/speed/other
            "state": {"type": "string"},              # open/paid/cancelled
            "transit_id": {"type": ["string","null"]}
        },
        "additionalProperties": False
    },

    # ====================== MAESTROS COMPARTIDOS (Módulo 6) ======================
    "customer.upserted": {
        "type": "object",
        "required": ["customer_id", "name", "is_active"],
        "properties": {
            "customer_id": {"type": "string"},
            "name": {"type": "string"},
            "is_active": {"type": "boolean"}
        },
        "additionalProperties": True
    },
    "vehicle.upserted": {
        "type": "object",
        "required": ["vehicle_id", "plate", "category_id"],
        "properties": {
            "vehicle_id": {"type": "string"},
            "plate": {"type": "string"},
            "category_id": {"type": "string"}
        },
        "additionalProperties": True
    },

    # ====================== AUDITORÍA (todos) ======================
    "audit.logged": {
        "type": "object",
        "required": ["event_id", "event_type", "timestamp", "toll_name", "details"],
        "properties": {
            "event_id": {"type": "string"},
            "event_type": {"type": "string"},
            "timestamp": {"type": "string", "format": "date-time"},
            "toll_name": {"type": "string"},
            "details": {"type": "string"},
            "vehicle_id": {"type": ["string","null"]}
        },
        "additionalProperties": False
    }
}
