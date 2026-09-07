"""Background worker process for Shelf.

Run as ``python -m shelf.worker``. One worker pod per cluster is
typically enough; horizontal scaling falls out for free because the
JetStream consumer is durable + queue-grouped, so adding pods just
spreads the same cursor.
"""
