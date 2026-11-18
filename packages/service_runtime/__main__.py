# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Explicit operator CLI. No secrets, inputs or exception traceback in output."""
import argparse
import json
import sys
from . import RuntimeFailure, load_config, initialize, check, backup, restore, verify_backup


class Parser(argparse.ArgumentParser):
    def error(self, message): raise RuntimeFailure('invalid_arguments')


def main(argv=None):
    try:
        parser = Parser(description='Single-host installation tooling; real login stays disabled.')
        parser.add_argument('--config', required=True)
        sub = parser.add_subparsers(dest='command', required=True, parser_class=Parser)
        sub.add_parser('validate-config')
        initializer = sub.add_parser('init')
        initializer.add_argument('--layout', type=int, choices=(1, 2), default=1)
        checker = sub.add_parser('check'); checker.add_argument('--require-production', action='store_true')
        for name in ('backup','restore','verify-backup'):
            sub.add_parser(name).add_argument('--directory', required=True)
        serve = sub.add_parser('serve'); serve.add_argument('--certfile', required=True); serve.add_argument('--keyfile', required=True)
        serve.add_argument('--port', type=int, required=True)
        args = parser.parse_args(argv)
        if args.command == 'validate-config':
            load_config(args.config); result = {'configuration_valid':True,'login_enabled':False,'production_ready':False}
        elif args.command == 'init': result = initialize(args.config, layout=args.layout)
        elif args.command == 'check':
            result = check(args.config)
            if args.require_production:
                print(json.dumps({'error':'production_acceptance_required','installation_valid':True,'login_enabled':False})); return 3
        elif args.command == 'backup': result = backup(args.config, args.directory)
        elif args.command == 'restore': result = restore(args.config, args.directory)
        elif args.command == 'verify-backup': result = verify_backup(args.config, args.directory)
        else:
            from .server import run_server
            run_server(args.config, args.certfile, args.keyfile, args.port)
            return 0
        print(json.dumps(result, sort_keys=True)); return 0
    except KeyboardInterrupt:
        print('{"error":"interrupted"}'); return 130
    except RuntimeFailure as error:
        print(json.dumps({'error':error.code})); return 2
    except Exception:
        print('{"error":"runtime_unavailable"}'); return 2


if __name__ == '__main__': sys.exit(main())
