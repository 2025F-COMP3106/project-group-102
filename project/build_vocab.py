"""Build a unified vocabulary from multiple parsed replay JSON files.

This script scans a folder of parsed JSON files, builds a unified vocabulary
of all tokens, and saves the mappings. Later, vectorize_states.py can use
this pre-built vocabulary to ensure consistent token IDs across all files.

Usage:
  python build_vocab.py --json-folder replays/ --out-prefix vocab/shared
  
This creates:
  - vocab/shared_token2id.json
  - vocab/shared_id2token.json
  - vocab/shared_vocab_species.json, vocab/shared_species2id.json, etc.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Dict, List, Set, Any
import concurrent.futures

# default import (non-optimized); we will import optimized variant at runtime if requested
from vectorize_states import build_vocabs, state_to_feature_tokens


def load_json(path: str) -> Any:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def build_global_token_vocab(json_folder: str, use_optimized: bool = False, workers: int = 4) -> Dict[str, int]:
    """Scan all JSON files in folder and build a unified token vocab using threads.

    If `use_optimized` is True, the optimized `state_to_feature_tokens_optimized` will be used.
    """
    all_tokens = set()

    # Find all JSON files
    json_files = sorted(Path(json_folder).glob('*.json'))
    print(f"Found {len(json_files)} JSON files")

    # choose token extraction function
    if use_optimized:
        try:
            from vectorize_states_optimized import state_to_feature_tokens_optimized as state_to_features_fn
        except Exception:
            print("Warning: failed to import vectorize_states_optimized; falling back to default state_to_feature_tokens")
            state_to_features_fn = state_to_feature_tokens
    else:
        state_to_features_fn = state_to_feature_tokens

    def process_file(path: Path) -> Set[str]:
        toks = set()
        try:
            parsed = load_json(str(path))
            if not isinstance(parsed, list):
                return toks
            for state in parsed:
                try:
                    feats = state_to_features_fn(state)
                    toks.update(feats.keys())
                except Exception:
                    # skip problematic state
                    continue
        except Exception:
            return toks
        return toks

    # Use a ThreadPoolExecutor for simpler cross-platform behavior (no pickling)
    max_workers = max(1, min(workers, (os.cpu_count() or 4)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(process_file, p): p for p in json_files}
        for fut in concurrent.futures.as_completed(futures):
            path = futures[fut]
            try:
                toks = fut.result()
                all_tokens.update(toks)
                print(f"  Scanned {path.name}: +{len(toks)} tokens (total {len(all_tokens)})")
            except Exception as e:
                print(f"  Error scanning {path.name}: {e}")

    # Build token2id mapping (sorted for determinism)
    sorted_tokens = sorted(list(all_tokens))
    token2id = {tok: i for i, tok in enumerate(sorted_tokens)}

    print(f"\nTotal unique tokens: {len(token2id)}")
    return token2id


def build_global_group_vocabs(json_folder: str, workers: int = 4) -> Dict[str, Dict[str, int]]:
    """Scan all JSON files and build unified per-group vocabularies concurrently.

    Returns mapping of group_name -> {value: id}
    """
    # Collect all values per group
    all_vocabs = {
        'species': set(),
        'moves': set(),
        'types': set(),
        'items': set(),
        'status_effects': set(),
        'volatile_effects': set(),
        'field_effects': set(),
        'hazards': set(),
    }

    json_files = sorted(Path(json_folder).glob('*.json'))

    def process_file_groups(path: Path) -> Dict[str, Set[str]]:
        try:
            parsed = load_json(str(path))
            if not isinstance(parsed, list):
                return {}
            return build_vocabs(parsed)
        except Exception:
            return {}

    max_workers = max(1, min(workers, (os.cpu_count() or 4)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(process_file_groups, p): p for p in json_files}
        for fut in concurrent.futures.as_completed(futures):
            path = futures[fut]
            try:
                group_vocabs = fut.result()
                if not group_vocabs:
                    continue
                for group_name in all_vocabs.keys():
                    all_vocabs[group_name].update(group_vocabs.get(group_name, set()))
                print(f"  Processed {path.name}")
            except Exception as e:
                print(f"  Error processing {path.name}: {e}")

    # Build id mappings (sorted for determinism)
    result = {}
    for group_name, values in all_vocabs.items():
        sorted_vals = sorted(list(values))
        val2id = {v: i for i, v in enumerate(sorted_vals)}
        result[group_name] = val2id

    return result


def main() -> None:
    p = argparse.ArgumentParser(
        description="Build unified vocabulary from multiple parsed JSON files"
    )
    p.add_argument('--json-folder', required=True, help='folder containing *.json files')
    p.add_argument('--out-prefix', default='vocab/shared', help='output prefix for vocab files')
    p.add_argument('--use-optimized', action='store_true', help='use optimized, slot-agnostic tokenization')
    p.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 4)), help='number of worker threads to use')
    args = p.parse_args()
    
    if not os.path.isdir(args.json_folder):
        raise ValueError(f"Folder not found: {args.json_folder}")
    
    print("="*80)
    print("BUILDING GLOBAL VOCABULARY")
    print("="*80)
    
    # Build token vocab
    print("\n[1] Building token vocabulary...")
    token2id = build_global_token_vocab(args.json_folder, use_optimized=args.use_optimized, workers=args.workers)
    id2token = {v: k for k, v in token2id.items()}
    
    # Build per-group vocabs
    print("\n[2] Building per-group vocabularies...")
    group_vocabs = build_global_group_vocabs(args.json_folder, workers=args.workers)
    for group_name, val2id in group_vocabs.items():
        print(f"  {group_name}: {len(val2id)} values")
    
    # Save outputs
    print("\n[3] Saving vocabulary files...")
    out_prefix = args.out_prefix
    os.makedirs(os.path.dirname(out_prefix) or '.', exist_ok=True)
    
    # Save token vocab
    with open(out_prefix + '_token2id.json', 'w', encoding='utf-8') as f:
        json.dump(token2id, f)
    with open(out_prefix + '_id2token.json', 'w', encoding='utf-8') as f:
        json.dump({str(k): v for k, v in id2token.items()}, f)
    
    # Save per-group vocabs
    for group_name, val2id in group_vocabs.items():
        sorted_vals = sorted(list(val2id.keys()))
        id2val = {i: v for v, i in val2id.items()}
        
        with open(out_prefix + f'_vocab_{group_name}.json', 'w', encoding='utf-8') as f:
            json.dump(sorted_vals, f)
        with open(out_prefix + f'_{group_name}2id.json', 'w', encoding='utf-8') as f:
            json.dump(val2id, f)
        with open(out_prefix + f'_id2{group_name}.json', 'w', encoding='utf-8') as f:
            json.dump({str(k): v for k, v in id2val.items()}, f)
    
    print(f"\nVocab saved to {out_prefix}_*.json")
    print(f"Total tokens: {len(token2id)}")
    print(f"Token vocab ready for vectorization!")


if __name__ == '__main__':
    main()
