# replay_inference.py

import argparse
import json
import os
import sys
import io
from contextlib import redirect_stdout
import subprocess
import time
from typing import List, Dict, Any 
from parse_batch import parse_one
from pathlib import Path

import numpy as np
import torch

from vectorize_states import state_to_feature_tokens,dict_to_hashed_vector
from agent_inference import (
    PokemonPolicyAgent,
    MultiHeadPolicy,
    WinPredictor,
    moves2id,
    types2id,
    species2id,
    ACTION_TYPE_LABELS,
    INPUT_DIM,
)

# === 1) SCRAPER: use your scraper.py logic ===

import requests

FORMAT = "gen9ou"

def evaluate_branches_by_p1_action(agent: PokemonPolicyAgent, branches: list[dict]) -> dict:
    """
    For each distinct p1_action (by move name), compute the probability-weighted
    average P1 win chance over all corresponding branches, marginalizing over p2_action.
    """
    groups: dict[str, list[int]] = {}
    for i, br in enumerate(branches):
        p1 = br.get("p1_action", {}) or {}
        move_name = p1.get("move")
        if not move_name:
            continue
        groups.setdefault(move_name, []).append(i)

    results = {}
    for move_name, idxs in groups.items():
        states = []
        weights = []
        for i in idxs:
            br = branches[i]
            st = br.get("state") or {}
            # use new prior field
            probability = float(br.get("probability", 1.0))
            p2_action_prior = float(br.get("p2_action_prior", 1.0))
            prob = probability
            states.append(st)
            weights.append(prob)

        if not states:
            continue

        X_np = states_to_input_batch(states, vector_size=INPUT_DIM)
        X = torch.from_numpy(X_np).to(agent.device)
        w = torch.tensor(weights, dtype=torch.float32, device=agent.device)

        with torch.no_grad():
            win_logits = agent.win_model(X)       # (N, 1)
            win_probs = torch.sigmoid(win_logits).view(-1)  # (N,)

        denom = w.sum().clamp_min(1e-8)
        weighted_mean = float((win_probs * w).sum() / denom)

        results[move_name] = {
            "weighted_win_prob": weighted_mean,
            "num_branches": len(idxs),
            "total_weight": float(denom.item()),
        }

    return results

def fetch_replay_json(replay_id_or_url: str, out_path: str = "in-state.json") -> dict:
    """
    Given either a bare id like 'gen9ou-2485464184' or a full
    https://replay.pokemonshowdown.com/... URL, download the .json payload
    and save it to out_path, then return the loaded dict.
    """
    if replay_id_or_url.startswith("http"):
        url = replay_id_or_url
        if not url.endswith(".json"):
            url = url + ".json"
    else:
        # treat as slug/id
        slug = replay_id_or_url
        url = f"https://replay.pokemonshowdown.com/{slug}.json"

    r = requests.get(url, timeout=10)
    r.raise_for_status()
    data = r.json()

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

    return data

def parse_replay_with_subprocess(
    replay_json_path: str,
    moves_data: str,
    pokemon_data: str,
) -> list[dict]:
    """
    Run parser.py via parse_one on a single replay file and return parsed states.
    """
    input_path = Path(replay_json_path)
    output_path = input_path.with_suffix(".parsed.json")

    env_extra = {
        "PARSER_MOVES_DATA": moves_data,
        "PARSER_POKEMON_DATA": pokemon_data,
    }

    name, code, out, err = parse_one(input_path, output_path, env_extra=env_extra)
    print(f"parse_one({name}) -> code {code}")
    if out:
        print("parser stdout:", out.strip())
    if err:
        print("parser stderr:", err.strip())

    if code != 0:
        raise RuntimeError(f"parse_one failed for {name} with code {code}")

    with output_path.open("r", encoding="utf-8") as f:
        parsed = json.load(f)

    # If parser returns {"states": [...]} instead of a raw list, adjust here.
    if isinstance(parsed, dict) and "states" in parsed:
        parsed = parsed["states"]

    return parsed[-1]

# === 3) VECTORIZATION + AGENT: use your agent_inference and vectorize_states ===


def branch_to_feature_tokens(state: Dict) -> Dict[str, float]:
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

def build_agent(device: str = "cuda") -> PokemonPolicyAgent:
    n_action_types = len(ACTION_TYPE_LABELS)
    n_moves = len(moves2id)
    n_tera = len(types2id)
    n_switch = len(species2id)

    model = MultiHeadPolicy(
        input_dim=INPUT_DIM,
        n_action_types=n_action_types,
        n_moves=n_moves,
        n_tera=n_tera,
        n_switch_targets=n_switch,
    ).to(device)

    ckpt = torch.load("checkpoints/policy_best.pt", map_location="cpu")
    model.load_state_dict(ckpt["model_state_dict"])
    model.eval()

    win_model = WinPredictor(INPUT_DIM).to(device)
    win_ckpt = torch.load("checkpoints/win_best.pt", map_location="cpu")
    win_model.load_state_dict(win_ckpt["model_state_dict"])
    win_model.eval()

    agent = PokemonPolicyAgent(
        model,
        moves2id,
        types2id,
        species2id,
        vector_size=INPUT_DIM,
        action_type_labels=ACTION_TYPE_LABELS,
        device=device,
    )
    agent.win_model = win_model
    return agent


def states_to_input_batch(states: List[Dict[str, Any]], vector_size: int) -> np.ndarray:
    """
    Convert a list of parsed state dicts into an (N, vector_size) matrix,
    using the same logic as state_to_input_vec.
    """
    X = np.zeros((len(states), vector_size), dtype=np.float32)
    for i, state in enumerate(states):
        feats = state_to_feature_tokens(state)
        for k in list(feats.keys()):
            if "p2_next_move" in k or "winner" in k:
                del feats[k]
        hv = dict_to_hashed_vector(feats, size=vector_size)
        X[i] = hv
    return X


def run_replay_inference(
    replay_id_or_url: str,
    device: str = "cuda",
    max_turns: int | None = None,
) -> None:
    
    # 1) scrape
    raw_log = fetch_replay_json(replay_id_or_url)
    print(raw_log['players'])
    if raw_log['players'][0] != 'Gbolshnik':
        subprocess.run(["python", "switch_viewpoint.py", "in-state.json"], capture_output=True, text=True)

    # 2) parse using your parser
    parsed_state = parse_replay_with_subprocess("in-state.json",
                                                moves_data="../../data/gen9_moves.json",pokemon_data="../../data/gen9_pokemon.json")
    if not parsed_state:
        print("No states parsed from replay.")
        return

    print(len(parsed_state['player1']['pokemon'][0]['moves']))
    if len(parsed_state['player1']['pokemon'][0]['moves']) == 0 and raw_log['players'][0] != 'Gbolshnik':
        with open("myteammoves.json", "r") as f:
            team_moves = json.load(f)
        for pokemon in range(6):
            parsed_state['player1']['pokemon'][pokemon]['moves'] = team_moves[pokemon]['moves']
            print(parsed_state['player1']['pokemon'][pokemon]['moves'])
            
    print(f"Loaded last state from replay")
    
    with open("in-state.parsed.json", "w") as f:
        json.dump(parsed_state, f, ensure_ascii=False, indent=4)

    
    # 3) build agent from your checkpoints + vocabs
    agent = build_agent("cuda")
    
    buf = io.StringIO()
    with redirect_stdout(buf):
        agent.describe_action_candidates(parsed_state, k=5)

    text = buf.getvalue()

    with open("./top-moves.txt", "w", encoding="utf-8") as f:
        f.write(text)

    # overall P1 win probability from win head
    p1_prob = agent.p1_win_prob(parsed_state)
    print(f"P1 win chance: {p1_prob * 100:.1f}%")
    print()


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--replay",
        required=False,
        help="Replay id or URL (e.g. gen9ou-2485464184 or full https URL)",
    )
    p.add_argument(
        "--device",
        default="cuda",
        help="cuda or cpu",
    )
    p.add_argument(
        "--max-turns",
        type=int,
        default=None,
        help="optional cap on number of turns to print",
    )
    args = p.parse_args()

    run_replay_inference(
        replay_id_or_url=args.replay,
        device=args.device,
        max_turns=args.max_turns,
    )
    
    result = subprocess.run(["node", "brancher.js"], capture_output=True, text=True)
    
    # === New: aggregate over branches.json ===
    start = time.time()
    with open("branches.json", "r", encoding="utf-8") as f:
        branches = json.load(f)

    # Build agent once for this pass
    agent = build_agent(args.device)

    results = evaluate_branches_by_p1_action(agent, branches)

    if results:
        best_move, best_info = max(
            results.items(),
            key=lambda kv: kv[1]["weighted_win_prob"]
        )
        print(f"Best P1 move: {best_move} "
            f"(weighted win prob = {best_info['weighted_win_prob']:.3f}, "
            f"branches = {best_info['num_branches']})")
    else:
        print("No p1 actions found in branches.json")

    # Optionally, write to a JSON file
    out_path = "./p1_action_eval.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"Wrote p1 action evaluations to {out_path}")
    end = time.time()
    print(f"Branch evaluation took {end - start:.2f} seconds")

if __name__ == "__main__":
    main()
