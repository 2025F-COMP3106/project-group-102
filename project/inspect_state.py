"""Inspect a single state: show both vectorized form and readable token form.

Usage:
  python inspect_state.py --parsed china-gen9ou-12854442.parsed.json --state 0 --vector-size 128
  
This will:
1. Load the parsed JSON
2. Extract and vectorize the state at index `--state`
3. Show the hashed dense vector
4. Show the sparse token-row representation with readable token names
5. Show the original p2_next_move and labels
"""
from __future__ import annotations

import argparse
import json
from typing import Any

try:
    import numpy as np
except Exception:
    np = None

from vectorize_states import (
    vectorize_file,
    build_token_vocab_from_features,
    features_to_token_id_lists,
    extract_multihead_labels,
    build_vocabs,
    ACTION_TYPE_REVERSE,
    IGNORE_INDEX,
)
from reverse_vector import reverse_tokenrow_row


def load_json(path: str) -> Any:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def inspect_state(parsed_path: str, state_idx: int, vector_size: int = 128) -> None:
    """Load, vectorize, and inspect a single state."""
    
    # Vectorize
    X_hashed, extra = vectorize_file(parsed_path, vector_size=vector_size)
    all_feats = extra.get('all_feats', [])
    parsed = extra.get('parsed', [])
    
    if state_idx < 0 or state_idx >= len(parsed):
        raise IndexError(f"State index {state_idx} out of range (0..{len(parsed)-1})")
    
    # Build vocabs and labels
    token2id = build_token_vocab_from_features(all_feats)
    id2token = {v: k for k, v in token2id.items()}
    token_rows = features_to_token_id_lists(all_feats, token2id)
    
    # Optionally prefer pre-built shared vocabs so ids match training vocabs
    move2id = {}
    tera2id = {}
    species2id = {}
    # try to load pre-built vocabs from out-of-band files if present
    try:
        import os
        vp = os.environ.get('INSPECT_VOCAB_PREFIX')
        # if environment variable not set, fallback to None (will use local vocabs)
    except Exception:
        vp = None

    # build local group vocabs by default
    group_vocabs = build_vocabs(parsed)
    if vp:
        try:
            # load mapping files produced by build_vocab.py (val->id)
            import json
            for group in ['moves', 'types', 'species']:
                p = vp + f'_{group}2id.json'
                if os.path.exists(p):
                    with open(p, 'r', encoding='utf-8') as f:
                        locals()[f'{group}2id'] = json.load(f)
        except Exception:
            # fallback to local group vocabs
            vp = None

    if not vp:
        move2id = {v: i for i, v in enumerate(sorted(list(group_vocabs.get('moves', set()))))}
        tera2id = {v: i for i, v in enumerate(sorted(list(group_vocabs.get('types', set()))))}
        species2id = {v: i for i, v in enumerate(sorted(list(group_vocabs.get('species', set()))))}
    else:
        # if loaded from files, ensure keys are strings -> ints mapping
        move2id = {k: int(v) for k, v in (locals().get('moves2id') or {}).items()}
        tera2id = {k: int(v) for k, v in (locals().get('types2id') or {}).items()}
        species2id = {k: int(v) for k, v in (locals().get('species2id') or {}).items()}
    
    action_types, moves, teras, switches, faints = extract_multihead_labels(
        parsed, move2id, tera2id, species2id
    )
    
    # Get state data
    state = parsed[state_idx]
    token_row = token_rows[state_idx]
    hashed_vec = X_hashed[state_idx]
    
    # Print header
    print("=" * 100)
    print(f"STATE INSPECTION: {parsed_path} [Index {state_idx}]")
    print("=" * 100)
    
    # Original data
    print("\n[ORIGINAL DATA]")
    print(f"  p2_next_move: {state.get('p2_next_move')}")
    print(f"  winner: {state.get('winner')}")
    print(f"  avg_rating: {state.get('avg_rating')}")
    
    # Multi-head labels
    print("\n[MULTI-HEAD LABELS]")
    action_name = ACTION_TYPE_REVERSE.get(int(action_types[state_idx]), 'UNKNOWN')
    print(f"  action_type: {action_name} (id={action_types[state_idx]})")
    move_label = int(moves[state_idx]) if int(moves[state_idx]) != IGNORE_INDEX else 'IGNORE'
    print(f"  move: {move_label}")
    tera_label = int(teras[state_idx]) if int(teras[state_idx]) != IGNORE_INDEX else 'IGNORE'
    print(f"  tera: {tera_label}")
    switch_label = int(switches[state_idx]) if int(switches[state_idx]) != IGNORE_INDEX else 'IGNORE'
    print(f"  switch: {switch_label}")
    faint_label = int(faints[state_idx]) if int(faints[state_idx]) != IGNORE_INDEX else 'IGNORE'
    print(f"  faint: {faint_label}")
    
    # Hashed vector stats
    print("\n[HASHED VECTOR]")
    if np is not None:
        nonzero_count = int((hashed_vec != 0).sum())
        print(f"  shape: {hashed_vec.shape}")
        print(f"  dtype: {hashed_vec.dtype}")
        print(f"  nonzero: {nonzero_count} / {vector_size}")
        print(f"  sum: {float(hashed_vec.sum()):.4f}")
        print(f"  min: {float(hashed_vec.min()):.6f}, max: {float(hashed_vec.max()):.6f}")
        print(f"  first 20 values: {hashed_vec[:20]}")
    else:
        nonzero_count = sum(1 for v in hashed_vec if v != 0)
        print(f"  length: {len(hashed_vec)}")
        print(f"  nonzero: {nonzero_count} / {vector_size}")
        print(f"  sum: {sum(hashed_vec):.4f}")
        print(f"  first 20 values: {hashed_vec[:20]}")
    
    # Token-row (sparse representation)
    print(f"\n[SPARSE TOKEN-ROW ({len(token_row)} tokens)]")
    reversed_tokens = reverse_tokenrow_row(
        token_row,
        id2token={str(k): v for k, v in id2token.items()},
        topk=None
    )

    # If token embeddings are provided via environment, load and show per-token embeddings
    try:
        import os
        te_path = os.environ.get('INSPECT_TOKEN_EMBEDDINGS')
    except Exception:
        te_path = None

    token_embeddings = None
    if te_path and np is not None:
        try:
            if os.path.exists(te_path):
                token_embeddings = np.load(te_path)
                print(f"\n[TOKEN EMBEDDINGS] Loaded embeddings from {te_path} shape={token_embeddings.shape}")
            else:
                print(f"\n[TOKEN EMBEDDINGS] path does not exist: {te_path}")
                token_embeddings = None
        except Exception as e:
            print(f"\n[TOKEN EMBEDDINGS] failed to load embeddings: {e}")
            token_embeddings = None
    
    # Group by category
    categories = {}
    for tok, val in reversed_tokens:
        cat = tok.split('.')[0] if '.' in tok else tok.split(':')[0]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append((tok, val))
    
    for cat in sorted(categories.keys()):
        print(f"\n  [{cat.upper()}] ({len(categories[cat])} tokens)")
        for tok, val in sorted(categories[cat]):
            print(f"    {tok}: {val}")

    # If token embeddings available, print per-token vectors for this state's token-row and a pooled embedding
    if token_embeddings is not None:
        print("\n[EMBEDDING INSPECTION]")
        emb_dim = int(token_embeddings.shape[1]) if token_embeddings.ndim == 2 else None
        pooled = None
        total_weight = 0.0
        for tid, val in token_row:
            try:
                emb = token_embeddings[int(tid)]
            except Exception:
                print(f"  token id {tid} not in embedding matrix (shape {token_embeddings.shape})")
                continue
            print(f"  token_id={tid} token='{id2token.get(int(tid))}' weight={val} emb[:8]={list(emb[:8]) if emb_dim and emb_dim>=8 else emb}")
            if pooled is None:
                pooled = emb.astype(float) * float(val)
            else:
                pooled += emb.astype(float) * float(val)
            total_weight += float(val)
        if pooled is not None and total_weight > 0:
            pooled = pooled / total_weight
            print(f"\n  pooled embedding (weighted by token values) first 16 dims: {list(pooled[:16])}")
        else:
            print("  pooled embedding: none (no tokens matched embedding matrix)")
    
    print("\n" + "=" * 100)


def main() -> None:
    p = argparse.ArgumentParser(
        description="Inspect a single state: show vector, tokens, and labels."
    )
    p.add_argument('--parsed', required=True, help='path to parsed replay json')
    p.add_argument('--state', type=int, default=0, help='state index to inspect (default 0)')
    p.add_argument('--vector-size', type=int, default=128, help='hashed vector size')
    p.add_argument('--vocab-prefix', type=str, default=None, help='optional vocab prefix (e.g. vocab/shared) to use shared vocabs')
    p.add_argument('--token-embeddings', type=str, default=None, help='optional path to token embeddings .npy (shape: [n_tokens, emb_dim]) to inspect learned embeddings')
    args = p.parse_args()
    # If a vocab-prefix is provided, expose it via environment variable used by inspect_state
    if args.vocab_prefix:
        import os
        os.environ['INSPECT_VOCAB_PREFIX'] = args.vocab_prefix
    if args.token_embeddings:
        import os
        os.environ['INSPECT_TOKEN_EMBEDDINGS'] = args.token_embeddings

    inspect_state(args.parsed, args.state, vector_size=args.vector_size)


if __name__ == '__main__':
    main()
