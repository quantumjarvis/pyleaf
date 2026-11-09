"""bank — pyleaf M3 example: secured money transfers with transactions.

Run ``python -m bank`` from ``examples/bank/`` (or anywhere with that
directory on ``PYTHONPATH``); the app serves via uvicorn on
``pyleaf.web.host``/``pyleaf.web.port``.

The example exercises the M3 feature set end to end:

- ``@Transactional`` boundaries with a mid-way failure that must roll
  back a partially-applied transfer;
- ``Propagation.REQUIRES_NEW`` audit rows that commit independently of
  the transfer outcome;
- ``@TransactionalEventListener(AFTER_COMMIT)`` exactly-once
  notifications through a stub mailer;
- ``CrudRepository`` beans joining the ambient transaction;
- JWT bearer authentication plus ``@Secured`` method security;
- actuator endpoints protected by the built-in ACTUATOR-role rule.
"""
