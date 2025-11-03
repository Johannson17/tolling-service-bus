#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tolling Service Bus — Docs (Rabbit-only, sin API de prueba)

- /docs: documentación por Módulo (qué publica/consume), con JSON y routing keys.
- /health: ping simple.
- Sin dependencias a archivos externos: catálogos embebidos; se pueden overridear por ENV.

ENV opcionales:
  RABBIT_URL o RABBITMQ_URL      → amqps://user:pass@host/vhost (solo mostrar y /health)
  DOCS_MODULES_JSON / _PATH      → JSON de módulos (override)
  EVENTS_CATALOG_JSON / _PATH    → JSON de eventos (override)

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
  # Captura / Reconocimiento
  "lane.passage.detected": {
    "routing_key": "lane.passage.detected",
    "description_es": "Pasada detectada en carril por LPR/RFID con metadatos.",
    "payload_example": {
      "event_id": "evt-7f3c",
      "toll_id": "TOLL-01",
      "lane_id": "L-05",
      "timestamp_utc": "2025-11-03T15:10:22Z",
      "plate_raw": "ABC123?",           # string crudo
      "vehicle_class_hint": "car",      # si existe
      "source": "lpr"                   # lpr/rfid/manual/sim
    },
    "notes_es": "Detección bruta: puede traer plate_raw (no validada)."
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
    "notes_es": "La media vive fuera del bus; acá se manda la URI."
  },
  "lane.status.changed": {
    "routing_key": "lane.status.changed",
    "description_es": "Cambio de estado de carril (abierto/cerrado, modo).",
    "payload_example": {
      "toll_id": "TOLL-01",
      "lane_id": "L-05",
      "status": "open",                 # open/closed/error
      "mode": "manual",                 # manual/auto
      "timestamp_utc": "2025-11-03T15:05:00Z"
    },
    "notes_es": ""
  },
  "plate.recognition.result": {
    "routing_key": "plate.recognition.result",
    "description_es": "Resultado de reconocimiento de patente (válida o no reconocida).",
    "payload_example": {
      "event_id": "evt-7f3c",
      "recognized": True,
      "plate": "ABC123",
      "format": "mercosur",
      "confidence": 0.97,
      "timestamp_utc": "2025-11-03T15:10:23Z"
    },
    "notes_es": "Si recognized=false, plate puede omitirse; se usará para multas."
  },

  # Tarifas / Config
  "tariff.catalog.request": {
    "routing_key": "tariff.catalog.request",
    "description_es": "Pedido del catálogo de tarifas vigente.",
    "payload_example": {
      "request_id": "req-catalog-001",
      "requested_at_utc": "2025-11-03T15:00:00Z"
    },
    "notes_es": ""
  },
  "tariff.catalog.updated": {
    "routing_key": "tariff.catalog.updated",
    "description_es": "Broadcast de catálogo de tarifas actualizado.",
    "payload_example": {
      "version": "2025.11.01",
      "published_at_utc": "2025-11-01T00:00:00Z"
    },
    "notes_es": "Consumidores deben refrescar cache."
  },
  "admin.config.updated": {
    "routing_key": "admin.config.updated",
    "description_es": "Actualización de parámetros de panel (valores de peaje, reglas, multas).",
    "payload_example": {
      "config_id": "cfg-20251103",
      "keys_changed": ["toll.amount.base", "fine.amount.unrecognized_plate"],
      "published_at_utc": "2025-11-03T12:00:00Z"
    },
    "notes_es": "Fuente: M7 Panel."
  },

  # Pagos
  "payment.authorize.request": {
    "routing_key": "payment.authorize.request",
    "description_es": "Solicitud de autorización de pago.",
    "payload_example": {
      "event_id": "evt-7f3c",
      "concept": "toll",                # toll/fine
      "amount": 1200,
      "currency": "ARS",
      "account_id": "ACC-1234",
      "method": "prepay"               # prepay/card/cash
    },
    "notes_es": ""
  },
  "payment.decision.result": {
    "routing_key": "payment.decision.result",
    "description_es": "Resultado de intento de pago (success/fail/pending).",
    "payload_example": {
      "event_id": "evt-7f3c",
      "status": "success",              # success/fail/pending
      "reason": "ok",
      "tx_id": "TX-9912",
      "timestamp_utc": "2025-11-03T15:10:30Z"
    },
    "notes_es": "Si success → se publica payment.confirmed."
  },
  "payment.confirmed": {
    "routing_key": "payment.confirmed",
    "description_es": "Confirmación de cobro realizado.",
    "payload_example": {
      "event_id": "evt-7f3c",
      "concept": "toll",
      "amount": 1200,
      "currency": "ARS",
      "method": "prepay",
      "tx_id": "TX-9912",
      "confirmed_at_utc": "2025-11-03T15:10:31Z"
    },
    "notes_es": ""
  },

  # Multas
  "enforcement.decision.result": {
    "routing_key": "enforcement.decision.result",
    "description_es": "Resultado de control (ok/observed/evasion).",
    "payload_example": {
      "event_id": "evt-7f3c",
      "status": "evasion",
      "reason": "unrecognized_plate"
    },
    "notes_es": "Usado por M5 para emitir multas."
  },
  "fine.issued": {
    "routing_key": "fine.issued",
    "description_es": "Multa emitida.",
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

  # Facturación
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

  # Seguridad / Usuarios
  "auth.user.created": {
    "routing_key": "auth.user.created",
    "description_es": "Alta de usuario del sistema (roles operativos/administrativos).",
    "payload_example": {
      "user_id": "U-1001",
      "email": "op@example.com",
      "roles": ["operator"]
    },
    "notes_es": "Fuente: M8."
  },
  "auth.role.assigned": {
    "routing_key": "auth.role.assigned",
    "description_es": "Asignación/actualización de rol.",
    "payload_example": {
      "user_id": "U-1001",
      "roles": ["operator","auditor"],
      "timestamp_utc": "2025-11-03T10:00:00Z"
    },
    "notes_es": "Fuente: M8."
  },

  # Logs centralizados
  "log.app": {
    "routing_key": "log.app",
    "description_es": "Log de aplicación (nivel/info/error, componente, mensaje).",
    "payload_example": {
      "component": "payments",
      "level": "info",
      "message": "Authorized payment",
      "timestamp_utc": "2025-11-03T15:10:32Z",
      "correlation_id": "evt-7f3c"
    },
    "notes_es": "Todos los módulos publican."
  }
}

# -----------------------------------------------------------
# Catálogo EMBEBIDO por defecto (MÓDULOS) — M3 EXCLUIDO
# -----------------------------------------------------------
DOCS_MODULES_DEFAULT = {
  "modules": [
    {
      "code": "M1",
      "name_es": "Servicio de Control de Tránsito",
      "summary_es": "Registra vehículos, estadísticas en tiempo real y se integra con Reconocimiento.",
      "publishes": ["lane.passage.detected", "evidence.image.captured", "lane.status.changed", "log.app"],
      "subscribes": ["plate.recognition.result", "enforcement.decision.result"]
    },
    {
      "code": "M2",
      "name_es": "Servicio de Reconocimiento de Patentes",
      "summary_es": "Procesa imágenes/simulaciones y emite resultado de patente.",
      "publishes": ["plate.recognition.result", "log.app"],
      "subscribes": ["lane.passage.detected"]
    },
    {
      "code": "M4",
      "name_es": "Servicio de Multas",
      "summary_es": "Detecta infracciones y emite multas según reglas vigentes.",
      "publishes": ["fine.issued", "log.app"],
      "subscribes": ["plate.recognition.result", "enforcement.decision.result", "payment.decision.result"]
    },
    {
      "code": "M5",
      "name_es": "Servicio de Pagos",
      "summary_es": "Procesa cobros (prepago/tarjeta/simulado) y confirma transacciones.",
      "publishes": ["payment.decision.result", "payment.confirmed", "log.app"],
      "subscribes": ["payment.authorize.request"]
    },
    {
      "code": "M6",
      "name_es": "Servicio de Facturación",
      "summary_es": "Consolida cobros y emite comprobantes/reportes.",
      "publishes": ["invoice.issued", "log.app"],
      "subscribes": ["payment.confirmed", "fine.issued", "invoice.issue.request"]
    },
    {
      "code": "M7",
      "name_es": "Panel de Administración y Configuración",
      "summary_es": "UI para operador; publica parámetros de peaje y muestra estado global.",
      "publishes": ["admin.config.updated", "tariff.catalog.updated", "log.app"],
      "subscribes": ["lane.status.changed", "invoice.issued", "log.app"]
    },
    {
      "code": "M8",
      "name_es": "Seguridad y Usuarios",
      "summary_es": "Autenticación y autorización; gestiona roles.",
      "publishes": ["auth.user.created", "auth.role.assigned", "log.app"],
      "subscribes": []
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
        <li>Solicitar credenciales (usuario/clave) al equipo de Infraestructura (M3).</li>
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
      <div class="meta">Eventos por módulo. Broker: <code>{html.escape(RABBIT_URL) if RABBIT_URL else "no configurado"}</code> — Exchange: <code>tolling.bus</code> (topic)</div>
    </div></header>
    <main><div class="wrap">{sections}</div></main>
    <footer>© Trabajo Práctico. Docs generadas por app_bus.py. M3 (Infraestructura) no usa el bus y no se muestra.</footer>
  </body></html>"""
  return Response(html_doc, mimetype="text/html; charset=utf-8")

# -----------------------------------------------------------
# Entry
# -----------------------------------------------------------
app = app  # explicit
if __name__ == "__main__":
  app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
