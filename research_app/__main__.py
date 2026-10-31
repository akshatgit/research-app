import argparse
from pathlib import Path
from .server import create_server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data', type=Path, default=Path.home() / 'research-data')
    args = parser.parse_args()
    server = create_server(args.data, port=args.port)
    print(f'Research App at http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        server.service.shutdown()


if __name__ == '__main__':
    main()
