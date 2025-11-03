#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Cliente de PRUEBA para el Módulo 2 (Reconocimiento de Patentes).

- Obtiene credenciales y política con /auth/credentials usando el token de M2.
- CONSUME: lane.passage.detected, evidence.image.captured
- PUBLICA: plate.recognition.result y log.app

Uso:
  python m2_client.py --service https://tolling-service-bus.onrender.com --token tok-M2-TEST-34cd consume
  python m2_client.py --service https://tolling-service-bus.onrender.com --token tok-M2-TEST-34cd publish \
         --event plate.recognition.result --event-id EVT-DEMO-1 --plate ABC123 --confidence 0.99
"""

import os
import sys
import json
import time
import signal
import argparse
from typing import Dict, Any, List

import requests
import pika

DEFAULT_SERVICE = "https://tolling-service-bus.onrender.com"
DEFAULT_TOKEN   = "tok-M2-TEST-34cd"

# ---------- Credenciales ----------
def get_credentials(service_url: str, token: str) -> Dict[str, Any]:
    url = service_url.rstrip("/") + "/auth/credentials"
    r = requests.post(url, json={"token": token}, timeout=20)
    if r.status_code != 200:
        raise SystemExit(f"ERROR {r.status_code} al pedir credenciales: {r.text}")
    data = r.json()
    # Validaciones mínimas
    for req_key in ("amqp_url", "exchange", "policy"):
        if req_key not in data:
            raise SystemExit(f"Respuesta inválida (falta {req_key}): {data}")
    return data

# ---------- Publisher ----------
def publish_message(amqp_url: str, exchange: str, routing_key: str, payload: Dict[str, Any]) -> None:
    params = pika.URLParameters(amqp_url)
    params.heartbeat = 30
    conn = pika.BlockingConnection(params)
    ch = conn.channel()
    ch.exchange_declare(exchange=exchange, exchange_type="topic", durable=True)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ch.basic_publish(
        exchange=exchange,
        routing_key=routing_key,
        body=body,
        properties=pika.BasicProperties(content_type="application/json", delivery_mode=2),
    )
    print(f"[PUBLISH] rk={routing_key} body={payload}")
    conn.close()

def make_recognition_payload(event_id: str, plate: str, confidence: float) -> Dict[str, Any]:
    return {
        "event_id": event_id,
        "recognized": True,
        "plate": plate,
        "format": "mercosur",
        "confidence": float(confidence),
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

def make_log_payload(component: str, level: str, message: str, correlation_id: str = "") -> Dict[str, Any]:
    return {
        "component": component,
        "level": level,
        "message": message,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "correlation_id": correlation_id or None,
    }

# ---------- Consumer ----------
def consume_queues(amqp_url: str, exchange: str, queue_name: str, routing_keys: List[str]) -> None:
    params = pika.URLParameters(amqp_url)
    params.heartbeat = 30
    conn = pika.BlockingConnection(params)
    ch = conn.channel()
    ch.exchange_declare(exchange=exchange, exchange_type="topic", durable=True)
    ch.queue_declare(queue=queue_name, durable=True)
    for rk in routing_keys:
        ch.queue_bind(exchange=exchange, queue=queue_name, routing_key=rk)
    ch.basic_qos(prefetch_count=50)

    print(f"[CONSUME] queue={queue_name} binds={routing_keys}")

    def on_msg(ch_, method, props, body):
        try:
            txt = body.decode("utf-8", errors="replace")
            print(f"[MSG] rk={method.routing_key} body={txt}")
        except Exception as e:
            print(f"[ERR] decode: {e}")
        finally:
            ch_.basic_ack(method.delivery_tag)

    ch.basic_consume(queue=queue_name, on_message_callback=on_msg)

    # Manejo de Ctrl+C limpio
    def _graceful(_sig, _frm):
        try:
            ch.stop_consuming()
        except Exception:
            pass
    signal.signal(signal.SIGINT, _graceful)
    signal.signal(signal.SIGTERM, _graceful)

    try:
        ch.start_consuming()
    finally:
        try:
            conn.close()
        except Exception:
            pass

# ---------- CLI ----------
def main():
    ap = argparse.ArgumentParser(description="M2 test client (Rabbit)")
    ap.add_argument("--service", default=os.getenv("SERVICE_URL", DEFAULT_SERVICE), help="URL del service bus")
    ap.add_argument("--token", default=os.getenv("MODULE_TOKEN", DEFAULT_TOKEN), help="Token del módulo 2")
    sub = ap.add_subparsers(dest="cmd", required=True)

    # consume
    ap_cons = sub.add_parser("consume", help="Arranca el consumidor (M2)")
    ap_cons.add_argument("--queue", default=os.getenv("M2_QUEUE", "m2.q"), help="Nombre de la cola durable")

    # publish
    ap_pub = sub.add_parser("publish", help="Publica un evento permitido por M2")
    ap_pub.add_argument("--event", choices=["plate.recognition.result", "log.app"], default="plate.recognition.result")
    ap_pub.add_argument("--event-id", default="EVT-DEMO-1")
    ap_pub.add_argument("--plate", default="ABC123")
    ap_pub.add_argument("--confidence", type=float, default=0.98)
    ap_pub.add_argument("--log-level", default="info")
    ap_pub.add_argument("--log-message", default="demo from M2")
    ap_pub.add_argument("--correlation-id", default="")

    args = ap.parse_args()

    # 1) Obtener credenciales + policy
    creds = get_credentials(args.service, args.token)
    amqp_url = creds["amqp_url"]
    exchange = creds["exchange"]
    policy   = creds["policy"]
    queue_suggested = creds.get("queue_suggested") or "m2.q"

    # 2) Ejecutar
    if args.cmd == "consume":
        if not policy.get("consume"):
            raise SystemExit("Este módulo no tiene permisos de consumo configurados.")
        routing_keys = policy["consume"]
        qname = args.queue or queue_suggested
        consume_queues(amqp_url, exchange, qname, routing_keys)

    elif args.cmd == "publish":
        allowed = policy.get("publish", [])
        # Determinar rk y payload según --event
        if args.event == "plate.recognition.result":
            rk = "plate.recognition.result"
            if rk not in allowed:
                raise SystemExit(f"Routing key no permitida para publicar: {rk}")
            payload = make_recognition_payload(args.event_id, args.plate, args.confidence)
            publish_message(amqp_url, exchange, rk, payload)
        elif args.event == "log.app":
            rk = "log.app"
            if rk not in allowed:
                raise SystemExit(f"Routing key no permitida para publicar: {rk}")
            payload = make_log_payload("recognition", args.log_level, args.log_message, args.correlation_id)
            publish_message(amqp_url, exchange, rk, payload)
        else:
            raise SystemExit("Evento no soportado.")

if __name__ == "__main__":
    main()
