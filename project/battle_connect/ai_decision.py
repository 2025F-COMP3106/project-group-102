import sys
import os
import json
import logging
import time
import subprocess
import torch
from pathlib import Path

# Setup paths
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Change to project root for imports
original_cwd = os.getcwd()
os.chdir(str(project_root))

try:
    from replay_to_inference import (
        fetch_replay_json,
        parse_replay_with_subprocess,
        build_agent,
        states_to_input_batch,
        INPUT_DIM,
    )
finally:
    os.chdir(original_cwd)

logger = logging.getLogger(__name__)


def normalize_name(name):
    return name.lower().replace("-", "").replace(" ", "").replace("'", "").replace("é", "e")


def parse_move_name(move_str):

    if not move_str:
        raise ValueError("Move name is empty")
    
    # Remove faint suffix
    if ",faint:" in move_str:
        move_str = move_str.split(",faint:")[0]
    
    result = {"move": None, "tera": None, "switch": None, "is_switch": False}
    
    if move_str.startswith("switch:"):
        result["is_switch"] = True
        result["switch"] = move_str.split("switch:", 1)[1].strip()
        return result
    
    # Check for tera type (format: "Type;Move")
    parts = move_str.split(";", 1)
    if len(parts) == 2:
        result["tera"] = parts[0].strip()
        move_str = parts[1].strip()
    
    # Check for switch target (format: "Move:Target")
    if ":" in move_str:
        result["move"], result["switch"] = move_str.split(":", 1)
        result["move"] = result["move"].strip()
        result["switch"] = result["switch"].strip()
    else:
        result["move"] = move_str.strip()
    
    return result


def evaluate_player_actions(agent, branches, player_id, baseline_state):

    action_key = f"p1_action"
    prior_key = f"p2_prior"

    # 1) Compute baseline value V_base from the single baseline_state
    X_base_np = states_to_input_batch([baseline_state], INPUT_DIM)
    X_base = torch.from_numpy(X_base_np).to(agent.device)

    with torch.no_grad():
        base_logit = agent.win_model(X_base) 
        v_base = torch.sigmoid(base_logit).item()
    # 2) Collect all branch states and compute V_branch for each
    all_states = [br.get("state") or {} for br in branches]
    if not all_states:
        return {}

    X_all_np = states_to_input_batch(all_states, INPUT_DIM)
    X_all = torch.from_numpy(X_all_np).to(agent.device)

    with torch.no_grad():
        branch_logits = agent.win_model(X_all)
        v_branch_all = torch.sigmoid(branch_logits).view(-1) 

    # 3) Group branch indices by this player's move name
    groups: dict[str, list[int]] = {}
    for i, br in enumerate(branches):
        action = br.get(action_key, {}) or {}
        move_name = action.get("move")
        if not move_name:
            continue
        groups.setdefault(move_name, []).append(i)

    # 4) Aggregate probability-weighted deltas per move
    move_delta_sum: dict[str, float] = {}
    move_weight_sum: dict[str, float] = {}
    move_count: dict[str, int] = {}

    for move_name, idxs in groups.items():
        delta_sum = 0.0
        weight_sum = 0.0
        count = 0

        for i in idxs:
            br = branches[i]
            prob = float(br.get("probability", 1.0)) * float(br.get(prior_key, 1.0))

            v_branch = float(v_branch_all[i].item())  # value after this branch
            delta = v_branch - v_base

            delta_sum += delta * prob
            weight_sum += prob
            count += 1

        if weight_sum <= 0:
            continue

        move_delta_sum[move_name] = move_delta_sum.get(move_name, 0.0) + delta_sum
        move_weight_sum[move_name] = move_weight_sum.get(move_name, 0.0) + weight_sum
        move_count[move_name] = move_count.get(move_name, 0) + count

    results = {}
    for move_name in move_delta_sum:
        w = move_weight_sum[move_name]
        if w <= 0:
            continue
        weighted_delta = move_delta_sum[move_name] / w
        results[move_name] = {
            "weighted_delta": weighted_delta,
            "num_branches": move_count[move_name],
            "total_weight": w,
        }

    return results


def to_showdown_command(parsed, request_json):
    if not request_json:
        raise RuntimeError("request_json required")
    
    # Handle switch
    if parsed["is_switch"]:
        side = request_json.get("side", {})
        pokemon = side.get("pokemon", [])
        
        target = parsed["switch"]
        # Strip form suffixes like "-*" or "-Crowned" that might be in output but not in request_json
        target_base = target.split("-")[0].strip() if "-" in target else target
        target_normalized = normalize_name(target_base)
        
        logger.debug(f"Looking for switch target: '{target}' (base: '{target_base}', normalized: '{target_normalized}')")
        
        # Collect all potential matches first to ensure we get the best one
        matches = []
        for i, pkmn in enumerate(pokemon):
            if pkmn.get("active", False) or "fnt" in pkmn.get("condition", "").lower():
                continue
            
            species = pkmn.get("details", "").split(",")[0].strip() if pkmn.get("details") else ""
            if not species:
                ident = pkmn.get("ident", "")
                if ident:
                    species = ident.split(":")[-1].strip()
                else:
                    species = pkmn.get("name", "")
            
            # Strip form suffix from species too
            species_base = species.split("-")[0].strip() if "-" in species else species
            species_normalized = normalize_name(species_base)
            
            logger.debug(f"  Checking Pokemon {i+1}: '{species}' (base: '{species_base}', normalized: '{species_normalized}')")
            
            # Try exact match first (most reliable)
            if species == target:
                matches.append((i, pkmn, species, "exact"))
            # Then normalized base match (handles form differences)
            elif species_normalized == target_normalized:
                matches.append((i, pkmn, species, "normalized_base"))
            # Then case-insensitive base match
            elif species_base.lower() == target_base.lower():
                matches.append((i, pkmn, species, "case_insensitive_base"))
        
        if matches:
            # Prefer exact match, then normalized, then case-insensitive
            matches.sort(key=lambda x: {"exact": 0, "normalized_base": 1, "case_insensitive_base": 2}[x[3]])
            i, pkmn, species, match_type = matches[0]
            
            ident = pkmn.get("ident", "")
            switch_name = ident.split(":")[-1].strip() if ident else species
            
            logger.info(f"Matched switch target '{target}' to '{switch_name}' (match type: {match_type}, position {i+1})")
            return f"/switch {switch_name}"
        
        available = [f"{i+1}: {p.get('details', '').split(',')[0].strip() if p.get('details') else p.get('ident', '').split(':')[-1].strip()}" 
                    for i, p in enumerate(pokemon) 
                    if not p.get("active", False) and "fnt" not in p.get("condition", "").lower()]
        raise RuntimeError(f"Switch target '{parsed['switch']}' not found. Available: {available}")
    
    # Handle move
    move_name = parsed["move"]
    if not move_name:
        raise RuntimeError("Move name required")
    
    # Get active Pokemon
    active = request_json.get("active")
    if active and isinstance(active, list) and len(active) > 0:
        active_pokemon = active[0]
    else:
        side = request_json.get("side", {})
        for pkmn in side.get("pokemon", []):
            if pkmn.get("active", False):
                active_pokemon = pkmn
                break
        else:
            raise RuntimeError("No active Pokemon found")
    
    # Find move in available moves
    moves = active_pokemon.get("moves", [])
    for move in moves:
        if isinstance(move, dict):
            if move.get("disabled", False):
                continue
            move_id = move.get("id", "")
            move_display = move.get("move", "")
        else:
            move_id = str(move)
            move_display = move_id
        
        if (move_id == move_name or move_display == move_name or
            normalize_name(move_id) == normalize_name(move_name) or
            normalize_name(move_display) == normalize_name(move_name)):
            command = f"/move {move_id if move_id else move_display}"
            if parsed["tera"]:
                command += " terastallize"
            return command
    
    available = [m.get("id", "") if isinstance(m, dict) else str(m) for m in moves]
    raise RuntimeError(f"Move '{move_name}' not found. Available: {available}")


def get_ai_command_from_replay(replay_link, request_json=None, device=None, player_id="p1", max_retries=10, retry_delay=1.0):
    replay_id = replay_link.replace("battle-", "") if replay_link.startswith("battle-") else replay_link
    
    if device is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
    
    original_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        
        # Fetch latest replay
        in_state_file = str(project_root / "in-state.json")
        for old_file in [in_state_file, str(project_root / "in-state.parsed.json")]:
            if os.path.exists(old_file):
                try:
                    os.remove(old_file)
                except:
                    pass
        
        for attempt in range(max_retries):
            try:
                raw_log = fetch_replay_json(replay_id, in_state_file)
                if os.path.exists(in_state_file) and os.path.getsize(in_state_file) > 0:
                    break
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                else:
                    raise RuntimeError(f"Failed to fetch replay: {e}")
        
        # Parse replay
        # Data files are in data/ at root level (one level up from project/)
        moves_data = str(project_root.parent / "data" / "gen9_moves.json")
        pokemon_data = str(project_root.parent / "data" / "gen9_pokemon.json")
        if raw_log and raw_log['players'][0] != 'Gbolshnik':
            subprocess.run(["python", "switch_viewpoint.py", in_state_file], capture_output=True, text=True)
        parsed_state = parse_replay_with_subprocess(in_state_file, moves_data, pokemon_data)
        
        if len(parsed_state['player1']['pokemon'][0]['moves']) == 0 and raw_log['players'][0] != 'Gbolshnik':
            with open(f"{project_root}/myteammoves.json", "r") as f:
                team_moves = json.load(f)
            for pokemon in range(6):
                parsed_state['player1']['pokemon'][pokemon]['moves'] = team_moves[pokemon]['moves']
                
        print(f"Loaded last state from replay")
        
        with open("in-state.parsed.json", "w") as f:
            json.dump(parsed_state, f, ensure_ascii=False, indent=4)
        if not parsed_state:
            raise RuntimeError("Failed to parse replay")
        
        # Generate top-moves.txt
        agent = build_agent(device)
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()) as buf:
            agent.describe_action_candidates(parsed_state, k=5)
        with open(str(project_root / "top-moves.txt"), "w") as f:
            f.write(buf.getvalue())
        
        # Generate branches
        parsed_file = str(project_root / "in-state.parsed.json")
        with open(parsed_file, "w") as f:
            json.dump(parsed_state, f, indent=2)
        
        result = subprocess.run(
            ["node", "attempt3.js", parsed_file],
            capture_output=True,
            text=True,
            cwd=str(project_root)
        )
        
        if result.returncode != 0:
            raise RuntimeError(f"attempt3.js failed: {result.stderr}")
        
        # Evaluate branches
        with open(str(project_root / "branches.json"), "r") as f:
            branches = json.load(f)
        
        if not branches:
            raise RuntimeError("No branches generated")
        
        results = evaluate_player_actions(agent, branches, player_id, parsed_state)
        if not results:
            raise RuntimeError("No actions found")

        # Check if a switch is forced (e.g., after a faint or forced switch-out)
        force_switch = request_json and request_json.get("forceSwitch", False)
        # Also check if there's no active Pokemon (after a faint)
        no_active = False
        if request_json:
            active = request_json.get("active")
            if not active or (isinstance(active, list) and len(active) == 0):
                # Check side for active Pokemon
                side = request_json.get("side", {})
                pokemon = side.get("pokemon", [])
                no_active = not any(p.get("active", False) for p in pokemon)
        
        if force_switch or no_active:
            # Filter to only switch actions when switch is forced
            switch_results = {k: v for k, v in results.items() if k.startswith("switch:")}
            if switch_results:
                results = switch_results
                logger.info(f"Switch is required (forceSwitch={force_switch}, no_active={no_active}), filtering to switch actions only")
            else:
                logger.warning("Switch is required but no switch actions found in results")

        best_move, best_info = max(results.items(), key=lambda kv: kv[1]["weighted_delta"])
        
        logger.info(f"Best action for {player_id}: {best_move} (P1 win prob: {best_info['weighted_delta']:.3f})")
        print(results)

        # Handle team preview
        if request_json and request_json.get("teamPreview"):
            # Sort by win probability based on which player we are
            if player_id == "p1":
                # Higher P1 win prob = better for P1
                switches = sorted([(k, v) for k, v in results.items() if k.startswith("switch:")],
                                key=lambda kv: kv[1]["weighted_delta"], reverse=True)[:6]
            else:  # player_id == "p2"
                # Lower P1 win prob = better for P2
                switches = sorted([(k, v) for k, v in results.items() if k.startswith("switch:")],
                                key=lambda kv: kv[1]["weighted_delta"], reverse=False)[:6]
            side = request_json.get("side", {})
            pokemon = side.get("pokemon", [])
            team_order = []
            
            for switch_name, _ in switches:
                parsed = parse_move_name(switch_name)
                target = parsed["switch"]
                # Strip form suffix (like "-*") from target for matching
                target_base = target.split("-")[0].strip() if "-" in target else target
                target_normalized = normalize_name(target_base)
                
                for i, pkmn in enumerate(pokemon):
                    species = pkmn.get("details", "").split(",")[0].strip() if pkmn.get("details") else ""
                    if not species:
                        ident = pkmn.get("ident", "")
                        if ident:
                            species = ident.split(":")[-1].strip()
                        else:
                            species = pkmn.get("name", "")
                    
                    # Strip form suffix from species too
                    species_base = species.split("-")[0].strip() if "-" in species else species
                    species_normalized = normalize_name(species_base)
                    
                    if species_normalized == target_normalized:
                        if str(i + 1) not in team_order:
                            team_order.append(str(i + 1))
                        logger.debug(f"Team preview: matched '{target}' to position {i+1} ({species})")
                        break
            
            # Fill remaining slots
            for i in range(len(pokemon)):
                if str(i + 1) not in team_order:
                    team_order.append(str(i + 1))
            
            return f"/team {''.join(team_order)}"
        
        # Convert to Showdown command
        parsed = parse_move_name(best_move)
        return to_showdown_command(parsed, request_json)
        
    finally:
        os.chdir(original_cwd)
