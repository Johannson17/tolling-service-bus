#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tolling Service Bus — Rabbit-only
- /docs: guía por módulo (token de prueba, cómo conectar, JSONs, snippets).
- /auth/credentials (POST): recibe token de módulo y devuelve credenciales AMQP + política.
- /health: ping.
- Sin HTML de API de pruebas ni simuladores.

En producción:
- Cada módulo tendrá usuario/ACL propios en Rabbit.
- Este servicio puede provisionar usuarios/permisos si configurás el HTTP API de Rabbit.

ENV (overrides):
  # Broker (si no seteás RABBIT_URL, se arma con HOST/USER/PASS/VHOST)
  RABBIT_URL | RABBITMQ_URL
  BROKER_HOST=leopard.lmq.cloudamqp.com
  BROKER_VHOST=smqbzeze
  BROKER_USERNAME=smqbzeze
  BROKER_PASSWORD=xlgdZM_NyZk8akZuWpUTDWpEb-16G4mf
  EXCHANGE_NAME=tolling.bus

  # Tokens y usuarios por módulo (JSON opcional para override)
  MODULE_TOKENS_JSON='{"M1":"token-...","M2":"..."}'
  MODULE_USERS_JSON='{"M1":{"username":"m1_user","password":"m1_pass"}, ...}'
  USE_SHARED_ACCOUNT=true|false   # si true, todos devuelven BROKER_USERNAME/PASSWORD

  # Catálogos (override opcional)
  DOCS_MODULES_JSON / DOCS_MODULES_PATH
  EVENTS_CATALOG_JSON / EVENTS_CATALOG_PATH

  # HTTP API de Rabbit (opcional para provisionar)
  RABBIT_HTTP_API_BASE=https://leopard.lmq.cloudamqp.com/api
  RABBIT_HTTP_API_USER=admin
  RABBIT_HTTP_API_PASS=********
  ADMIN_TOKEN=********          # para /admin/provision si lo querés exponer (no expuesto por defecto)
"""

import os, json, html, base64
from datetime import datetime
from typing import Dict, Any, List, Optional
from flask import Flask, jsonify, Response, redirect, request
from flask_cors import CORS

# ---------- Broker base (pruebas visibles) ----------
BROKER_HOST   = os.getenv("BROKER_HOST", "leopard.lmq.cloudamqp.com")
BROKER_VHOST  = os.getenv("BROKER_VHOST", "smqbzeze")
BROKER_USER   = os.getenv("BROKER_USERNAME", "smqbzeze")
BROKER_PASS   = os.getenv("BROKER_PASSWORD", "xlgdZM_NyZk8akZuWpUTDWpEb-16G4mf")
EXCHANGE_NAME = os.getenv("EXCHANGE_NAME", "tolling.bus")
RABBIT_URL    = os.getenv("RABBIT_URL") or os.getenv("RABBITMQ_URL") or f"amqps://{BROKER_USER}:{BROKER_PASS}@{BROKER_HOST}/{BROKER_VHOST}"

# ---------- Catálogo de eventos (embebido; se puede overridear) ----------
EVENTS_DEFAULT: Dict[str, Any] = {
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
    "notes_es": "Detección bruta; plate_raw puede no estar validada."
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
    "notes_es": "La media vive fuera del bus; se publica la referencia."
  },
  "lane.status.changed": {
    "routing_key": "lane.status.changed",
    "description_es": "Cambio de estado de carril.",
    "payload_example": {"toll_id":"TOLL-01","lane_id":"L-05","status":"open","mode":"manual","timestamp_utc":"2025-11-03T15:05:00Z"},
    "notes_es": ""
  },
  "plate.recognition.result": {
    "routing_key": "plate.recognition.result",
    "description_es": "Resultado de reconocimiento de patente.",
    "payload_example": {"event_id":"evt-7f3c","recognized": True,"plate":"ABC123","format":"mercosur","confidence":0.97,"timestamp_utc":"2025-11-03T15:10:23Z"},
    "notes_es": "Si recognized=false, plate puede omitirse."
  },
  "tariff.catalog.request": {
    "routing_key": "tariff.catalog.request",
    "description_es": "Pedido del catálogo de tarifas vigente.",
    "payload_example": {"request_id":"req-catalog-001","requested_at_utc":"2025-11-03T15:00:00Z"},
    "notes_es": ""
  },
  "tariff.catalog.updated": {
    "routing_key": "tariff.catalog.updated",
    "description_es": "Broadcast de catálogo de tarifas actualizado.",
    "payload_example": {"version":"2025.11.01","published_at_utc":"2025-11-01T00:00:00Z"},
    "notes_es": "Refrescar caché local."
  },
  "admin.config.updated": {
    "routing_key": "admin.config.updated",
    "description_es": "Actualización de parámetros de panel.",
    "payload_example": {"config_id":"cfg-20251103","keys_changed":["toll.amount.base","fine.amount.unrecognized_plate"],"published_at_utc":"2025-11-03T12:00:00Z"},
    "notes_es": "Fuente: Panel/Admin."
  },
  "payment.authorize.request": {
    "routing_key": "payment.authorize.request",
    "description_es": "Solicitud de autorización de pago.",
    "payload_example": {"event_id":"evt-7f3c","concept":"toll","amount":1200,"currency":"ARS","account_id":"ACC-1234","method":"prepay"},
    "notes_es": ""
  },
  "payment.decision.result": {
    "routing_key": "payment.decision.result",
    "description_es": "Resultado del intento (success/fail/pending).",
    "payload_example": {"event_id":"evt-7f3c","status":"success","reason":"ok","tx_id":"TX-9912","timestamp_utc":"2025-11-03T15:10:30Z"},
    "notes_es": ""
  },
  "payment.confirmed": {
    "routing_key": "payment.confirmed",
    "description_es": "Confirmación de cobro.",
    "payload_example": {"event_id":"evt-7f3c","concept":"toll","amount":1200,"currency":"ARS","method":"prepay","tx_id":"TX-9912","confirmed_at_utc":"2025-11-03T15:10:31Z"},
    "notes_es": ""
  },
  "enforcement.decision.result": {
    "routing_key": "enforcement.decision.result",
    "description_es": "Resultado de control (ok/observed/evasion).",
    "payload_example": {"event_id":"evt-7f3c","status":"evasion","reason":"unrecognized_plate"},
    "notes_es": ""
  },
  "fine.issued": {
    "routing_key": "fine.issued",
    "description_es": "Multa emitida.",
    "payload_example": {"fine_id":"F-8821","event_id":"evt-7f3c","plate":"ABC123","amount":20000,"currency":"ARS","issued_at_utc":"2025-11-03T15:12:00Z"},
    "notes_es": ""
  },
  "invoice.issue.request": {
    "routing_key": "invoice.issue.request",
    "description_es": "Solicitud de emisión de comprobante.",
    "payload_example": {"event_id":"evt-7f3c","customer_id":"C-77","amount":1200,"currency":"ARS","items":[{"code":"TOLL","qty":1,"unit_price":1200}]},
    "notes_es": ""
  },
  "invoice.issued": {
    "routing_key": "invoice.issued",
    "description_es": "Comprobante emitido.",
    "payload_example": {"invoice_id":"FV-00012345","customer_id":"C-77","amount":1200,"currency":"ARS","pdf_uri":"s3://invoices/FV-00012345.pdf","issued_at_utc":"2025-11-03T15:11:00Z"},
    "notes_es": ""
  },
  "auth.user.created": {
    "routing_key": "auth.user.created",
    "description_es": "Alta de usuario del sistema.",
    "payload_example": {"user_id":"U-1001","email":"op@example.com","roles":["operator"]},
    "notes_es": "Fuente: Seguridad/Usuarios."
  },
  "auth.role.assigned": {
    "routing_key": "auth.role.assigned",
    "description_es": "Asignación/actualización de rol.",
    "payload_example": {"user_id":"U-1001","roles":["operator","auditor"],"timestamp_utc":"2025-11-03T10:00:00Z"},
    "notes_es": "Fuente: Seguridad/Usuarios."
  },
  "log.app": {
    "routing_key": "log.app",
    "description_es": "Log de aplicación.",
    "payload_example": {"component":"payments","level":"info","message":"Authorized payment","timestamp_utc":"2025-11-03T15:10:32Z","correlation_id":"evt-7f3c"},
    "notes_es": "Publicable por todos."
  }
}

# ---------- Módulos (M3 INFRA excluido; M1 solo publica) ----------
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

# ---------- Overrides por ENV/archivo ----------
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

# ---------- Tokens (prueba) y usuarios por módulo ----------
TOKENS_DEFAULT = {
  "M1": "tok-M1-TEST-12ab",
  "M2": "tok-M2-TEST-34cd",
  "M4": "tok-M4-TEST-56ef",
  "M5": "tok-M5-TEST-78gh",
  "M6": "tok-M6-TEST-90ij",
  "M7": "tok-M7-TEST-12kl",
  "M8": "tok-M8-TEST-34mn"
}
USERS_DEFAULT = {
  # En pruebas podés usar usuarios ficticios; con USE_SHARED_ACCOUNT=true se devolverá BROKER_USERNAME/PASSWORD
  "M1": {"username":"m1_test","password":"m1_test_pass"},
  "M2": {"username":"m2_test","password":"m2_test_pass"},
  "M4": {"username":"m4_test","password":"m4_test_pass"},
  "M5": {"username":"m5_test","password":"m5_test_pass"},
  "M6": {"username":"m6_test","password":"m6_test_pass"},
  "M7": {"username":"m7_test","password":"m7_test_pass"},
  "M8": {"username":"m8_test","password":"m8_test_pass"}
}
MODULE_TOKENS = json.loads(os.getenv("MODULE_TOKENS_JSON", json.dumps(TOKENS_DEFAULT)))
MODULE_USERS  = json.loads(os.getenv("MODULE_USERS_JSON",  json.dumps(USERS_DEFAULT)))
USE_SHARED    = os.getenv("USE_SHARED_ACCOUNT", "false").lower() == "true"

def _policy_for_module(mod_code: str) -> Dict[str, Any]:
  # Arma listas de routing keys permitidas para publicar/consumir segun catálogo
  publishes = []
  consumes  = []
  for m in DOCS_CFG.get("modules", []):
    if m.get("code") == mod_code:
      for evk in m.get("publishes", []):
        rk = EVENTS.get(evk, {}).get("routing_key", evk)
        publishes.append(rk)
      for evk in m.get("subscribes", []):
        rk = EVENTS.get(evk, {}).get("routing_key", evk)
        consumes.append(rk)
      break
  return {"publish": sorted(set(publishes)), "consume": sorted(set(consumes))}

def _amqp_url(user: str, pwd: str) -> str:
  return f"amqps://{user}:{pwd}@{BROKER_HOST}/{BROKER_VHOST}"

# ---------- Web ----------
app = Flask(__name__)
CORS(app, resources={r"*": {"origins": ["*"]}})

@app.get("/")
def _root():
  return redirect("/docs", code=302)

@app.get("/health")
def _health():
  return jsonify({"status":"ok","time":datetime.utcnow().isoformat()+"Z","rabbit_url_present":bool(RABBIT_URL)})

# ---------- Auth API: exchange token → credentials + policy ----------
@app.post("/auth/credentials")
def issue_credentials():
  """
  Body JSON: {"token":"..."}  o Header: X-Module-Token: ...
  Devuelve:
    {
      "module": "M5",
      "amqp_url": "...",
      "host": "...", "vhost":"...", "username":"...", "password":"...",
      "exchange":"tolling.bus",
      "queue_suggested":"m5.q",
      "policy":{"publish":[...], "consume":[...]}
    }
  """
  token = (request.json or {}).get("token") if request.is_json else None
  if not token:
    token = request.headers.get("X-Module-Token", "")
  token = (token or "").strip()
  if not token:
    return jsonify({"error":"missing_token"}), 400

  # Buscar módulo por token
  mod_code = None
  for k, v in MODULE_TOKENS.items():
    if v == token:
      mod_code = k
      break
  if not mod_code:
    return jsonify({"error":"invalid_token"}), 401

  # Credenciales
  if USE_SHARED:
    u, p = BROKER_USER, BROKER_PASS
  else:
    creds = MODULE_USERS.get(mod_code) or {}
    u, p = creds.get("username"), creds.get("password")
    if not u or not p:
      # fallback a shared si faltan
      u, p = BROKER_USER, BROKER_PASS

  policy = _policy_for_module(mod_code)
  payload = {
    "module": mod_code,
    "amqp_url": _amqp_url(u, p),
    "host": BROKER_HOST,
    "vhost": BROKER_VHOST,
    "username": u,
    "password": p,
    "exchange": EXCHANGE_NAME,
    "queue_suggested": f"{mod_code.lower()}.q" if policy["consume"] else None,
    "policy": policy
  }
  return jsonify(payload)

# ---------- HTML helpers ----------
def _esc(s: str) -> str: return html.escape(str(s))
def _code(obj: Any) -> str: return "<pre><code>"+_esc(json.dumps(obj, ensure_ascii=False, indent=2))+"</code></pre>"

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

def _pub_snippet(amqp_url: str, rk: str, payload: Dict[str, Any]) -> str:
  return _esc(f"""# publisher.py
import json, pika
params = pika.URLParameters("{amqp_url}")
params.heartbeat = 30
conn = pika.BlockingConnection(params)
ch = conn.channel()
ch.exchange_declare(exchange="{EXCHANGE_NAME}", exchange_type="topic", durable=True)
body = {json.dumps(payload, ensure_ascii=False, indent=2)}
ch.basic_publish(exchange="{EXCHANGE_NAME}", routing_key="{rk}",
                 body=json.dumps(body).encode("utf-8"),
                 properties=pika.BasicProperties(content_type="application/json", delivery_mode=2))
print("Published:", "{rk}")
conn.close()""")

def _sub_snippet(amqp_url: str, queue: str, rks: List[str]) -> str:
  binds = "\n".join([f'channel.queue_bind(exchange="{EXCHANGE_NAME}", queue="{queue}", routing_key="{rk}")' for rk in rks])
  return _esc(f"""# consumer.py
import pika, json
params = pika.URLParameters("{amqp_url}")
params.heartbeat = 30
conn = pika.BlockingConnection(params)
channel = conn.channel()
channel.exchange_declare(exchange="{EXCHANGE_NAME}", exchange_type="topic", durable=True)
channel.queue_declare(queue="{queue}", durable=True)
{binds}
def on_msg(ch, method, props, body):
    print("rk=", method.routing_key, "msg=", body.decode())
    ch.basic_ack(method.delivery_tag)
channel.basic_qos(prefetch_count=50)
channel.basic_consume(queue="{queue}", on_message_callback=on_msg)
print("Listening on {queue}...")
channel.start_consuming()""")

def _module_conn_card(mod: Dict[str, Any]) -> str:
  code  = mod["code"]
  pubs  = [EVENTS[k]["routing_key"] if k in EVENTS else k for k in mod.get("publishes", [])]
  subs  = [EVENTS[k]["routing_key"] if k in EVENTS else k for k in mod.get("subscribes", [])]
  tok   = MODULE_TOKENS.get(code, "—")
  qname = f"{code.lower()}.q" if subs else None
  # ejemplo amqp_url mostrado con shared (para copy/paste rápido en pruebas)
  sample_user, sample_pass = (BROKER_USER, BROKER_PASS) if USE_SHARED else (MODULE_USERS.get(code,{}).get("username","<user>"), MODULE_USERS.get(code,{}).get("password","<pass>"))
  sample_amqp = _amqp_url(sample_user, sample_pass)

  pub_sample = ""
  if pubs:
    pub_sample = f"""
      <h4>Publicar</h4>
      <p>Exchange <code>{_esc(EXCHANGE_NAME)}</code> (topic). Usar las <code>routing_key</code> listadas abajo.</p>
      <details><summary>Snippet (publicación)</summary><pre>{_pub_snippet(sample_amqp, pubs[0], EVENTS.get(mod.get('publishes',[pubs[0]])[0],{}).get('payload_example', {}))}</pre></details>
    """
  sub_sample = ""
  if subs:
    sub_sample = f"""
      <h4>Consumir</h4>
      <p>Declarar cola durable sugerida <code>{_esc(qname)}</code> y bindear:</p>
      <ul>{"".join(f"<li><code>{_esc(r)}</code></li>" for r in subs)}</ul>
      <details><summary>Snippet (consumer)</summary><pre>{_sub_snippet(sample_amqp, qname, subs)}</pre></details>
    """
  creds = f"""
    <div class="creds">
      <h4>Token de prueba del módulo</h4>
      <table class="kv">
        <tr><td>Module</td><td><code>{_esc(code)}</code></td></tr>
        <tr><td>Token</td><td><code>{_esc(tok)}</code></td></tr>
      </table>
      <h4>Cómo obtener tus credenciales</h4>
      <p>Hacé un POST a <code>/auth/credentials</code> con tu token.</p>
      <pre><code>curl -s -X POST {request.host_url.rstrip('/')}/auth/credentials -H "Content-Type: application/json" -d '{{"token":"{_esc(tok)}"}}'</code></pre>
      <p>Respuesta incluye: <code>amqp_url</code>, <code>username</code>, <code>password</code>, <code>exchange</code>, <code>queue_suggested</code> y <code>policy</code> (publish/consume).</p>
      <h4>Conexión de prueba (visible)</h4>
      <table class="kv">
        <tr><td>AMQP URL</td><td><code>{_esc(sample_amqp)}</code></td></tr>
        <tr><td>Exchange</td><td><code>{_esc(EXCHANGE_NAME)}</code> (topic)</td></tr>
      </table>
      <p class="warn">Pruebas: credenciales visibles. En producción se emitirán usuarios/ACL por módulo.</p>
    </div>
  """
  streams = f"""
    <div class="streams">
      <div><h3>Publica</h3>{("".join(_event_block(k) for k in mod.get("publishes", [])) if mod.get("publishes") else "<p>No publica eventos.</p>")}</div>
      <div><h3>Consume</h3>{("".join(_event_block(k) for k in mod.get("subscribes", [])) if mod.get("subscribes") else "<p>No consume eventos.</p>")}</div>
    </div>
  """
  return creds + pub_sample + sub_sample + streams

# ---------- /docs ----------
@app.get("/docs")
def docs():
  CSS = """
  :root{--bg:#0f172a;--panel:#111827;--ink:#e5e7eb;--muted:#94a3b8;--accent:#22d3ee;--chip:#0b2430;--code:#0b1220;--border:#1f2937;--warn:#f59e0b}
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);font-family:ui-sans-serif,system-ui,Segoe UI,Roboto,Helvetica,Arial}
  header{position:sticky;top:0;background:linear-gradient(180deg,var(--panel),rgba(17,24,39,.85));border-bottom:1px solid var(--border)}
  .wrap{max-width:1100px;margin:0 auto;padding:18px 20px}
  h1{margin:0;font-size:22px} .meta{color:var(--muted);font-size:13px;margin-top:6px}
  main .wrap{padding:22px 20px 64px}
  .module{border:1px solid var(--border);background:var(--panel);border-radius:16px;padding:18px;margin:18px 0 28px}
  h2{margin:0 0 8px;font-size:20px} h3{margin:18px 0 8px;color:var(--accent);font-size:16px} h4{margin:14px 0 6px;font-size:15px}
  .summary{color:var(--muted);margin:0 0 8px}
  .event{border:1px solid var(--border);border-radius:12px;padding:12px;background:rgba(34,211,238,.04);margin-bottom:12px}
  .event .rk{background:var(--chip);display:inline-block;padding:4px 8px;border-radius:999px;margin-bottom:8px}
  pre{background:var(--code);border:1px solid var(--border);padding:10px;border-radius:8px;overflow:auto}
  code{background:rgba(34,211,238,.08);padding:2px 6px;border-radius:6px}
  .kv{width:100%;border-collapse:collapse;margin-top:8px}
  .kv td{border-bottom:1px solid var(--border);padding:8px 6px;vertical-align:top}
  .warn{color:var(--warn);font-size:12px;margin-top:6px}
  footer{color:var(--muted);font-size:12px;text-align:center;padding:24px;border-top:1px solid var(--border)}
  """
  intro = f"""
  <section class="module">
    <h2>Broker de prueba</h2>
    <table class="kv">
      <tr><td>AMQP URL (compartida)</td><td><code>{_esc(RABBIT_URL)}</code></td></tr>
      <tr><td>Host</td><td><code>{_esc(BROKER_HOST)}</code></td></tr>
      <tr><td>VHost</td><td><code>{_esc(BROKER_VHOST)}</code></td></tr>
      <tr><td>Exchange</td><td><code>{_esc(EXCHANGE_NAME)}</code> (topic)</td></tr>
    </table>
    <p class="warn">Solo pruebas: credenciales visibles y/o token por módulo. En producción se aplicarán usuarios/ACL per-module y topic-permissions.</p>
    <h3>Cómo obtener credenciales por módulo</h3>
    <ol>
      <li>Tomá tu token en la sección de tu módulo abajo.</li>
      <li>POST a <code>/auth/credentials</code> con <code>{{"token":"..."}}</code>.</li>
      <li>Usá el <code>amqp_url</code> devuelto y respetá la <code>policy</code> (publish/consume).</li>
    </ol>
  </section>
  """
  modules_html = ""
  for m in DOCS_CFG.get("modules", []):
    modules_html += f"""
      <section class="module">
        <h2>{_esc(m.get('code','?'))} — {_esc(m.get('name_es',''))}</h2>
        <p class="summary">{_esc(m.get('summary_es',''))}</p>
        {_module_conn_card(m)}
      </section>
    """
  html_doc = f"""<!doctype html><html lang="es"><head>
    <meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
    <title>Tolling Service Bus — Documentación</title>
    <style>{CSS}</style></head><body>
    <header><div class="wrap">
      <h1>Tolling Service Bus — Documentación</h1>
      <div class="meta">M3 (Infraestructura) no usa el bus. Esta guía contiene tokens de prueba, credenciales y políticas por módulo.</div>
    </div></header>
    <main><div class="wrap">{intro}{modules_html}</div></main>
    <footer>© Trabajo Práctico — Exchange: <code>{_esc(EXCHANGE_NAME)}</code></footer>
  </body></html>"""
  return Response(html_doc, mimetype="text/html; charset=utf-8")

# ---------- (Opcional) Provisionar usuarios/permisos en Rabbit vía HTTP API ----------
def _http_provision_supported() -> bool:
  return bool(os.getenv("RABBIT_HTTP_API_BASE") and os.getenv("RABBIT_HTTP_API_USER") and os.getenv("RABBIT_HTTP_API_PASS"))

def _topic_regex(keys: List[str]) -> str:
  # crea un regex que cubra exactamente esas routing keys (sin wildcards)
  if not keys: return "^$"
  esc = [k.replace(".", r"\.") for k in keys]
  return "^(" + "|".join(esc) + ")$"

@app.post("/admin/provision")
def admin_provision():
  # Requiere ADMIN_TOKEN
  admin_tok = os.getenv("ADMIN_TOKEN", "")
  if not admin_tok or request.headers.get("X-Admin-Token") != admin_tok:
    return jsonify({"error":"unauthorized"}), 401
  if not _http_provision_supported():
    return jsonify({"error":"http_api_not_configured"}), 400

  try:
    import requests
  except Exception:
    return jsonify({"error":"requests_not_available"}), 500

  base = os.getenv("RABBIT_HTTP_API_BASE").rstrip("/")
  auth = (os.getenv("RABBIT_HTTP_API_USER"), os.getenv("RABBIT_HTTP_API_PASS"))
  vhost_enc = base64.urlsafe_b64encode(BROKER_VHOST.encode()).decode().rstrip("=")  # CloudAMQP acepta / api/paths sin encode; usamos urlsafe por seguridad

  results = []
  for m in DOCS_CFG.get("modules", []):
    code = m["code"]
    creds = MODULE_USERS.get(code, {})
    if not creds: continue
    u, p = creds.get("username"), creds.get("password")
    if not (u and p): continue

    # 1) Create/Update user
    r = requests.put(f"{base}/users/{u}", auth=auth, json={"password": p, "tags": ""}, timeout=15)
    results.append({"module":code, "step":"user", "status": r.status_code})

    # 2) Permissions on vhost
    # configure: allow declare the exchange; write/read: allow the exchange
    perm = {"configure": f"^{EXCHANGE_NAME}$", "write": f"^{EXCHANGE_NAME}$", "read": f"^{EXCHANGE_NAME}$"}
    r = requests.put(f"{base}/permissions/{BROKER_VHOST}/{u}", auth=auth, json=perm, timeout=15)
    results.append({"module":code, "step":"permissions", "status": r.status_code})

    # 3) Topic-permissions (if plugin available)
    pol = _policy_for_module(code)
    tp = {"exchange": EXCHANGE_NAME, "write": _topic_regex(pol["publish"]), "read": _topic_regex(pol["consume"])}
    r = requests.put(f"{base}/topic-permissions/{BROKER_VHOST}/{u}", auth=auth, json=tp, timeout=15)
    results.append({"module":code, "step":"topic-permissions", "status": r.status_code})

  return jsonify({"ok": True, "results": results})

# ---------- Entry ----------
if __name__ == "__main__":
  app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
