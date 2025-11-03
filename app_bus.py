#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Orquestador del BUS:
- SIN endpoints de publicación ni sandbox.
- Sólo documentación (/docs), health y schemas.
- (Opcional) Validador de eventos como worker: `python app_bus.py worker`.

Requisitos:
pip install flask flask-cors pika jsonschema

Procfile (web):
  web: gunicorn -w 2 -b 0.0.0.0:$PORT app_bus:app
(Worker opcional – si lo usás en otra app/process):
  worker: python app_bus.py worker
"""
import os, json, threading, time, html
from typing import Dict, Any
from flask import Flask, jsonify, redirect
from flask_cors import CORS
import pika
from jsonschema import validate, ValidationError

# =========================
# Config
# =========================
CFG_PATH = os.environ.get("BUS_CONFIG", "config.json")
CFG = json.load(open(CFG_PATH, "r", encoding="utf-8"))

RAB = CFG["rabbit"]
HOST  = RAB["host"]
VHOST = RAB["vhost"]
EX    = RAB["exchange"]
EX_T  = RAB["exchange_type"]
DLX   = RAB["dlx"]
TTL   = int(RAB.get("ttl_ms", 604800000))

# AMQP URL para el validador (admin o cuenta con permisos de lectura sobre '#')
# Si tenés usuario/clave admin en config.json, construimos la URL automáticamente.
ADMIN_USER = RAB.get("admin_user")
ADMIN_PASS = RAB.get("admin_pass")
DEFAULT_AMQP_URL = f"amqps://{ADMIN_USER}:{ADMIN_PASS}@{HOST}/{VHOST}" if (ADMIN_USER and ADMIN_PASS) else os.environ.get("RABBIT_URL", "")

# =========================
# Esquemas (envelope + payloads)
# =========================
ENVELOPE_SCHEMA = {
  "type": "object",
  "required": ["event", "version", "data", "meta"],
  "properties": {
    "event":   {"type": "string"},
    "version": {"type": "string"},
    "data":    {"type": "object"},
    "meta": {
      "type": "object",
      "required": ["occurred_at", "producer"],
      "properties": {
        "occurred_at": {"type":"string","format":"date-time"},
        "producer":    {"type":"string"},
        "correlation_id": {"type":"string"},
        "causation_id":   {"type":"string"}
      },
      "additionalProperties": True
    }
  },
  "additionalProperties": False
}

# Tomamos de config.json los ejemplos como guía y definimos validaciones por evento.
EXAMPLES: Dict[str, Any] = CFG.get("examples", {})
SCHEMAS: Dict[str, Dict[str, Any]] = {
  "transit.recorded": {
    "type":"object",
    "required":["transit_id","toll_id","toll_name","lane","vehicle_id","vehicle_type","timestamp"],
    "properties":{
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

# =========================
# Validador (worker opcional)
# =========================
class Validator:
  def __init__(self, amqp_url: str):
    self.url = amqp_url
    self.conn = None
    self.ch   = None
    self.queue = "validator.q"

  def _params(self):
    p = pika.URLParameters(self.url)
    p.heartbeat = 30
    p.blocked_connection_timeout = 60
    return p

  def _ensure(self):
    if self.conn and self.conn.is_open and self.ch and self.ch.is_open:
      return
    self.conn = pika.BlockingConnection(self._params())
    self.ch   = self.conn.channel()
    # Topología pasiva/defensiva
    self.ch.exchange_declare(exchange=DLX, exchange_type="fanout", durable=True)
    self.ch.exchange_declare(exchange=EX,  exchange_type=EX_T,    durable=True)
    self.ch.queue_declare(queue=self.queue, durable=True,
                          arguments={"x-dead-letter-exchange": DLX, "x-message-ttl": TTL})
    # Captura todo para validar
    self.ch.queue_bind(queue=self.queue, exchange=EX, routing_key="#")
    self.ch.basic_qos(prefetch_count=100)

  def _audit(self, err: str, rk: str, raw: str):
    # Publica un evento de auditoría minimal (no bloquea si falla)
    try:
      evt = {
        "event":"audit.logged","version":"1.0",
        "data":{"event_id":"schema-error","event_type":"schema_error","timestamp":time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "toll_name":"validator","details":f"{err} rk={rk}", "vehicle_id":None},
        "meta":{"occurred_at":time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),"producer":"validator"}
      }
      self.ch.basic_publish(exchange=EX, routing_key="audit.logged",
                            body=json.dumps(evt).encode(),
                            properties=pika.BasicProperties(content_type="application/json", delivery_mode=2))
    except Exception:
      pass

  def _on_msg(self, ch, method, props, body):
    rk = method.routing_key or ""
    try:
      msg = json.loads(body.decode("utf-8"))
      validate(msg, ENVELOPE_SCHEMA)
      ev  = msg["event"]
      schema = SCHEMAS.get(ev)
      if not schema:
        raise ValidationError(f"schema_not_found:{ev}")
      validate(msg["data"], schema)
      ch.basic_ack(method.delivery_tag)
    except ValidationError as ve:
      self._audit(str(ve), rk, body[:200].decode("utf-8", "ignore"))
      ch.basic_nack(method.delivery_tag, requeue=False)  # va a DLX
    except Exception as e:
      self._audit(f"exception:{e}", rk, body[:200].decode("utf-8", "ignore"))
      ch.basic_nack(method.delivery_tag, requeue=False)

  def run(self):
    self._ensure()
    self.ch.basic_consume(queue=self.queue, on_message_callback=self._on_msg)
    self.ch.start_consuming()

# =========================
# Web (docs + health + schemas)
# =========================
app = Flask(__name__)
CORS(app, resources={r"*": {"origins": ["*"]}})

@app.get("/")
def root():
  return redirect("/docs", code=302)

@app.get("/health")
def health():
  ok = bool(DEFAULT_AMQP_URL)
  return jsonify({"ok": ok, "host": HOST, "vhost": VHOST, "exchange": EX})

@app.get("/schemas")
def schemas():
  return jsonify({"envelope": ENVELOPE_SCHEMA, "events": list(SCHEMAS.keys())})

def _code(s: str) -> str:
  return "<pre><code>" + html.escape(s) + "</code></pre>"

def _conn_line(user, pwd) -> str:
  return f"amqps://{user}:{pwd}@{HOST}/{VHOST}"

def _pub_snippet(user, pwd, example: Dict[str, Any]) -> str:
  return f"""# Python + pika
import pika, json
url = "{_conn_line(user,pwd)}"
params = pika.URLParameters(url); params.heartbeat=30
conn = pika.BlockingConnection(params); ch = conn.channel()
ch.exchange_declare(exchange="{EX}", exchange_type="{EX_T}", durable=True, passive=True)
msg = {json.dumps(example, indent=2)}
rk = msg["event"]
ch.basic_publish(exchange="{EX}", routing_key=rk,
                 body=json.dumps(msg).encode(),
                 properties=pika.BasicProperties(content_type="application/json", delivery_mode=2))
conn.close()"""

def _cons_snippet(user, pwd, queue: str) -> str:
  return f"""# Python + pika
import pika
url = "{_conn_line(user,pwd)}"
params = pika.URLParameters(url); params.heartbeat=30
conn = pika.BlockingConnection(params); ch = conn.channel()
ch.queue_declare(queue="{queue}", durable=True)
def cb(ch_, m, props, body):
    print(m.routing_key, body.decode()); ch_.basic_ack(m.delivery_tag)
ch.basic_qos(prefetch_count=50)
ch.basic_consume(queue="{queue}", on_message_callback=cb)
ch.start_consuming()"""

def _section_for(name: str, mod: Dict[str, Any]) -> str:
  u, p = mod["username"], mod["password"]
  q = mod["queue"]
  bindings = mod["bindings"]
  can_pub  = mod.get("can_publish", [])
  html_s = f'<div class="card"><h3>{name.upper()}</h3>'
  html_s += f'<div class="kv"><div>AMQP URL</div><div><code>{_conn_line(u,p)}</code></div></div>'
  html_s += f'<div class="kv"><div>Queue</div><div><code>{q}</code></div></div>'
  html_s += f'<div class="kv"><div>Bindings</div><div>' + ", ".join(f"<code>{b}</code>" for b in bindings) + "</div></div>"
  html_s += f'<div class="kv"><div>Puede publicar</div><div>' + (", ".join(f"<code>{e}</code>" for e in can_pub) if can_pub else "<span class=\"small\">—</span>") + "</div></div>"
  html_s += "<h4>Consumir</h4>" + _code(_cons_snippet(u,p,q))
  if can_pub:
    ev0 = EXAMPLES[can_pub[0]] if can_pub[0] in EXAMPLES else None
    if ev0:
      html_s += "<h4>Publicar (ejemplo)</h4>" + _code(_pub_snippet(u,p,ev0))
      html_s += "<h5>JSON</h5>" + _code(json.dumps(ev0, indent=2))
  html_s += "</div>"
  return html_s

@app.get("/docs")
def docs():
  CSS = """
  body{font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Arial,sans-serif;background:#0b1020;color:#e5e7eb;margin:0}
  a{color:#8ab4ff;text-decoration:none} a:hover{text-decoration:underline}
  .wrap{max-width:1100px;margin:0 auto;padding:36px}
  h1{margin:0 0 8px 0} h2{margin-top:28px;border-bottom:1px solid #1f2a44;padding-bottom:6px}
  .card{background:#0f1629;border:1px solid #1f2a44;border-radius:12px;padding:18px;margin:16px 0}
  pre{background:#0b1020;border:1px solid #1f2a44;border-radius:12px;padding:12px;overflow:auto}
  .small{font-size:12px;color:#9aa4bd}
  .kv{display:grid;grid-template-columns:180px 1fr;gap:8px}
  """
  parts = [f"""<!doctype html><html><head><meta charset="utf-8">
  <title>Tolling RabbitMQ Bus — Documentación</title>
  <style>{CSS}</style></head><body><div class="wrap">
  <h1>Tolling RabbitMQ Bus — Documentación</h1>
  <div class="small">RabbitMQ <code>{HOST}</code> | vhost <code>{VHOST}</code> | exchange <code>{EX}</code> ({EX_T})</div>
  <div class="card">
    <b>Reglas</b>
    <ul>
      <li>Cada módulo tiene usuario propio y cola dedicada.</li>
      <li>Publicación limitada por <i>topic-permissions</i> del broker (definido fuera de esta web).</li>
      <li>Lectura limitada a su cola y patrones asociados.</li>
      <li>Formato envelope: <code>event</code>, <code>version</code>, <code>data</code>, <code>meta</code>.</li>
    </ul>
  </div>
  <h2>Envelope</h2>
  {_code(json.dumps({"event":"<event-name>","version":"1.0","data":{"...":"..."},"meta":{"occurred_at":"<ISO-8601>","producer":"<module>","correlation_id":"<uuid>"}}, indent=2))}
  <h2>Módulos</h2>
  """]
  for name, mod in CFG["modules"].items():
    parts.append(_section_for(name, mod))
  parts.append("</div></body></html>")
  return "\n".join(parts), 200, {"Content-Type": "text/html; charset=utf-8"}

# =========================
# Entrypoints
# =========================
def run_worker():
  if not DEFAULT_AMQP_URL:
    print("[validator] ERROR: faltan credenciales AMQP admin. Seteá RABBIT_URL o admin_user/admin_pass en config.json")
    return
  v = Validator(DEFAULT_AMQP_URL)
  while True:
    try:
      v.run()
    except Exception as e:
      print("[validator] reiniciando por error:", e)
      time.sleep(3)

if __name__ == "__main__":
  mode = os.environ.get("MODE", "web")
  if mode == "worker":
    run_worker()
  else:
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
