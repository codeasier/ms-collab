import argparse
from .app import create_server


def main():
    parser = argparse.ArgumentParser(description="Run the synthetic, loopback-only demo (not production).")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    with create_server(args.port) as server:
        print(f"Synthetic demo only: http://127.0.0.1:{server.server_port}", flush=True)
        print("No real identity, credentials, data or production isolation. Ctrl+C to stop.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
