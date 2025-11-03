# worker.py
import os, json, time, logging
from typing import List
import pika

CFG_PATH = os.environ.get("BUS_CONFIG", "config.json")
CFG = json.load(open(CFG_PATH, "r", encoding="utf-8"))
if os.environ.get("RABBIT_URL"):
    CFG["rabbitmq"]["url"] = os.environ["RABBIT_URL"]

RAB = CFG["rabbitmq"]
TOPO = CFG["topology"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

def _params():
    p = pika.URLParameters(RAB["url"])
    p.heartbeat = int(RAB.get("heartbeat", 30))
    p.blocked_connection_timeout = int(RAB.get("blocked_timeout", 60))
    return p

def _ensure_topology(ch):
    ch.exchange_declare(exchange=TOPO["dlx"], exchange_type="fanout", durable=True)
    ch.exchange_declare(exchange=RAB["exchange"], exchange_type=RAB["exchange_type"], durable=True)
    ttl = int(TOPO.get("ttl_ms", 604800000))
    for q in TOPO["queues"]:
        args = {"x-dead-letter-exchange": TOPO["dlx"], "x-message-ttl": ttl}
        ch.queue_declare(queue=q["name"], durable=True, arguments=args)
        for rk in q["bindings"]:
            ch.queue_bind(queue=q["name"], exchange=RAB["exchange"], routing_key=rk)
        dlq = f"{q['name']}.dlq"
        ch.queue_declare(queue=dlq, durable=True)
        ch.queue_bind(queue=dlq, exchange=TOPO["dlx"], routing_key="")

def run_consumer(queue_names: List[str]):
    backoff = 1.0
    while True:
        try:
            logging.info("Connecting to Rabbit...")
            conn = pika.BlockingConnection(_params())
            ch = conn.channel()
            ch.basic_qos(prefetch_count=100)
            _ensure_topology(ch)

            def on_msg(ch, method, props, body):
                try:
                    logging.info("[%s] %s", method.routing_key, body.decode("utf-8")[:2000])
                    ch.basic_ack(method.delivery_tag)
                except Exception as e:
                    logging.exception("Handler error: %s", e)
                    ch.basic_nack(method.delivery_tag, requeue=False)  # DLQ

            for q in queue_names:
                ch.basic_consume(queue=q, on_message_callback=on_msg)
                logging.info("Consuming queue=%s", q)

            backoff = 1.0
            ch.start_consuming()
        except KeyboardInterrupt:
            break
        except Exception as e:
            logging.warning("Consumer disconnected: %s", e)
            time.sleep(min(backoff, 30))
            backoff *= 2

if __name__ == "__main__":
    qs = [q["name"] for q in TOPO["queues"]]
    env_qs = os.environ.get("BUS_QUEUES")
    if env_qs:
        qs = [x.strip() for x in env_qs.split(",") if x.strip()]
    run_consumer(qs)
