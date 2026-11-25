import argparse, sys
from pathlib import Path
from engine.parser import parse_file
from engine.runtime import Runtime
from engine.errors import TGLError

def main():
    ap=argparse.ArgumentParser(prog='tgl',description='Reidi TGL — TGL Engine')
    sub=ap.add_subparsers(dest='action',required=True)
    for action in ('run','check'):
        p=sub.add_parser(action);p.add_argument('file')
    args=ap.parse_args()
    try:
        path=Path(args.file).expanduser().resolve()
        program=parse_file(path)
        print(f'[TGL] Archivo: {path}')
        print(f'[TGL] Comandos: {len(program.commands)} | Constantes: {len(program.constants)} | Eventos: {len(program.events)}')
        if args.action=='check': print('[TGL] Sintaxis correcta.');return 0
        Runtime(program).run();return 0
    except (TGLError,OSError,ValueError) as e:
        print(f'[TGL ERROR] {e}',file=sys.stderr);return 1
if __name__=='__main__':raise SystemExit(main())
