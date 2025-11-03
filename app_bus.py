#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tolling Service Bus — Documentación (Rabbit-only)

- /docs: Guía por módulo con TODO lo necesario para conectarse en pruebas:
         URL del broker, credenciales, vhost, exchange, qué publica/consume,
         JSON esperado, y snippets de código (publish/consume).
- /health: ping simple (no conecta al broker).

Notas:
- M3 (Infraestructura) NO usa el bus y NO aparece en la doc.
- En fase de prueba se usa un USUARIO COMPARTIDO visible en la doc.
  En prod, cada módulo debe tener credenciales y ACL propias.

ENV opcionales (para override sin tocar código):
  RABBIT_URL / RABBITMQ_URL     → amqps://user:pass@host/vhost (si se setea, pisa la URL construida)
  BROKER_HOST                   → leopard.lmq.cloudamqp.com
  BROKER_VHOST                  → smqbzeze
  BROKER_USERNAME               → smqbzeze
  BROKER_PASSWORD               → xlgd... (tu pass)
  EXCHANGE_NAME                 → tolling.bus
  DOCS_MODULES_JSON / _PATH     → JSON con módulos
  EVENTS_CATALOG_JSON / _PATH   → JSON con eventos
"""

import os, json, html
from datetime import datetime
from typing import Dict, Any, List
from flask import Flask, jsonify, Response, redirect
from flask_cors import CORS

# -------------------------------------------------------------------
# Broker de PRUEBA (valores por defecto visibles en la documentación)
# -------------------------------------------------------------------
BROKER_HOST     = os.getenv("BROKER_HOST", "leopard.lmq.cloudamqp.com")
BROKER_VHOST    = os.getenv("BROKER_VHOST", "smqbzeze")
BROKER_USER     = os.getenv("BROKER_USERNAME", "smqbzeze")
BROKER_PASS     = os.getenv("BROKER_PASSWORD", "xlgdZM_NyZk8akZuWpUTDWpEb-16G4mf")
EXCHANGE_NAME   = os.getenv("EXCHANGE_NAME", "tolling.bus")

# URL construida o override directa por RABBIT_URL
RABBIT_URL = os.getenv("RABBIT_URL") or os.getenv("RABBITMQ_URL") or f"amqps://{BROKER_USER}:{BROKER_PASS}@{BROKER_HOST}/{BROKER_VHOST}"

# -------------------------------------------------------------------
# Catálogo de EVENTOS (embebido; editable por ENV)
# -------------------------------------------------------------------
EVENTS_DEFAULT: Dict[str, Any] = {
  # Captura / Reconocimiento
  "lane.passage.detected": {
    "routing_key": "lane.passage.detected",
    "description_es": "Pasada detectada en carril (LPR/RFID) con metadatos.",
    "payload_example": {
      "event_id": "evt-7f3c",
      "toll_id": "TOLL-01",
      "lane_id": "L-05",
      "timestamp_utc": "2025-11-03T15:10:22Z",
      "plate_raw": "ABC123?",
      "vehicle_class_hint": "car",
      "source": "lpr"
    },
    "notes_es": "Detección bruta; puede incluir plate_raw sin validar."
  },
  "evidence.image.captured": {
    "routing_key": "evidence.image.captured",
    "description_es": "URI de evidencia (imagen/video) asociada a una pasada.",
    "payload_example": {
      "event_id": "evt-7f3c",
      "toll_id": "TOLL-01",
      "lane_id": "L-05",
      "uri": "s3://bucket/evidence/evt-7f3c.jpg",
      "timestamp_utc": "2025-11-03T15:10:22Z"
    },
    "notes_es": "La media vive fuera del bus; se publica la referencia/URI."
  },
  "lane.status.changed": {
    "routing_key": "lane.status.changed",
    "description_es": "Cambio de estado de carril (abierto/cerrado, modo).",
    "payload_example": {
      "toll_id": "TOLL-01",
      "lane_id": "L-05",
      "status": "open",     "mode": "manual",
      "timestamp_utc": "2025-11-03T15:05:00Z"
    },
    "notes_es": ""
  },
  "plate.recognition.result": {
    "routing_key": "plate.recognition.result",
    "description_es": "Resultado de reconocimiento de patente.",
    "payload_example": {
      "event_id": "evt-7f3c",
      "recognized": True,
      "plate": "ABC123",
      "format": "mercosur",
      "confidence": 0.97,
      "timestamp_utc": "2025-11-03T15:10:23Z"
    },
    "notes_es": "Si recognized=false, plate puede omitirse."
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
    "notes_es": "Consumidores deben refrescar caché local."
  },
  "admin.config.updated": {
    "routing_key": "admin.config.updated",
    "description_es": "Actualización de parámetros del panel (valores/reglas).",
    "payload_example": {
      "config_id": "cfg-20251103",
      "keys_changed": ["toll.amount.base","fine.amount.unrecognized_plate"],
      "published_at_utc": "2025-11-03T12:00:00Z"
    },
    "notes_es": "Fuente: Panel/Admin."
  },

  # Pagos
  "payment.authorize.request": {
    "routing_key": "payment.authorize.request",
    "description_es": "Solicitud de autorización de pago.",
    "payload_example": {
      "event_id": "evt-7f3c",
      "concept": "toll",
      "amount": 1200,
      "currency": "ARS",
      "account_id": "ACC-1234",
      "method": "prepay"
    },
    "notes_es": ""
  },
  "payment.decision.result": {
    "routing_key": "payment.decision.result",
    "description_es": "Resultado del intento (success/fail/pending).",
    "payload_example": {
      "event_id": "evt-7f3c",
      "status": "success",
      "reason": "ok",
      "tx_id": "TX-9912",
      "timestamp_utc": "2025-11-03T15:10:30Z"
    },
    "notes_es": ""
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
    "notes_es": ""
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
    "description_es": "Alta de usuario del sistema.",
    "payload_example": { "user_id": "U-1001", "email": "op@example.com", "roles": ["operator"] },
    "notes_es": "Fuente: Seguridad/Usuarios."
  },
  "auth.role.assigned": {
    "routing_key": "auth.role.assigned",
    "description_es": "Asignación/actualización de rol.",
    "payload_example": { "user_id": "U-1001", "roles": ["operator","auditor"], "timestamp_utc": "2025-11-03T10:00:00Z" },
    "notes_es": "Fuente: Seguridad/Usuarios."
  },

  # Logs
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
    "notes_es": "Publicable por todos los servicios."
  }
}

# -------------------------------------------------------------------
# MÓDULOS (embebido). M3 excluido. M1 SOLO PUBLICA.
# -------------------------------------------------------------------
DOCS_MODULES_DEFAULT = {
  "modules": [
    {
      "code": "M1",
      "name_es": "Servicio de Control de Tránsito",
      "summary_es": "Registra vehículos y estado de carriles en tiempo real.",
      "publishes": ["lane.passage.detected", "evidence.image.captured", "lane.status.changed", "log.app"],
      "subscribes": []
    },
    {
      "code": "M2",
      "name_es": "Servicio de Reconocimiento de Patentes",
      "summary_es": "Procesa evidencias y emite el resultado de patente.",
      "publishes": ["plate.recognition.result", "log.app"],
      "subscribes": ["lane.passage.detected", "evidence.image.captured"]
    },
    {
      "code": "M4",
      "name_es": "Servicio de Multas",
      "summary_es": "Determina infracciones y emite multas.",
      "publishes": ["fine.issued", "log.app"],
      "subscribes": ["plate.recognition.result", "enforcement.decision.result", "payment.decision.result"]
    },
    {
      "code": "M5",
      "name_es": "Servicio de Pagos",
      "summary_es": "Autoriza y confirma pagos.",
      "publishes": ["payment.decision.result", "payment.confirmed", "log.app"],
      "subscribes": ["payment.authorize.request"]
    },
    {
      "code": "M6",
      "name_es": "Servicio de Facturación",
      "summary_es": "Emite comprobantes a partir de cobros y multas.",
      "publishes": ["invoice.issued", "log.app"],
      "subscribes": ["payment.confirmed", "fine.issued", "invoice.issue.request"]
    },
    {
      "code": "M7",
      "name_es": "Panel de Administración y Configuración",
      "summary_es": "Publica parámetros y visualiza estado global.",
      "publishes": ["admin.config.updated", "tariff.catalog.updated", "log.app"],
      "subscribes": ["lane.status.changed", "invoice.issued", "log.app"]
    },
    {
      "code": "M8",
      "name_es": "Seguridad y Usuarios",
      "summary_es": "Gestión de usuarios y roles.",
      "publishes": ["auth.user.created", "auth.role.assigned", "log.app"],
      "subscribes": []
    }
  ]
}

# -------------------------------------------------------------------
# Helpers: cargar overrides por ENV/archivo; escape; render JSON
# -------------------------------------------------------------------
def _load_json(env_json: str, env_path: str, default_obj: Dict[str, Any]) -> Dict[str, Any]:
  js = os.getenv(env_json)
  if js:
    try: return json.loads(js)
    except Exception: pass
  p = os.getenv(env_path)
  if p and os.path.exists(p):
    try:
      with open(p, "r", encoding="utf-8") as f:
        return json.load(f)
    except Exception:
      pass
  return default_obj

EVENTS   = _load_json("EVENTS_CATALOG_JSON", "EVENTS_CATALOG_PATH", EVENTS_DEFAULT)
DOCS_CFG = _load_json("DOCS_MODULES_JSON", "DOCS_MODULES_PATH", DOCS_MODULES_DEFAULT)

def _esc(s: str) -> str:
  return html.escape(s)

def _code(obj: Any) -> str:
  return "<pre><code>" + _esc(json.dumps(obj, ensure_ascii=False, indent=2)) + "</code></pre>"

# -------------------------------------------------------------------
# Snippets de publicación/consumo (Python/pika) por módulo
# -------------------------------------------------------------------
def _pub_snippet(amqp_url: str, rk: str, payload: Dict[str, Any]) -> str:
  ex = EXCHANGE_NAME
  return _esc(f"""# publisher.py
import json, pika
params = pika.URLParameters("{amqp_url}")
params.heartbeat = 30
conn = pika.BlockingConnection(params)
ch = conn.channel()
ch.exchange_declare(exchange="{ex}", exchange_type="topic", durable=True)
body = {json.dumps(payload, ensure_ascii=False, indent=2)}
ch.basic_publish(exchange="{ex}", routing_key="{rk}",
                 body=json.dumps(body).encode("utf-8"),
                 properties=pika.BasicProperties(content_type="application/json", delivery_mode=2))
print("Published:", "{rk}")
conn.close()""")

def _sub_snippet(amqp_url: str, queue: str, rks: List[str]) -> str:
  ex = EXCHANGE_NAME
  binds = "\n".join([f'channel.queue_bind(exchange="{ex}", queue="{queue}", routing_key="{rk}")' for rk in rks])
  return _esc(f"""# consumer.py
import pika, json
params = pika.URLParameters("{amqp_url}")
params.heartbeat = 30
conn = pika.BlockingConnection(params)
channel = conn.channel()
channel.exchange_declare(exchange="{ex}", exchange_type="topic", durable=True)
channel.queue_declare(queue="{queue}", durable=True)
{binds}
def on_msg(ch, method, props, body):
    print("rk=", method.routing_key, "msg=", body.decode())
    ch.basic_ack(method.delivery_tag)
channel.basic_qos(prefetch_count=50)
channel.basic_consume(queue="{queue}", on_message_callback=on_msg)
print("Listening on {queue}...")
channel.start_consuming()""")

# -------------------------------------------------------------------
# Render de documentación por módulo
# -------------------------------------------------------------------
def _event_block(key: str) -> str:
  ev = EVENTS.get(key)
  if not ev:
    return f"<div class='event missing'><b>Evento no definido</b>: <code>{_esc(key)}</code></div>"
  return f"""
    <div class="event">
      <div class="rk"><b>Routing key</b>: <code>{_esc(ev['routing_key'])}</code></div>
      <div class="desc">{_esc(ev.get('description_es',''))}</div>
      <div class="payload"><div><b>Payload JSON</b></div>{_code(ev.get('payload_example', {}))}</div>
      <div class="notes">{_esc(ev.get('notes_es',''))}</div>
    </div>
  """

def _module_conn_card(mod_code: str, publishes: List[str], subscribes: List[str]) -> str:
  # URL y cola recomendada
  amqp_url = RABBIT_URL
  qname    = f"{mod_code.lower()}.q"
  # bloques según roles
  pub_block = ""
  if publishes:
    # ejemplo: primer evento
    ev_key = publishes[0]
    ev     = EVENTS.get(ev_key, {"routing_key": ev_key, "payload_example": {}})
    pub_block = f"""
      <h4>Publicar</h4>
      <p>Usar exchange <code>{_esc(EXCHANGE_NAME)}</code> (tipo <code>topic</code>) y la <code>routing_key</code> de cada evento listado en “Publica”.</p>
      <details><summary>Snippet Python (ejemplo con <code>{_esc(ev['routing_key'])}</code>)</summary>
        <pre>{_pub_snippet(amqp_url, ev["routing_key"], ev.get("payload_example", {}))}</pre>
      </details>
    """
  sub_block = ""
  if subscribes:
    rks = [EVENTS[k]["routing_key"] if k in EVENTS else k for k in subscribes]
    sub_block = f"""
      <h4>Consumir</h4>
      <p>Declarar cola durable sugerida <code>{_esc(qname)}</code> y bindear las siguientes <code>routing_key</code>:</p>
      <ul>{"".join(f"<li><code>{_esc(r)}</code></li>" for r in rks)}</ul>
      <details><summary>Snippet Python (consumer para <code>{_esc(qname)}</code>)</summary>
        <pre>{_sub_snippet(amqp_url, qname, rks)}</pre>
      </details>
    """

  creds = f"""
    <div class="creds">
      <h4>Conexión (PRUEBA)</h4>
      <table class="kv">
        <tr><td>AMQP URL</td><td><code>{_esc(amqp_url)}</code></td></tr>
        <tr><td>Host</td><td><code>{_esc(BROKER_HOST)}</code></td></tr>
        <tr><td>VHost</td><td><code>{_esc(BROKER_VHOST)}</code></td></tr>
        <tr><td>Username</td><td><code>{_esc(BROKER_USER)}</code></td></tr>
        <tr><td>Password</td><td><code>{_esc(BROKER_PASS)}</code></td></tr>
        <tr><td>Exchange</td><td><code>{_esc(EXCHANGE_NAME)}</code> (topic)</td></tr>
        {"<tr><td>Queue sugerida</td><td><code>"+_esc(qname)+"</code></td></tr>" if subscribes else ""}
      </table>
      <p class="warn">Fase de prueba: credenciales compartidas. En producción, cada módulo tendrá usuario/ACL propios.</p>
    </div>
  """
  return creds + pub_block + sub_block

def _module_section(mod: Dict[str, Any]) -> str:
  publishes  = mod.get("publishes", [])
  subscribes = mod.get("subscribes", [])
  pub_html   = "\n".join(_event_block(k) for k in publishes) if publishes else "<p>No publica eventos.</p>"
  sub_html   = "\n".join(_event_block(k) for k in subscribes) if subscribes else "<p>No consume eventos.</p>"
  conn_html  = _module_conn_card(mod["code"], publishes, subscribes)

  return f"""
    <section class="module">
      <h2>{_esc(mod.get('code','?'))} — {_esc(mod.get('name_es',''))}</h2>
      <p class="summary">{_esc(mod.get('summary_es',''))}</p>
      {conn_html}
      <div class="streams">
        <div><h3>Publica</h3>{pub_html}</div>
        <div><h3>Consume</h3>{sub_html}</div>
      </div>
    </section>
  """

# -------------------------------------------------------------------
# Web (solo docs + health)
# -------------------------------------------------------------------
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

@app.get("/docs")
def _docs():
  CSS = """
  :root{--bg:#0f172a;--panel:#111827;--ink:#e5e7eb;--muted:#94a3b8;--accent:#22d3ee;--chip:#0b2430;--code:#0b1220;--border:#1f2937;--warn:#f59e0b}
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);font-family:ui-sans-serif,system-ui,Segoe UI,Roboto,Helvetica,Arial;line-height:1.45}
  header{position:sticky;top:0;background:linear-gradient(180deg,var(--panel),rgba(17,24,39,.85));border-bottom:1px solid var(--border)}
  .wrap{max-width:1100px;margin:0 auto;padding:18px 20px}
  h1{margin:0;font-size:22px} .meta{color:var(--muted);font-size:13px;margin-top:6px}
  main .wrap{padding:22px 20px 64px}
  .module{border:1px solid var(--border);background:var(--panel);border-radius:16px;padding:18px;margin:18px 0 28px}
  h2{margin:0 0 8px;font-size:20px} h3{margin:18px 0 8px;color:var(--accent);font-size:16px} h4{margin:14px 0 6px;font-size:15px}
  .summary{color:var(--muted);margin:0 0 8px}
  .streams{display:grid;gap:18px;grid-template-columns:1fr} @media(min-width:980px){.streams{grid-template-columns:1fr 1fr}}
  .event{border:1px solid var(--border);border-radius:12px;padding:12px;background:rgba(34,211,238,.04);margin-bottom:12px}
  .event .rk{background:var(--chip);display:inline-block;padding:4px 8px;border-radius:999px;margin-bottom:8px}
  pre{background:var(--code);border:1px solid var(--border);padding:10px;border-radius:8px;overflow:auto}
  code{background:rgba(34,211,238,.08);padding:2px 6px;border-radius:6px}
  .kv{width:100%;border-collapse:collapse;margin-top:8px}
  .kv td{border-bottom:1px solid var(--border);padding:8px 6px;vertical-align:top}
  .warn{color:var(--warn);font-size:12px;margin-top:6px}
  footer{color:var(--muted);font-size:12px;text-align:center;padding:24px;border-top:1px solid var(--border)}
  """
  modules_html = "\n".join(_module_section(m) for m in DOCS_CFG.get("modules", []))
  top = f"""
  <section class="module">
    <h2>Broker de prueba</h2>
    <table class="kv">
      <tr><td>AMQP URL</td><td><code>{_esc(RABBIT_URL)}</code></td></tr>
      <tr><td>Host</td><td><code>{_esc(BROKER_HOST)}</code></td></tr>
      <tr><td>VHost</td><td><code>{_esc(BROKER_VHOST)}</code></td></tr>
      <tr><td>Username</td><td><code>{_esc(BROKER_USER)}</code></td></tr>
      <tr><td>Password</td><td><code>{_esc(BROKER_PASS)}</code></td></tr>
      <tr><td>Exchange</td><td><code>{_esc(EXCHANGE_NAME)}</code> (topic)</td></tr>
    </table>
    <p class="warn">Solo para pruebas. En producción, cada módulo tendrá credenciales dedicadas y permisos restringidos.</p>
  </section>
  """
  html_doc = f"""<!doctype html><html lang="es"><head>
    <meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
    <title>Tolling Service Bus — Documentación</title>
    <style>{CSS}</style></head><body>
    <header><div class="wrap">
      <h1>Tolling Service Bus — Documentación</h1>
      <div class="meta">M3 (Infraestructura) no usa el bus. Esta guía muestra TODO lo necesario por módulo para publicar/consumir en fase de prueba.</div>
    </div></header>
    <main><div class="wrap">{top}{modules_html}</div></main>
    <footer>© Trabajo Práctico — Bus RabbitMQ de pruebas — Exchange: <code>{_esc(EXCHANGE_NAME)}</code></footer>
  </body></html>"""
  return Response(html_doc, mimetype="text/html; charset=utf-8")

# -------------------------------------------------------------------
# Entry
# -------------------------------------------------------------------
if __name__ == "__main__":
  app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
