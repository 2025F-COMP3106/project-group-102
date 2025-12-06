"""Vectorize parsed replay states into hashed feature vectors (streaming).

This script:
- Converts parsed state dicts into sparse feature dicts (state_to_feature_tokens)
- Hashes features into a fixed-size dense vector (dict_to_hashed_vector)
- Uses existing group vocabs (moves/types/species) for multi-head labels
- Streams over all JSON files in a folder

Output:
    <out_prefix>_X_hashed.npy          (N, VECTOR_SIZE)
    <out_prefix>_y_action_type.npy
    <out_prefix>_y_move.npy
    <out_prefix>_y_tera.npy
    <out_prefix>_y_switch.npy
    <out_prefix>_y_faint.npy
    <out_prefix>_y_winner.npy
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from typing import Dict, List, Tuple, Any

try:
    import numpy as np
except Exception:
    np = None


# =====================
# IO helpers
# =====================

def load_json(path: str) -> Any:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _load_optional_vocab(root: str):
    """Try to load shared move/species vocabs to help normalize prev move strings."""
    moves = None
    species = None
    try:
        p = os.path.join('vocab', 'shared_vocab_moves.json')
        if os.path.exists(p):
            with open(p, 'r', encoding='utf-8') as f:
                moves = set(json.load(f))
    except Exception:
        moves = None
    try:
        p2 = os.path.join('vocab', 'shared_vocab_species.json')
        if os.path.exists(p2):
            with open(p2, 'r', encoding='utf-8') as f:
                species = set(json.load(f))
    except Exception:
        species = None
    return moves, species


# =====================
# Prev-move parsing
# =====================

def _parse_prev_raw_move(raw_move: str, moves_set=None, species_set=None):
    """Robustly parse raw prev-action move strings.

    Returns: move_name (or None), faint_species list, switch_species list, tera_flag (bool)
    """
    if not raw_move or not isinstance(raw_move, str):
        return None, [], [], False
    s = raw_move.replace(',', ';')
    parts = [p.strip() for p in s.split(';') if p.strip()]
    move_name = None
    faint_species = []
    switch_species = []
    tera_flag = False

    for tok in parts:
        if not tok:
            continue
        if tok.startswith('faint:'):
            sp = tok.split(':', 1)[1].strip()
            if sp:
                faint_species.append(sp)
            continue
        if tok.startswith('switch:'):
            sp = tok.split(':', 1)[1].strip()
            if sp:
                switch_species.append(sp)
            continue
        if tok.startswith('move:'):
            cand = tok.split(':', 1)[1].strip()
            if cand:
                move_name = cand
            continue
        if tok.lower() == 'tera' or tok.lower().startswith('tera'):
            tera_flag = True
            if ' ' in tok:
                after = tok.split(' ', 1)[1].strip()
                if after:
                    move_name = move_name or after
            continue
        if ':' in tok:
            left, right = tok.split(':', 1)
            left = left.strip()
            right = right.strip()
            if species_set and right in species_set:
                if left:
                    move_name = move_name or left
                continue
        if moves_set and tok in moves_set:
            move_name = move_name or tok
            continue
        if not move_name and ((' ' in tok) or any(c.isupper() for c in tok)):
            move_name = tok
            continue
        if species_set and tok in species_set:
            switch_species.append(tok)
            continue

    return move_name, faint_species, switch_species, tera_flag


# =====================
# Feature extraction
# =====================

def state_to_feature_tokens(state: Dict) -> Dict[str, float]:
    """Convert a state to features using slot-agnostic tokenization."""
    feats: Dict[str, float] = {}

    # For each player, collect team-level info
    for p_label, p_key in (('p1', 'player1'), ('p2', 'player2')):
        p = state.get(p_key, {}) or {}
        pokemon = p.get('pokemon', []) or []

        team_species = set()
        team_types = set()
        team_moves = set()
        team_items = set()
        team_statuses = set()
        team_volatiles = set()
        team_tera_types = set()

        total_team_health = 0.0
        team_with_health = 0

        for mon in pokemon:
            if mon is None:
                continue
            sp = mon.get('species')
            if sp:
                team_species.add(sp)
            for t in (mon.get('type') or []) or []:
                team_types.add(t)
            if mon.get('item'):
                team_items.add(mon.get('item'))
            if mon.get('tera_type'):
                team_tera_types.add(mon.get('tera_type'))
            for mv in (mon.get('moves') or []):
                name = mv.get('name')
                if name:
                    team_moves.add(name)
            status = mon.get('status_effects')
            if status:
                if isinstance(status, dict):
                    keys = list(status.keys())
                elif isinstance(status, list):
                    keys = status
                elif isinstance(status, str):
                    keys = [status]
                else:
                    keys = []
                for s in keys:
                    team_statuses.add(s)
            for v in (mon.get('volatile_effects') or {}).keys():
                team_volatiles.add(v)

            health = mon.get('health')
            if isinstance(health, (int, float)):
                total_team_health += float(health)
                team_with_health += 1

        for sp in team_species:
            feats[f"{p_label}.species:{sp}"] = 1.0
        for t in team_types:
            feats[f"{p_label}.type:{t}"] = 1.0
        for m in team_moves:
            feats[f"{p_label}.move:{m}"] = 1.0
        for it in team_items:
            feats[f"{p_label}.item:{it}"] = 1.0
        for tera in team_tera_types:
            feats[f"{p_label}.tera:{tera}"] = 1.0
        for st in team_statuses:
            feats[f"{p_label}.status:{st}"] = 1.0
        for vol in team_volatiles:
            feats[f"{p_label}.volatile:{vol}"] = 1.0

        if team_with_health > 0:
            feats[f"{p_label}.avg_health_pct"] = total_team_health / (100.0 * team_with_health)

        active_name = state.get('environment', {}).get('active_pokemon', {}).get(p_label)
        if active_name:
            feats[f"{p_label}.active:{active_name}"] = 1.0

    # Environment
    env = state.get('environment', {}) or {}
    if env.get('weather'):
        feats[f"env.weather:{env.get('weather')}"] = 1.0
    if env.get('terrain'):
        feats[f"env.terrain:{env.get('terrain')}"] = 1.0
    for fe in (env.get('field_effects') or []):
        feats[f"env.field:{fe}"] = 1.0
    for fe, turns in (env.get('field_effects_turns') or {}).items():
        feats[f"env.field_turns:{fe}"] = float(turns)

    hazards = env.get('hazards') or {}
    for side in ('p1', 'p2'):
        for h, val in (hazards.get(side) or {}).items():
            feats[f"env.hazard.{side}:{h}"] = float(val) if isinstance(val, (int, float)) else 1.0

    # Previous actions (up to 5)
    prev = env.get('prev_actions') or []
    moves_set, species_set = _load_optional_vocab('.')
    for i, a in enumerate(prev[-5:]):
        if a.get('player'):
            feats[f"env.prev.{i}.player:{a.get('player')}"] = 1.0

        raw_move = a.get('move')
        move_name, faint_species, switch_species, tera_flag = _parse_prev_raw_move(
            raw_move, moves_set, species_set
        )
        if move_name:
            feats[f"env.prev.{i}.move:{move_name}"] = 1.0
        if tera_flag:
            feats[f"env.prev.{i}.tera"] = 1.0
        if faint_species:
            feats[f"env.prev.{i}.faint_count"] = float(len(faint_species))
            for j, sp in enumerate(faint_species[:2]):
                feats[f"env.prev.{i}.faint_species_{j}:{sp}"] = 1.0
        if switch_species:
            for sp in switch_species:
                feats[f"env.prev.{i}.switch:{sp}"] = 1.0
        if a.get('stat_change'):
            sc = a.get('stat_change')
            feats[f"env.prev.{i}.statchange:{sc.get('stat')}:{sc.get('change')}"] = 1.0

    # Meta (no winner)
    if state.get('avg_rating') is not None:
        try:
            feats['meta.avg_rating'] = float(state.get('avg_rating'))
        except Exception:
            pass

    return feats


# =====================
# Hashing
# =====================

def dict_to_hashed_vector(feat_dict: Dict[str, float], size: int = 1024) -> List[float]:
    """Convert feature dict to fixed-size dense vector with simple hashing."""
    vec = [0.0] * size
    for k, v in feat_dict.items():
        h = hashlib.md5(k.encode('utf-8')).hexdigest()
        idx = int(h, 16) % size
        vec[idx] += float(v)
    return vec


# =====================
# Labels
# =====================

IGNORE_INDEX = -1

ACTION_TYPE_LABELS = {
    'MOVE': 0,
    'PIVOT_MOVE': 1,
    'TERA_MOVE': 2,
    'TERA_PIVOT_MOVE': 3,
    'SWITCH': 4,
    'TERA_NO_ACTION': 5,
    'NO_ACTION': 6,
}

ACTION_TYPE_REVERSE = {v: k for k, v in ACTION_TYPE_LABELS.items()}


def parse_p2_next_move(p2_next_move_str: str | None,
                        move2id: Dict[str, int],
                        tera2id: Dict[str, int],
                        species2id: Dict[str, int]) -> Tuple[int, int, int, int, int]:
    if not p2_next_move_str or str(p2_next_move_str).lower() == "null":
        return (ACTION_TYPE_LABELS["NO_ACTION"],
                IGNORE_INDEX, IGNORE_INDEX, IGNORE_INDEX, IGNORE_INDEX)

    s = str(p2_next_move_str).strip()

    action_type_id = ACTION_TYPE_LABELS["NO_ACTION"]
    move_id = IGNORE_INDEX
    tera_type_id = IGNORE_INDEX
    switch_target_id = IGNORE_INDEX
    faint_switch_id = IGNORE_INDEX

    # 0) Standalone faint action: "faint:Pokemon"
    if s.startswith("faint:"):
        faint_species = s.split(":", 1)[1].strip()
        if faint_species:
            faint_switch_id = species2id.get(faint_species, IGNORE_INDEX)
        # You can either define a separate ACTION_TYPE for this,
        # or treat it as NO_ACTION with only the faint head supervised.
        action_type_id = ACTION_TYPE_LABELS["NO_ACTION"]
        return action_type_id, move_id, tera_type_id, switch_target_id, faint_switch_id

    # 1) Optional faint suffix for other actions: "... ,faint:Pokemon"
    main = s
    faint_part = None
    if ",faint:" in s:
        main, faint_part = s.split(",faint:", 1)
        faint_part = faint_part.strip()
        if faint_part:
            faint_species = faint_part.strip()
            faint_switch_id = species2id.get(faint_species, IGNORE_INDEX)
    main = main.strip()

    # 2) Pure switch: "switch:Species"
    if main.startswith("switch:"):
        action_type_id = ACTION_TYPE_LABELS["SWITCH"]
        species = main.split(":", 1)[1].strip()
        switch_target_id = species2id.get(species, IGNORE_INDEX)
        return action_type_id, move_id, tera_type_id, switch_target_id, faint_switch_id

    # 3) Tera vs non-tera using ';'
    semi_parts = [p.strip() for p in main.split(";") if p.strip()]

    # No ';' -> no tera, handle as normal move/pivot
    if len(semi_parts) == 1:
        move_and_target = semi_parts[0]  # e.g. "U-turn:Dragapult" or "Knock Off"
        subparts = [p.strip() for p in move_and_target.split(":") if p.strip()]

        if len(subparts) == 1:
            move_name = subparts[0]
            if move_name and move_name.lower() != "null":
                move_id = move2id.get(move_name, IGNORE_INDEX)
                action_type_id = ACTION_TYPE_LABELS["MOVE"]
        else:
            move_name = subparts[0]
            target = subparts[1]
            if move_name and move_name.lower() != "null":
                move_id = move2id.get(move_name, IGNORE_INDEX)
            if "u-turn" in move_name.lower() or "volt switch" in move_name.lower() or "pivot" in move_name.lower():
                action_type_id = ACTION_TYPE_LABELS["PIVOT_MOVE"]
                switch_target_id = species2id.get(target, IGNORE_INDEX)
            else:
                action_type_id = ACTION_TYPE_LABELS["MOVE"]

        return action_type_id, move_id, tera_type_id, switch_target_id, faint_switch_id

    # "<type>;<move_and_target>"
    tera_str = semi_parts[0]
    move_and_target = ";".join(semi_parts[1:])

    if tera_str in tera2id:
        tera_type_id = tera2id.get(tera_str, IGNORE_INDEX)

        subparts = [p.strip() for p in move_and_target.split(":") if p.strip()]
        if len(subparts) == 0:
            action_type_id = ACTION_TYPE_LABELS["TERA_NO_ACTION"]
        elif len(subparts) == 1:
            move_name = subparts[0]
            if move_name and move_name.lower() != "null":
                move_id = move2id.get(move_name, IGNORE_INDEX)
            action_type_id = ACTION_TYPE_LABELS["TERA_MOVE"]
        else:
            move_name = subparts[0]
            target = subparts[1]
            if move_name and move_name.lower() != "null":
                move_id = move2id.get(move_name, IGNORE_INDEX)
            if "u-turn" in move_name.lower() or "volt switch" in move_name.lower() or "pivot" in move_name.lower():
                action_type_id = ACTION_TYPE_LABELS["TERA_PIVOT_MOVE"]
            else:
                action_type_id = ACTION_TYPE_LABELS["TERA_PIVOT_MOVE"]
            switch_target_id = species2id.get(target, IGNORE_INDEX)

        return action_type_id, move_id, tera_type_id, switch_target_id, faint_switch_id

    # Fallback: treat as non-tera move/pivot on main
    move_and_target = main
    subparts = [p.strip() for p in move_and_target.split(":") if p.strip()]
    if len(subparts) == 1:
        move_name = subparts[0]
        if move_name and move_name.lower() != "null":
            move_id = move2id.get(move_name, IGNORE_INDEX)
            action_type_id = ACTION_TYPE_LABELS["MOVE"]
    else:
        move_name = subparts[0]
        target = subparts[1]
        if move_name and move_name.lower() != "null":
            move_id = move2id.get(move_name, IGNORE_INDEX)
        if "u-turn" in move_name.lower() or "volt switch" in move_name.lower() or "pivot" in move_name.lower():
            action_type_id = ACTION_TYPE_LABELS["PIVOT_MOVE"]
            switch_target_id = species2id.get(target, IGNORE_INDEX)
        else:
            action_type_id = ACTION_TYPE_LABELS["MOVE"]

    return action_type_id, move_id, tera_type_id, switch_target_id, faint_switch_id




def extract_multihead_labels(parsed: List[Dict],
                            move2id: Dict[str, int],
                            tera2id: Dict[str, int],
                            species2id: Dict[str, int]
                            ) -> Tuple[List[int], List[int], List[int], List[int], List[int]]:
    """Extract multi-head labels from a list of parsed states."""
    action_types = []
    moves = []
    teras = []
    switches = []
    faints = []

    for state in parsed:
        p2_next = state.get('p2_next_move')
        action_type, move_id, tera_id, switch_id, faint_id = parse_p2_next_move(
            p2_next, move2id, tera2id, species2id
        )
        action_types.append(action_type)
        moves.append(move_id)
        teras.append(tera_id)
        switches.append(switch_id)
        faints.append(faint_id)

    return action_types, moves, teras, switches, faints


# =====================
# Streaming vectorization (hashed)
# =====================

def stream_vectorize_folder(parsed_dir: str,
                            out_prefix: str,
                            moves2id_path: str,
                            types2id_path: str,
                            species2id_path: str,
                            vector_size: int = 1024,
                            total_states: int | None = None) -> None:
    """
    Stream over all parsed jsons in parsed_dir and build:
    - <out_prefix>_X_hashed.npy    (N, vector_size)
    - <out_prefix>_y_action_type.npy
    - <out_prefix>_y_move.npy
    - <out_prefix>_y_tera.npy
    - <out_prefix>_y_switch.npy
    - <out_prefix>_y_faint.npy
    - <out_prefix>_y_winner.npy
    """
    if np is None:
        raise RuntimeError("NumPy is required for streaming vectorization")

    # Load group vocabs
    with open(moves2id_path, "r", encoding="utf-8") as f:
        move2id = json.load(f)
    with open(types2id_path, "r", encoding="utf-8") as f:
        tera2id = json.load(f)
    with open(species2id_path, "r", encoding="utf-8") as f:
        species2id = json.load(f)

    file_paths = [os.path.join(parsed_dir, fname)
                    for fname in os.listdir(parsed_dir)
                    if fname.endswith(".json")]
    print(f"[stream_vectorize] Found {len(file_paths)} files in {parsed_dir}")

    # First pass: count states
    file_paths = [os.path.join(parsed_dir, fname)
                    for fname in os.listdir(parsed_dir)
                    if fname.endswith(".json")]
    print(f"[stream_vectorize] Found {len(file_paths)} files in {parsed_dir}")

    # First pass: count states (only if not provided)
    if total_states is None:
        total_states = 0
        for i, path in enumerate(file_paths):
            parsed = load_json(path)
            if isinstance(parsed, list):
                total_states += len(parsed)
            if (i + 1) % 1000 == 0:
                print(f"[stream_vectorize] Counted states in {i+1}/{len(file_paths)} files "
                        f"({total_states} states so far)")
    print(f"[stream_vectorize] Total states: {total_states}")

    X_hashed = np.zeros((total_states, vector_size), dtype=np.float32)
    y_action = np.full((total_states,), IGNORE_INDEX, dtype=np.int32)
    y_move   = np.full((total_states,), IGNORE_INDEX, dtype=np.int32)
    y_tera   = np.full((total_states,), IGNORE_INDEX, dtype=np.int32)
    y_switch = np.full((total_states,), IGNORE_INDEX, dtype=np.int32)
    y_faint  = np.full((total_states,), IGNORE_INDEX, dtype=np.int32)
    y_win    = np.full((total_states,), IGNORE_INDEX, dtype=np.int32)

    idx = 0
    bad_labels = []
    for fi, path in enumerate(file_paths):
        parsed = load_json(path)
        if not isinstance(parsed, list):
            continue

        a_types, moves, teras, switches, faints = extract_multihead_labels(
            parsed, move2id, tera2id, species2id
        )

        for s, a_t, m_id, t_id, sw_id, ft_id in zip(parsed, a_types, moves, teras, switches, faints):
            feats = state_to_feature_tokens(s)
            # strip any accidental non-percept keys
            bad_keys = [k for k in feats if ("p2_next_move" in k or "winner" in k)]
            for bk in bad_keys:
                del feats[bk]

            hv = dict_to_hashed_vector(feats, size=vector_size)
            X_hashed[idx, :] = hv

            y_action[idx] = a_t
            y_move[idx]   = m_id
            y_tera[idx]   = t_id
            y_switch[idx] = sw_id
            y_faint[idx]  = ft_id

            w = s.get("winner")
            if w in ("player1", "p1"):
                y_win[idx] = 1  # choose 1 for P1 win
            elif w in ("player2", "p2"):
                y_win[idx] = 0  # choose 0 for P2 win
            else:
                bad_labels.append((path, idx, w))

            idx += 1

        if (fi + 1) % 500 == 0:
            print(f"[stream_vectorize] Processed {fi+1}/{len(file_paths)} files, "
                    f"{idx}/{total_states} states")

    os.makedirs(os.path.dirname(out_prefix) or ".", exist_ok=True)
    np.save(out_prefix + "_X_hashed.npy",      X_hashed)
    np.save(out_prefix + "_y_action_type.npy", y_action)
    np.save(out_prefix + "_y_move.npy",        y_move)
    np.save(out_prefix + "_y_tera.npy",        y_tera)
    np.save(out_prefix + "_y_switch.npy",      y_switch)
    np.save(out_prefix + "_y_faint.npy",       y_faint)
    np.save(out_prefix + "_y_winner.npy",      y_win)

    with open(out_prefix + "_action_type_map.json", "w", encoding="utf-8") as f:
        json.dump(ACTION_TYPE_LABELS, f)
    with open(out_prefix + "_ignore_index.json", "w", encoding="utf-8") as f:
        json.dump({"IGNORE_INDEX": IGNORE_INDEX}, f)

    print(f"[stream_vectorize] Saved hashed X and labels with prefix {out_prefix}")
    print(f"[stream_vectorize] Bad winner labels (count={len(bad_labels)}): {bad_labels}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--folder', required=True,
                    help='folder with parsed replay json files (each a list of states)')
    p.add_argument('--moves2id', required=True,
                    help='path to existing moves2id.json')
    p.add_argument('--types2id', required=True,
                    help='path to existing types2id.json (for tera types)')
    p.add_argument('--species2id', required=True,
                    help='path to existing species2id.json')
    p.add_argument('--out-prefix', default='out/vector',
                    help='prefix for output files')
    p.add_argument('--vector-size', type=int, default=512,
                    help='hashed vector size (e.g. 512 or 1024)')
    p.add_argument('--total-states', type=int, default=None,
                    help='optional known total number of states to skip counting pass')
    args = p.parse_args()

    stream_vectorize_folder(
        parsed_dir=args.folder,
        out_prefix=args.out_prefix,
        moves2id_path=args.moves2id,
        types2id_path=args.types2id,
        species2id_path=args.species2id,
        vector_size=args.vector_size,
        total_states=args.total_states,
    )


if __name__ == '__main__':
    main()
