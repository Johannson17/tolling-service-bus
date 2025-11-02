# publisher.py
# CLI para publicar un envelope validado contra schemas.py
import os, json, sys, argparse
import pika
from jsonschema import validate, ValidationError
from schemas import ENVELOPE as ENVELOPE_SCHEMA, SCHEMAS

def load_cfg():
    with open(os.environ.get("BUS_CONFIG","config.json"), "r", encoding="utf-8") as f:
        return json.load(f)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--routing-key", help="topic; por defecto se usa event del envelope")
    ap.add_argument("--file", help="ruta a JSON con envelope")
    ap.add_argument("--stdin", action="store_true", help="leer envelope por stdin")
    args = ap.parse_args()

    if not args.file and not args.stdin:
        ap.error("use --file o --stdin")

    envelope = json.load(sys.stdin) if args.stdin else json.load(open(args.file,"r",encoding="utf-8"))

    # Validación
    validate(envelope, ENVELOPE_SCHEMA)
    evt = envelope["event"]
    schema = SCHEMAS.get(evt)
    if not schema:
        raise SystemExit(f"Schema no registrado: {evt}")
    validate(envelope["data"], schema)

    cfg = load_cfg()
    params = pika.URLParameters(cfg["rabbitmq"]["url"])
    conn = pika.BlockingConnection(params)
    ch = conn.channel()
    rk = args.routing_key or evt
    ch.basic_publish(
        exchange=cfg["rabbitmq"]["exchange"],
        routing_key=rk,
        body=json.dumps(envelope).encode("utf-8"),
        properties=pika.BasicProperties(content_type="application/json", delivery_mode=2)
    )
    conn.close()
    print(f"OK: {rk}")

if __name__ == "__main__":
    main()
