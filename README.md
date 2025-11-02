# Tolling RabbitMQ Bus

## Qué va al BUS (realtime)
- `transit.recorded`, `plate.captured`, `toll.status.updated` (operación en vivo).
- `payment.recorded`, `prepaid.balance.updated`, `debt.created`, `debt.settled` (impacto financiero inmediato).
- `fine.issued` (notificación de multa en el momento).
- `rate.updated`, `vehicle.category.changed` (afecta cálculo de peaje online).
- `customer.upserted`, `vehicle.upserted` (sincronización ligera a otros módulos).
- `audit.logged` (observabilidad transversal).

Lo demás va por la **API** y base de datos (consultas, reportes históricos, ABM no urgente).

## Requisitos
- Python 3.11
- RabbitMQ o CloudAMQP (URL en `config.json`).

## Setup
```bash
python -m venv .venv
. .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export BUS_CONFIG=./config.json
