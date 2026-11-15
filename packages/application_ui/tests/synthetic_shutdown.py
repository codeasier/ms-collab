"""Test-fixture lifecycle only; never imported by the product application."""
import signal
from threading import Thread


def serve_synthetic(server, *, on_ready=None):
    """Stop a synthetic WSGI loop without throwing inside an active request.

    WSGI's request handler catches BaseException, including SystemExit. Raising
    from a signal handler can therefore leave the fixture running indefinitely.
    BaseServer.shutdown must run off the serving thread to avoid deadlock.
    """
    shutdown = None

    def request_stop(*_):
        nonlocal shutdown
        if shutdown is None:
            shutdown = Thread(target=server.shutdown, name='synthetic-shutdown', daemon=True)
            shutdown.start()

    previous = signal.signal(signal.SIGTERM, request_stop)
    try:
        if on_ready is not None:
            on_ready()
        server.serve_forever(poll_interval=0.05)
    finally:
        signal.signal(signal.SIGTERM, previous)
        server.server_close()
        if shutdown is not None:
            shutdown.join(timeout=1)
            if shutdown.is_alive():
                raise RuntimeError('Synthetic server shutdown did not finish')
