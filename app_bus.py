# app_bus.py
import os, json
from typing import Dict, Any, Tuple
from flask import Flask, request, jsonify, redirect
from flask_cors import CORS
import pika
from jsonschema import validate, ValidationError
from schemas import ENVELOPE as ENVELOPE_SCHEMA, SCHEMAS

# ============== Config ==============
CFG_PATH = os.environ.get("BUS_CONFIG", "config.json")
with open(CFG_PATH, "r", encoding="utf-8") as f:
    CFG = json.load(f)

RAB = CFG["rabbitmq"]
TOPO = CFG["topology"]

# ============== Rabbit helpers ==============
def _params() -> pika.URLParameters:
    p = pika.URLParameters(RAB["url"])
    p.heartbeat = int(RAB.get("heartbeat", 30))
    p.blocked_connection_timeout = int(RAB.get("blocked_timeout", 60))
    return p

def rabbit_connect() -> pika.BlockingConnection:
    return pika.BlockingConnection(_params())

def declare_topology() -> None:
    conn = rabbit_connect()
    ch = conn.channel()

    # DLX
    ch.exchange_declare(exchange=TOPO["dlx"], exchange_type="fanout", durable=True)

    # Exchange principal
    ch.exchange_declare(exchange=RAB["exchange"], exchange_type=RAB["exchange_type"], durable=True)

    # Queues y bindings
    ttl = int(TOPO.get("ttl_ms", 604800000))
    for q in TOPO["queues"]:
        args = {"x-dead-letter-exchange": TOPO["dlx"], "x-message-ttl": ttl}
        ch.queue_declare(queue=q["name"], durable=True, arguments=args)
        for rk in q["bindings"]:
            ch.queue_bind(queue=q["name"], exchange=RAB["exchange"], routing_key=rk)
        # DLQ por cola
        dlq = f"{q['name']}.dlq"
        ch.queue_declare(queue=dlq, durable=True)
        ch.queue_bind(queue=dlq, exchange=TOPO["dlx"], routing_key="")
    conn.close()

def validate_envelope(payload: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    validate(payload, ENVELOPE_SCHEMA)
    event = payload["event"]
    schema = SCHEMAS.get(event)
    if not schema:
        raise ValidationError(f"Schema no registrado para event='{event}'")
    validate(payload["data"], schema)
    return event, payload["data"]

def publish(routing_key: str, envelope: Dict[str, Any]) -> None:
    # Valida y publica persistente
    validate_envelope(envelope)
    body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
    conn = rabbit_connect()
    ch = conn.channel()
    ch.basic_publish(
        exchange=RAB["exchange"],
        routing_key=routing_key,
        body=body,
        properties=pika.BasicProperties(content_type="application/json", delivery_mode=2)
    )
    conn.close()

# Declarar topología al iniciar
declare_topology()

# ============== HTTP Service ==============
app = Flask(__name__)
CORS(app, resources={r"*": {"origins": CFG["server"].get("cors_allow_origins", ["*"])}})

@app.get("/")
def root():
    return redirect("/docs", code=302)

@app.get("/health")
def health():
    try:
        c = rabbit_connect(); c.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.get("/schemas")
def list_schemas():
    return jsonify({"events": list(SCHEMAS.keys()), "envelope": ENVELOPE_SCHEMA})

@app.post("/bus/publish")
def http_publish():
    """
    Publica un mensaje en el bus.
    Body:
    {
      "routing_key": "transit.recorded",       # si falta, se usa envelope.event
      "message": {
        "event": "transit.recorded",
        "version": "1.0",
        "data": {...},                         # según schemas.py
        "meta": {"occurred_at":"...","producer":"moduleX","correlation_id":"..."}
      }
    }
    """
    body = request.get_json(silent=True) or {}
    msg = body.get("message")
    if not isinstance(msg, dict):
        return jsonify({"error": "message requerido"}), 400
    try:
        event, _ = validate_envelope(msg)
        rk = body.get("routing_key") or event
        publish(rk, msg)
        return jsonify({"published": True, "routing_key": rk, "event": event})
    except ValidationError as ve:
        return jsonify({"error": "validation_error", "message": str(ve)}), 400
    except Exception as e:
        return jsonify({"error": "publish_failed", "message": str(e)}), 500

# ============== Swagger mínimo ==============
@app.get("/openapi.json")
def openapi_json():
    spec = {
        "openapi": "3.0.3",
        "info": {"title": "Tolling RabbitMQ Bus", "version": "1.0.0"},
        "paths": {
            "/health": { "get": {"summary":"Health", "responses":{"200":{"description":"OK"}}}},
            "/schemas": { "get": {"summary":"List schemas", "responses":{"200":{"description":"OK"}}}},
            "/bus/publish": {
                "post": {
                    "summary": "Publish",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {"type":"object"}}}},
                    "responses": {"200":{"description":"Published"},"400":{"description":"Validation error"}}
                }
            }
        }
    }
    return jsonify(spec)

@app.get("/docs")
def swagger_ui():
    return """<!doctype html><html><head>
<meta charset="utf-8"/><title>Bus Docs</title>
<link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
</head><body><div id="swagger-ui"></div>
<script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script>window.ui=SwaggerUIBundle({url:'/openapi.json',dom_id:'#swagger-ui'});</script>
</body></html>"""

if __name__ == "__main__":
    app.run(host=CFG["server"]["host"], port=int(CFG["server"]["port"]), debug=bool(CFG["server"]["debug"]))
