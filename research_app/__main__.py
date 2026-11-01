import argparse
import os
from pathlib import Path
from .server import create_server


def main():
    parser = argparse.ArgumentParser()
    # Flags override environment variables (which the systemd unit sets), which override defaults.
    env = os.environ.get
    parser.add_argument('--port', type=int, default=int(env('RESEARCH_APP_PORT') or 8765))
    parser.add_argument('--data', type=Path,
                        default=Path(env('RESEARCH_APP_DATA') or Path.home() / 'research-data'))
    parser.add_argument('--host', default=env('RESEARCH_APP_HOST') or '127.0.0.1',
                        help='interface to listen on; 0.0.0.0 for all (the app has no login)')
    parser.add_argument('--allow-host', action='append',
                        default=(env('RESEARCH_APP_ALLOWED_HOSTS') or '').split(), metavar='NAME',
                        help='extra hostname or IP the browser may use (repeatable)')
    args = parser.parse_args()
    server = create_server(args.data, host=args.host, port=args.port, allowed_hosts=args.allow_host)
    for name in ['127.0.0.1', *args.allow_host] if args.host != '127.0.0.1' else ['127.0.0.1']:
        print(f'Research App at http://{name}:{server.server_port}', flush=True)
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
