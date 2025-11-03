#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tolling Service Bus — Docs (Rabbit-only)

- /docs: documentación por Módulo (qué publica/consume), con JSONs y routing keys.
- /health: ping simple.
- Sin endpoints de prueba ni sandbox HTTP.
- No depende de archivos externos: catálogos embebidos con override opcional por env/archivo.

ENV opcionales:
  RABBIT_URL o RABBITMQ_URL   → amqps://user:pass@host/vhost  (solo para mostrar y /health)
  DOCS_MODULES_JSON           → JSON string con módulos (override)
  DOCS_MODULES_PATH           → ruta a JSON de módulos (override)
  EVENTS_CATALOG_JSON         → JSON string con eventos (override)
  EVENTS_CATALOG_PATH         → ruta a JSON de eventos (override)

Procfile:
  web: gunicorn -w 2 -b 0.0.0.0:$PORT app_bus:app
"""

import os, json, html
from datetime import datetime
from typing import Dict, Any
from flask import Flask, jsonify, Response, redirect
from flask_cors import CORS

# -----------------------------------------------------------
# Rabbit URL (opcional). Si no está, la doc igual carga.
# -----------------------------------------------------------
RABBIT_URL = os.getenv("RABBIT_URL") or os.getenv("RABBITMQ_URL") or ""

# -----------------------------------------------------------
# Catálogo EMBEBIDO por defecto (EVENTOS)
# -----------------------------------------------------------
EVENTS_DEFAULT: Dict[str, Any] = {
  "lane.passage.detected": {
    "routing_key": "lane.passage.detected",
    "description_es": "Pasada detectada en carril por LPR/RFID con metadatos.",
    "payload_example": {
      "event_id": "evt-7f3c",
      "toll_id": "TOLL-01",
      "lane_id": "L-05",
      "timestamp_utc": "2025-11-03T15:10:22Z",
      "plate": "ABC123",
      "vehicle_class": "car",
      "tag_id": "TAG-9981",
      "confidence": 0.97,
      "source": "lpr"
    },
    "notes_es": "Se publica inmediatamente al detectar el vehículo."
  },
  "lane.status.changed": {
    "routing_key": "lane.status.changed",
    "description_es": "Cambio de estado de carril (abierto/cerrado, modo).",
    "payload_example": {
      "toll_id": "TOLL-01",
      "lane_id": "L-05",
      "status": "open",
      "mode": "manual",
      "timestamp_utc": "2025-11-03T15:05:00Z"
    },
    "notes_es": ""
  },
  "evidence.image.captured": {
    "routing_key": "evidence.image.captured",
    "description_es": "Referencia a evidencia (imagen/video) de una pasada.",
    "payload_example": {
      "event_id": "evt-7f3c",
      "toll_id": "TOLL-01",
      "lane_id": "L-05",
      "uri": "s3://bucket/evidence/evt-7f3c.jpg",
      "timestamp_utc": "2025-11-03T15:10:22Z"
    },
    "notes_es": "La media vive fuera del bus."
  },
  "tariff.lookup.request": {
    "routing_key": "tariff.lookup.request",
    "description_es": "Solicitud para resolver tarifa aplicable.",
    "payload_example": {
      "event_id": "evt-7f3c",
      "vehicle_class": "car",
      "toll_id": "TOLL-01",
      "timestamp_utc": "2025-11-03T15:10:22Z"
    },
    "notes_es": ""
  },
  "tariff.lookup.response": {
    "routing_key": "tariff.lookup.response",
    "description_es": "Respuesta con tarifa aplicada.",
    "payload_example": {
      "event_id": "evt-7f3c",
      "tariff_code": "CAR_DAY",
      "amount": 1200,
      "currency": "ARS",
      "valid_from": "2025-11-01",
      "valid_to": "2025-12-31"
    },
    "notes_es": "Correlacionar por event_id."
  },
  "tariff.catalog.request": {
    "routing_key": "tariff.catalog.request",
    "description_es": "Pedido de catálogo vigente.",
    "payload_example": {
      "request_id": "req-catalog-001",
      "requested_at_utc": "2025-11-03T15:00:00Z"
    },
    "notes_es": ""
  },
  "tariff.catalog.updated": {
    "routing_key": "tariff.catalog.updated",
    "description_es": "Broadcast de catálogo actualizado.",
    "payload_example": {
      "version": "2025.11.01",
      "published_at_utc": "2025-11-01T00:00:00Z"
    },
    "notes_es": "Los consumidores deben refrescar cache."
  },
  "payment.authorize.request": {
    "routing_key": "payment.authorize.request",
    "description_es": "Solicitud de autorización de pago (efectivo/tarjeta/tag).",
    "payload_example": {
      "event_id": "evt-7f3c",
      "method": "prepay",
      "amount": 1200,
      "currency": "ARS",
      "account_id": "ACC-1234"
    },
    "notes_es": ""
  },
  "payment.decision.result": {
    "routing_key": "payment.decision.result",
    "description_es": "Resultado lógico de decisión de pago.",
    "payload_example": {
      "event_id": "evt-7f3c",
      "decision": "charge",
      "reason": "default_flow"
    },
    "notes_es": ""
  },
  "payment.confirmed": {
    "routing_key": "payment.confirmed",
    "description_es": "Confirmación de cobro realizado.",
    "payload_example": {
      "event_id": "evt-7f3c",
      "amount": 1200,
      "currency": "ARS",
      "method": "prepay",
      "confirmed_at_utc": "2025-11-03T15:10:30Z",
      "tx_id": "TX-9912"
    },
    "notes_es": ""
  },
  "prepay.balance.changed": {
    "routing_key": "prepay.balance.changed",
    "description_es": "Cambio de balance de cuenta prepaga.",
    "payload_example": {
      "account_id": "ACC-1234",
      "delta": -1200,
      "balance": 8800,
      "timestamp_utc": "2025-11-03T15:10:31Z"
    },
    "notes_es": ""
  },
  "invoice.issue.request": {
    "routing_key": "invoice.issue.request",
    "description_es": "Solicitud de emisión de factura/recibo.",
    "payload_example": {
      "event_id": "evt-7f3c",
      "customer_id": "C-77",
      "amount": 1200,
      "currency": "ARS",
      "items": [{ "code": "TOLL", "qty": 1, "unit_price": 1200 }]
    },
    "notes_es": ""
  },
  "invoice.issued": {
    "routing_key": "invoice.issued",
    "description_es": "Factura/recibo emitido y disponible.",
    "payload_example": {
      "invoice_id": "FV-00012345",
      "customer_id": "C-77",
      "amount": 1200,
      "currency": "ARS",
      "pdf_uri": "s3://invoices/FV-00012345.pdf",
      "issued_at_utc": "2025-11-03T15:11:00Z"
    },
    "notes_es": ""
  },
  "enforcement.decision.result": {
    "routing_key": "enforcement.decision.result",
    "description_es": "Resultado de control: OK / Observado / Evasión.",
    "payload_example": {
      "event_id": "evt-7f3c",
      "status": "ok",
      "reason": "payment_confirmed"
    },
    "notes_es": ""
  },
  "fine.issued": {
    "routing_key": "fine.issued",
    "description_es": "Multa emitida por evasión u otra infracción.",
    "payload_example": {
      "fine_id": "F-8821",
      "event_id": "evt-7f3c",
      "plate": "ABC123",
      "amount": 20000,
      "currency": "ARS",
      "issued_at_utc": "2025-11-03T15:12:00Z"
    },
    "notes_es": ""
  },
  "master.customer.request": {
    "routing_key": "master.customer.request",
    "description_es": "Pedido de datos de cliente.",
    "payload_example": {
      "request_id": "req-cust-01",
      "customer_id": "C-77"
    },
    "notes_es": ""
  },
  "master.customer.updated": {
    "routing_key": "master.customer.updated",
    "description_es": "Actualización/alta de cliente.",
    "payload_example": {
      "customer_id": "C-77",
      "name": "Acme SA",
      "is_active": True
    },
    "notes_es": ""
  },
  "master.vehicle.request": {
    "routing_key": "master.vehicle.request",
    "description_es": "Pedido de datos de vehículo.",
    "payload_example": {
      "request_id": "req-veh-01",
      "plate": "ABC123"
    },
    "notes_es": ""
  },
  "master.vehicle.updated": {
    "routing_key": "master.vehicle.updated",
    "description_es": "Actualización/alta de vehículo.",
    "payload_example": {
      "plate": "ABC123",
      "customer_id": "C-77",
      "class": "car"
    },
    "notes_es": ""
  },
  "device.status.changed": {
    "routing_key": "device.status.changed",
    "description_es": "Cambio de estado de dispositivo (cámara, barrera, POS).",
    "payload_example": {
      "device_id": "DEV-001",
      "type": "camera",
      "status": "online",
      "timestamp_utc": "2025-11-03T15:00:00Z"
    },
    "notes_es": ""
  },
  "ops.shift.opened": {
    "routing_key": "ops.shift.opened",
    "description_es": "Apertura de turno de caja/operación.",
    "payload_example": {
      "shift_id": "S-20251103-1",
      "toll_id": "TOLL-01",
      "operator_id": "OP-33",
      "opened_at_utc": "2025-11-03T12:00:00Z"
    },
    "notes_es": ""
  },
  "ops.shift.closed": {
    "routing_key": "ops.shift.closed",
    "description_es": "Cierre de turno de caja/operación.",
    "payload_example": {
      "shift_id": "S-20251103-1",
      "toll_id": "TOLL-01",
      "operator_id": "OP-33",
      "closed_at_utc": "2025-11-03T20:00:00Z",
      "totals": { "payments": 150, "amount": 180000 }
    },
    "notes_es": ""
  },
  "ops.manual.override": {
    "routing_key": "ops.manual.override",
    "description_es": "Acción manual de backoffice/operador.",
    "payload_example": {
      "actor_id": "OP-33",
      "action": "open_barrier",
      "reason": "emergency",
      "toll_id": "TOLL-01",
      "lane_id": "L-05",
      "timestamp_utc": "2025-11-03T15:06:00Z"
    },
    "notes_es": ""
  },
  "audit.event.logged": {
    "routing_key": "audit.event.logged",
    "description_es": "Evento de auditoría (trazabilidad).",
    "payload_example": {
      "audit_id": "aud-991",
      "event_type": "lane_override",
      "message": "Barrier opened manually",
      "timestamp_utc": "2025-11-03T15:06:01Z",
      "actor_id": "OP-33",
      "scope": "lane:L-05"
    },
    "notes_es": ""
  }
}

# -----------------------------------------------------------
# Catálogo EMBEBIDO por defecto (MÓDULOS)
#   M3 = USTEDES (Operación y Backoffice)
# -----------------------------------------------------------
DOCS_MODULES_DEFAULT = {
  "modules": [
    {
      "code": "M1",
      "name_es": "Captura en Carril (LPR/RFID/Barrera)",
      "summary_es": "Detecta pasadas en tiempo real y reporta estado de carriles.",
      "publishes": ["lane.passage.detected", "lane.status.changed", "evidence.image.captured"],
      "subscribes": ["tariff.lookup.response", "payment.decision.result", "enforcement.decision.result"]
    },
    {
      "code": "M2",
      "name_es": "Motor de Tarifas y Categorías",
      "summary_es": "Resuelve tarifa aplicable y publica catálogos de tarifas/categorías.",
      "publishes": ["tariff.lookup.response", "tariff.catalog.updated"],
      "subscribes": ["tariff.lookup.request", "tariff.catalog.request"]
    },
    {
      "code": "M3",
      "name_es": "Operación y Backoffice (USTEDES)",
      "summary_es": "Orquesta operación, turnos, auditoría y ajustes.",
      "publishes": ["ops.shift.opened", "ops.shift.closed", "ops.manual.override", "audit.event.logged"],
      "subscribes": ["lane.passage.detected", "payment.confirmed", "tariff.catalog.updated", "enforcement.decision.result"]
    },
    {
      "code": "M4",
      "name_es": "Pagos / Prepago / Cobranzas",
      "summary_es": "Autoriza pagos, maneja wallet/prepago y confirma cobranzas.",
      "publishes": ["payment.confirmed", "prepay.balance.changed", "invoice.issued"],
      "subscribes": ["payment.authorize.request", "invoice.issue.request"]
    },
    {
      "code": "M5",
      "name_es": "Infracciones y Multas",
      "summary_es": "Detecta evasiones, genera y publica multas.",
      "publishes": ["enforcement.decision.result", "fine.issued"],
      "subscribes": ["lane.passage.detected", "payment.decision.result", "evidence.image.captured"]
    },
    {
      "code": "M6",
      "name_es": "Maestros y Catálogos",
      "summary_es": "Mantiene clientes, vehículos y dispositivos.",
      "publishes": ["master.customer.updated", "master.vehicle.updated", "device.status.changed"],
      "subscribes": ["master.customer.request", "master.vehicle.request"]
    },
    {
      "code": "M7",
      "name_es": "Dashboard y Reportes",
      "summary_es": "Consume eventos operativos y de negocio para tableros.",
      "publishes": [],
      "subscribes": ["audit.event.logged", "payment.confirmed", "lane.status.changed", "fine.issued"]
    }
  ]
}

# -----------------------------------------------------------
# Loaders con override por ENV/archivo, fallback embebido
# -----------------------------------------------------------
def _load_json_from_env_or_file(env_json: str, env_path: str, default_obj: Dict[str, Any]) -> Dict[str, Any]:
  js = os.getenv(env_json)
  if js:
    try:
      return json.loads(js)
    except Exception:
      pass
  p = os.getenv(env_path)
  if p and os.path.exists(p):
    try:
      with open(p, "r", encoding="utf-8") as f:
        return json.load(f)
    except Exception:
      pass
  return default_obj

EVENTS   = _load_json_from_env_or_file("EVENTS_CATALOG_JSON", "EVENTS_CATALOG_PATH", EVENTS_DEFAULT)
DOCS_CFG = _load_json_from_env_or_file("DOCS_MODULES_JSON", "DOCS_MODULES_PATH", DOCS_MODULES_DEFAULT)

# -----------------------------------------------------------
# Web (solo docs + health)
# -----------------------------------------------------------
app = Flask(__name__)
CORS(app, resources={r"*": {"origins": ["*"]}})

@app.get("/")
def _root():
  return redirect("/docs", code=302)

@app.get("/health")
def _health():
  return jsonify({
    "status": "ok",
    "time": datetime.utcnow().isoformat() + "Z",
    "rabbit_url_present": bool(RABBIT_URL)
  })

def _code(obj: Any) -> str:
  return "<pre><code>" + html.escape(json.dumps(obj, ensure_ascii=False, indent=2)) + "</code></pre>"

def _event_block(ev_key: str) -> str:
  ev = EVENTS.get(ev_key)
  if not ev:
    return f"<div class='event missing'><b>Evento no definido</b>: <code>{html.escape(ev_key)}</code></div>"
  return f"""
    <div class="event">
      <div class="rk"><b>Routing key</b>: <code>{html.escape(ev['routing_key'])}</code></div>
      <div class="desc">{html.escape(ev.get('description_es',''))}</div>
      <div class="payload"><div><b>Payload JSON</b></div>{_code(ev.get('payload_example', {}))}</div>
      <div class="notes">{html.escape(ev.get('notes_es',''))}</div>
    </div>
  """

def _module_section(mod: Dict[str, Any]) -> str:
  publishes  = mod.get("publishes", [])
  subscribes = mod.get("subscribes", [])
  pub_html   = "\n".join(_event_block(k) for k in publishes) if publishes else "<p>No publica eventos.</p>"
  sub_html   = "\n".join(_event_block(k) for k in subscribes) if subscribes else "<p>No consume eventos.</p>"

  howto = f"""
    <div class="howto">
      <h3>Cómo conectarse (RabbitMQ)</h3>
      <ol>
        <li>Solicitar credenciales (usuario/clave) al equipo de Integración.</li>
        <li>Broker URL: <code>{html.escape(RABBIT_URL) if RABBIT_URL else "<configurar RABBIT_URL>"}</code></li>
        <li>Exchange: <code>tolling.bus</code> (tipo: <code>topic</code>, durable).</li>
        <li>Publicar usando la <code>routing_key</code> de cada evento de “Publica”.</li>
        <li>Consumir declarando una <code>queue</code> durable y bindeando a las <code>routing_key</code> de “Consume”.</li>
      </ol>
    </div>
  """

  return f"""
    <section class="module">
      <h2>{html.escape(mod.get('code','?'))} — {html.escape(mod.get('name_es',''))}</h2>
      <p class="summary">{html.escape(mod.get('summary_es',''))}</p>
      {howto}
      <div class="streams">
        <div><h3>Publica</h3>{pub_html}</div>
        <div><h3>Consume</h3>{sub_html}</div>
      </div>
    </section>
  """

@app.get("/docs")
def _docs():
  CSS = """
  :root{--bg:#0f172a;--panel:#111827;--ink:#e5e7eb;--muted:#94a3b8;--accent:#22d3ee;--chip:#0b2430;--code:#0b1220;--border:#1f2937}
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);font-family:ui-sans-serif,system-ui,Segoe UI,Roboto,Helvetica,Arial;line-height:1.45}
  header{position:sticky;top:0;background:linear-gradient(180deg,var(--panel),rgba(17,24,39,.85));border-bottom:1px solid var(--border)}
  .wrap{max-width:1100px;margin:0 auto;padding:18px 20px}
  h1{margin:0;font-size:22px} .meta{color:var(--muted);font-size:13px;margin-top:6px}
  main .wrap{padding:22px 20px 64px}
  .module{border:1px solid var(--border);background:var(--panel);border-radius:16px;padding:18px;margin:18px 0 28px}
  h2{margin:0 0 8px;font-size:20px} h3{margin:18px 0 8px;font-size:16px;color:var(--accent)}
  .summary{color:var(--muted);margin:0 0 8px}
  .streams{display:grid;gap:18px;grid-template-columns:1fr} @media(min-width:980px){.streams{grid-template-columns:1fr 1fr}}
  .event{border:1px solid var(--border);border-radius:12px;padding:12px;background:rgba(34,211,238,.04);margin-bottom:12px}
  .event .rk{background:var(--chip);display:inline-block;padding:4px 8px;border-radius:999px;margin-bottom:8px}
  pre{background:var(--code);border:1px solid var(--border);padding:10px;border-radius:8px;overflow:auto}
  code{background:rgba(34,211,238,.08);padding:2px 6px;border-radius:6px}
  footer{color:var(--muted);font-size:12px;text-align:center;padding:24px;border-top:1px solid var(--border)}
  """
  sections = "\n".join(_module_section(m) for m in DOCS_CFG.get("modules", []))
  html_doc = f"""<!doctype html><html lang="es"><head>
    <meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
    <title>Tolling Service Bus — Documentación</title>
    <style>{CSS}</style></head><body>
    <header><div class="wrap">
      <h1>Tolling Service Bus — Documentación</h1>
      <div class="meta">Eventos por Módulo según consignas. Broker: <code>{html.escape(RABBIT_URL) if RABBIT_URL else "no configurado"}</code> — Exchange: <code>tolling.bus</code> (topic)</div>
    </div></header>
    <main><div class="wrap">{sections}</div></main>
    <footer>© Trabajo Práctico. Generado por app_bus.py embebido.</footer>
  </body></html>"""
  return Response(html_doc, mimetype="text/html; charset=utf-8")

# -----------------------------------------------------------
# Entry
# -----------------------------------------------------------
app_title = "Tolling Service Bus — Docs"
app.config["APPLICATION_ROOT"] = "/"

if __name__ == "__main__":
  app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
