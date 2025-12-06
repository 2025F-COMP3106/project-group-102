"""
Batch runner for parser.py
Usage:
  python parse_batch.py --input-dir "C:/Users/admin/Code/COMP3106/data/replays" --output-dir "C:/Users/admin/Code/COMP3106/data/parsed" --workers 6 --moves-data "C:/Users/admin/Code/COMP3106/data/gen9_moves.json" --pokemon-data "C:/Users/admin/Code/COMP3106/data/gen9_pokemon.json"
"""
import os
import argparse
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
PARSER_SCRIPT = PROJECT_DIR / 'parser.py'


def parse_one(input_path: Path, output_path: Path, env_extra=None):
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    env['PARSER_INPUT'] = str(input_path)
    env['PARSER_OUTPUT'] = str(output_path)

    cmd = [env.get('PYTHON', 'python'), str(PARSER_SCRIPT)]
    try:
        result = subprocess.run(cmd, env=env, cwd=str(PROJECT_DIR), capture_output=True, text=True, timeout=300)
        return (input_path.name, result.returncode, result.stdout, result.stderr)
    except Exception as e:
        return (input_path.name, -1, '', str(e))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--input-dir', '-i', required=True)
    p.add_argument('--output-dir', '-o', required=True)
    p.add_argument('--workers', '-w', type=int, default=min(8, os.cpu_count() or 4))
    p.add_argument('--recursive', action='store_true')
    p.add_argument('--moves-data', help='Path to gen9_moves.json')
    p.add_argument('--pokemon-data', help='Path to gen9_pokemon.json')
    p.add_argument('--max-files', type=int, default=None)
    p.add_argument('--offset', type=int, default=0)
    args = p.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Prepare extra environment variables
    env_extra = {}
    if args.moves_data:
        env_extra['PARSER_MOVES_DATA'] = args.moves_data
    if args.pokemon_data:
        env_extra['PARSER_POKEMON_DATA'] = args.pokemon_data
        

    pattern = "**/*.json" if args.recursive else "*.json"
    files = sorted(input_dir.glob(pattern))
    if not files:
        print('No json files found in', input_dir)
        return

    print(f'Found {len(files)} files, parsing with {args.workers} workers')
    
    files = sorted(input_dir.glob(pattern))
    if args.offset:
        files = files[args.offset:]
    if args.max_files is not None:
        files = files[:args.max_files]

    futures = []
    count = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for f in files:
            out_name = f.stem + '.parsed.json'
            out_path = output_dir / out_name
            futures.append(ex.submit(parse_one, f, out_path, env_extra))

        for fut in as_completed(futures):
            count+=1
            name, code, out, err = fut.result()
            status = 'OK' if code == 0 else f'ERR({code})'
            if count%1000==0:
                print(f'{name}: {status}: count: {count}')
            if out:
                print('stdout:', out.strip())
            if err:
                print('stderr:', err.strip())


if __name__ == '__main__':
    main()
