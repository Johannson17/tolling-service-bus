# publisher.py
import os, json, sys, argparse
import pika
from jsonschema import validate
from schemas import ENVELOPE as ENVELOPE_SCHEMA, SCHEMAS

def load_cfg():
    path = os.environ.get("BUS_CONFIG","config.json")
    cfg = json.load(open(path,"r",encoding="utf-8"))
    if os.environ.get("RABBIT_URL"):
        cfg["rabbitmq"]["url"] = os.environ["RABBIT_URL"]
    return cfg

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="envelope JSON")
    ap.add_argument("--routing-key", help="si se omite, usa event del envelope")
    args = ap.parse_args()

    env = json.load(open(args.file,"r",encoding="utf-8"))
    validate(env, ENVELOPE_SCHEMA)
    evt = env["event"]
    validate(env["data"], SCHEMAS[evt])

    cfg = load_cfg()
    params = pika.URLParameters(cfg["rabbitmq"]["url"])
    conn = pika.BlockingConnection(params)
    ch = conn.channel()
    ch.confirm_delivery()
    unroutable = {"flag": False}
    def _on_return(ch, method, props, body): unroutable.update(flag=True)
    ch.add_on_return_callback(_on_return)

    rk = args.routing_key or evt
    ok = ch.basic_publish(cfg["rabbitmq"]["exchange"], rk, json.dumps(env).encode("utf-8"),
                          pika.BasicProperties(content_type="application/json", delivery_mode=2),
                          mandatory=True)
    conn.close()
    if not ok or unroutable["flag"]:
        print("PUBLISH FAILED", file=sys.stderr)
        sys.exit(1)
    print("OK", rk)

if __name__ == "__main__":
    main()
