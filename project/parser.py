import os
import argparse

# Parse command-line arguments
parser = argparse.ArgumentParser(description="Parse Pokémon Showdown replay logs.")
parser.add_argument("-i", "--input", dest="input", help="Input replay JSON file")
parser.add_argument("-o", "--output", dest="output", help="Output parsed JSON file")
args = parser.parse_args()

# Allow overriding paths via environment so a batch runner can set them per-invocation
# Command-line args take precedence over env vars, which take precedence over defaults
INPUT = args.input or os.environ.get("PARSER_INPUT") or "C:/Users/admin/Code/COMP3106/data/replays/gen9ou-switching.json"
OUTPUT = args.output or os.environ.get("PARSER_OUTPUT") or "C:/Users/admin/Code/COMP3106/data/replays/gen9ou-switching.json"
MOVES_DATA = os.environ.get("PARSER_MOVES_DATA") or "C:/Users/admin/Code/COMP3106/data/gen9_moves.json"
POKEMON_DATA = os.environ.get("PARSER_POKEMON_DATA") or "C:/Users/admin/Code/COMP3106/data/gen9_pokemon.json"

import json
from copy import deepcopy

# ---------- Load moves dataset ----------
try:
    with open(MOVES_DATA, "r", encoding="utf-8") as f:
        moves_pp_data = json.load(f)
except FileNotFoundError:
    print(f"Warning: {MOVES_DATA} not found. Using default PP of 35 for all moves.")
    moves_pp_data = {}

# ---------- Load pokemon dataset ----------
try:
    with open(POKEMON_DATA, "r", encoding="utf-8") as f:
        pokemon_types_data = json.load(f)
except FileNotFoundError:
    print(f"Warning: {POKEMON_DATA} not found. Types will not be populated.")
    pokemon_types_data = {}

# Function to get types for a pokemon
def get_pokemon_types(species_name):
    """Get types for a pokemon from the dataset."""
    if not species_name:
        return None
    # Normalize species name (lowercase, handle special forms)
    normalized = species_name.lower().replace(" ", "-")
    if normalized in pokemon_types_data:
        types = pokemon_types_data[normalized].get('types', [])
        return types if types else None
    return None

# Function to get PP for a move
def get_move_pp(move_name):
    """Get PP for a move, using either the dataset or a sensible default."""
    if not move_name:
        return None
    # Normalize move name (replace spaces with hyphens, lowercase)
    normalized = move_name.lower().replace(" ", "-")
    if normalized in moves_pp_data:
        return moves_pp_data[normalized]['pp']
    # Fallback defaults based on move properties
    if normalized in ['protect', 'splash', 'minimize']:
        return 10
    return 35  # Default PP for most moves

def standardize_species(species_name):
    """Add default forms for Pokemon that have hidden initial forms."""
    if not species_name:
        return species_name
    
    # Mimikyu always starts in Disguised form
    if species_name == "Mimikyu":
        return "Mimikyu-Disguised"
    
    # Cramorant starts in base form
    if species_name == "Cramorant":
        return "Cramorant"
    
    # Keep all other forms as-is
    return species_name

# Side conditions with durations (in turns)
SIDE_CONDITION_DURATIONS = {
    'aurora veil': 5,
    'light screen': 5,
    'reflect': 5,
    'mist': 5,
    'safeguard': 5,
    'tailwind': 4,
}

# Moves that force the opponent to switch (Roar, Whirlwind, Dragon Tail, etc.)
FORCED_SWITCH_MOVES = {'roar', 'whirlwind', 'dragon tail'}

# ---------- Load battle log ----------
with open(INPUT, "r", encoding="utf-8") as f:
    log_data = json.load(f)

log_lines = log_data["log"].split("\n")
battle_id = log_data.get("id", "unknown_battle")
avg_rating = log_data.get("rating") or 1400

# ---------- Initialize base structures ----------
state_template = {
    "player1": {"pokemon": []},
    "player2": {"pokemon": []},
    "environment": {
        "weather": None,
        "weather_turns": None,  # Duration tracking
        "terrain": None,
        "terrain_turns": None,  # Duration tracking
        "field_effects": [],  # Track field-wide effects like Trick Room
        "field_effects_turns": {},  # Duration tracking for each field effect
        "hazards": {"p1": {}, "p2": {}},  # Changed to dict to track layer counts
        "active_pokemon": {"p1": None, "p2": None},
        "prev_actions": [],
        "tera_used": {"p1": False, "p2": False}  # Track if tera has been used this game
    },
    "winner": None,
    "avg_rating": avg_rating,
    "p2_next_move": None,
}


# Track Pokémon for both players
# p1: keyed by species (generalized, all known moves)
# p2: keyed by species (not generalized, moves only as discovered)
players_pokemon = {"p1": {}, "p2": {}}
p2_slot_to_species = {}  # Map slot (p2a, p2b...) to species for active pokemon tracking
active_pokemon = {"p1": None, "p2": None}  # p1: species, p2: species
prev_actions = []
player_names = {"p1": None, "p2": None}  # Map player ID to player name (for winner tracking, not included in output)
battle_winner = None  # Will be set when we find the |win| event
hazards = {"p1": {}, "p2": {}}  # Track hazards with layer counts (e.g., {"Stealth Rock": 1, "Spikes": 2})
tera_used_this_turn = {"p1": None, "p2": None}  # Track tera type used this turn for each player
tera_used_this_game = {"p1": False, "p2": False}  # Track if tera has been used at all this game
field_effects = []  # Track active field effects like Trick Room
current_terrain = None  # Track current terrain
player_just_fainted = {"p1": False, "p2": False}  # Track if a player's pokemon just fainted

# Moves that switch out the user's own active pokemon (not opponent's)
SWITCH_MOVES = {'u-turn', 'volt switch', 'flip turn', 'baton pass', 'chilly reception', 'teleport', 'parting shot'}

# Status effects that prevent pokemon from moving
IMMOBILIZING_STATUS = {'paralysis', 'freeze', 'sleep', 'confusion'}
IMMOBILIZING_VOLATILE = {'taunt', 'encore'}

# Volatile effects to track per pokemon
TRACKED_VOLATILE = {
    'psychic_noise', 'drowsy', 'no_retreat', 'balloon', 'magnet_rise', 
    'destiny_bond', 'throat_chop', 'leech_seed', 'salt_cure', 'taunt', 'encore'
}

# Effect durations (in turns)
WEATHER_DURATIONS = {
    'sandstorm': 5,
    'hail': 5,
    'rain': 5,
    'sun': 5,
    'desolate land': 999,  # Permanent until changed
    'primordial sea': 999,
    'delta stream': 999,
    'heavy rain': 5,  # Lasts 5 turns
    'harsh sunlight': 5,
    'sandstorm hail': 5,
}

TERRAIN_DURATIONS = {
    # Note: Grassy Terrain, Misty Terrain, Psychic Terrain, Electric Terrain
    # are field effects, not terrain. Only actual terrains go here.
}

FIELD_EFFECT_DURATIONS = {
    'trick room': 5,
    'tailwind': 3,
    'grassy terrain': 5,
    'misty terrain': 5,
    'psychic terrain': 5,
    'electric terrain': 5,
    'gravity': 5,
    'magic room': 5,
    'wonder room': 5,
}

all_turns = []

def init_pokemon(species):
    types = get_pokemon_types(species)
    return {
        "species": species,
        "type": types,
        "health": 100,
        "status_effects": None,
        "toxic_counter": 0,
        "stat_changes": None,
        "item": None,  # None = unknown, "" = removed/knocked off
        "ability": None,
        "tera_type": None,
        "moves": {},
        "volatile_effects": {}
    }


def add_move_to_pokemon(pokemon_moves, move_name):
    """Add a move to a pokemon's move list with PP tracking."""
    if move_name not in pokemon_moves:
        pokemon_moves[move_name] = {
            "name": move_name,
            "max_pp": get_move_pp(move_name),
            "current_pp": get_move_pp(move_name)
        }

def use_move(pokemon_moves, move_name, target_has_pressure=False):
    """Decrement current_pp for a move when used."""
    if move_name in pokemon_moves and pokemon_moves[move_name]["current_pp"] > 0:
        # Pressure causes 2 PP loss instead of 1
        pp_loss = 2 if target_has_pressure else 1
        pokemon_moves[move_name]["current_pp"] = max(0, pokemon_moves[move_name]["current_pp"] - pp_loss)

def parse_player(line):
    # |player|p1|samulijohan|flannery|1284
    parts = line.split("|")
    pid = parts[2]
    name = parts[3] if parts[3] != "" else None
    return pid, name

# ---------- Parse players and Pokémon ----------
for line in log_lines:
    if line.startswith("|player|"):
        pid, name = parse_player(line)
        if pid == "p1":
            if name:
                player_names["p1"] = name
        elif pid == "p2":
            if name:
                player_names["p2"] = name
    
    elif line.startswith("|poke|p1|"):
        species_raw = line.split("|")[3].split(",")[0].strip()
        species = standardize_species(species_raw)
        players_pokemon["p1"][species] = init_pokemon(species)

    elif line.startswith("|poke|p2|"):
        species_raw = line.split("|")[3].split(",")[0].strip()
        species = standardize_species(species_raw)
        if species not in players_pokemon["p2"]:
            players_pokemon["p2"][species] = init_pokemon(species)
        slot_num = len(p2_slot_to_species)
        slot = f"p2{chr(ord('a') + slot_num)}"
        p2_slot_to_species[slot] = species

# ---------- Save team preview state (no active pokemon yet) ----------
team_preview_snapshot = deepcopy(state_template)
team_preview_snapshot["player1"]["pokemon"] = deepcopy(list(players_pokemon["p1"].values()))
team_preview_snapshot["player2"]["pokemon"] = deepcopy(list(players_pokemon["p2"].values()))
team_preview_snapshot["environment"]["active_pokemon"] = {"p1": None, "p2": None}
team_preview_snapshot["environment"]["prev_actions"] = []
team_preview_snapshot["environment"]["hazards"] = {"p1": {}, "p2": {}}
team_preview_snapshot["environment"]["tera_used"] = {"p1": False, "p2": False}
team_preview_snapshot["winner"] = None  # Will be set later in post-processing
team_preview_snapshot["p2_next_move"] = None  # Will be set to P2's lead in post-processing
all_turns.append(team_preview_snapshot)

# ---------- Parse turns ----------
current_turn = 0
lead_state_saved = False  # Remove team_preview_saved

for line in log_lines:
    # New turn
    if line.startswith("|turn|"):
        turn_number = int(line.split("|")[2])
        
        # Save team preview and lead state when we first see turn 1
        if turn_number == 1 and not lead_state_saved:
            # Save lead state
            snapshot = deepcopy(state_template)
            snapshot["player1"]["pokemon"] = deepcopy(list(players_pokemon["p1"].values()))
            snapshot["player2"]["pokemon"] = deepcopy(list(players_pokemon["p2"].values()))
            snapshot["environment"]["active_pokemon"] = active_pokemon.copy()
            snapshot["environment"]["prev_actions"] = []
            snapshot["environment"]["hazards"] = {"p1": hazards["p1"].copy(), "p2": hazards["p2"].copy()}
            snapshot["environment"]["tera_used"] = tera_used_this_game.copy()
            snapshot["winner"] = battle_winner
            snapshot["p2_next_move"] = None  # P2's first real move (Spikes)
            all_turns.append(snapshot)
            lead_state_saved = True
        
        if current_turn > 0:
            # Decrement field effect durations
            for effect_name in list(state_template["environment"]["field_effects_turns"].keys()):
                state_template["environment"]["field_effects_turns"][effect_name] -= 1
                if state_template["environment"]["field_effects_turns"][effect_name] <= 0:
                    # Effect expired
                    if effect_name in field_effects:
                        field_effects.remove(effect_name)
                    del state_template["environment"]["field_effects_turns"][effect_name]
            
            state_template["environment"]["field_effects"] = field_effects.copy()
            
            # Decrement side condition durations (Aurora Veil, Screens, Tailwind, etc.)
            for pid in ["p1", "p2"]:
                for effect_name in list(hazards[pid].keys()):
                    if effect_name in SIDE_CONDITION_DURATIONS:
                        # This is a duration-based effect, decrement it
                        hazards[pid][effect_name] -= 1
                        if hazards[pid][effect_name] <= 0:
                            # Effect expired
                            del hazards[pid][effect_name]
            
            # Decrement weather turns
            if state_template["environment"]["weather_turns"] and state_template["environment"]["weather_turns"] > 0:
                state_template["environment"]["weather_turns"] -= 1
                if state_template["environment"]["weather_turns"] <= 0:
                    state_template["environment"]["weather"] = None
                    state_template["environment"]["weather_turns"] = None
            
            # Decrement terrain turns
            if state_template["environment"]["terrain_turns"] and state_template["environment"]["terrain_turns"] > 0:
                state_template["environment"]["terrain_turns"] -= 1
                if state_template["environment"]["terrain_turns"] <= 0:
                    state_template["environment"]["terrain"] = None
                    state_template["environment"]["terrain_turns"] = None
            
            # save previous turn (now that we've processed all its moves)
            if prev_actions:  # Only save if there are actions
                snapshot = deepcopy(state_template)
                snapshot["player1"]["pokemon"] = deepcopy(list(players_pokemon["p1"].values()))
                snapshot["player2"]["pokemon"] = deepcopy(list(players_pokemon["p2"].values()))
                snapshot["environment"]["active_pokemon"] = active_pokemon.copy()
                snapshot["environment"]["prev_actions"] = prev_actions.copy()
                snapshot["environment"]["hazards"] = {"p1": hazards["p1"].copy(), "p2": hazards["p2"].copy()}
                snapshot["environment"]["tera_used"] = tera_used_this_game.copy()
                all_turns.append(snapshot)
            
            # Increment toxic counter AFTER saving snapshot (for next turn)
            for pid in ["p1", "p2"]:
                for species in players_pokemon[pid]:
                    if (players_pokemon[pid][species]["status_effects"] == "toxic" and
                        active_pokemon.get(pid) == species):
                        # Only increment if this pokemon is currently active
                        players_pokemon[pid][species]["toxic_counter"] += 1
        
        current_turn = turn_number
        prev_actions = []
        tera_used_this_turn = {"p1": None, "p2": None}  # Reset tera tracking for new turn
        player_just_fainted = {"p1": False, "p2": False}  # Reset faint tracking for new turn
        
        # Reset state_template hazards to empty (they'll be populated based on current hazards dict)
        state_template["environment"]["hazards"] = {"p1": {}, "p2": {}}

    # Moves
    elif line.startswith("|move|"):
        parts = line.split("|")
        full_id = parts[2]       # e.g., 'p1a: Garchomp' or 'p2a: CustomNickname'
        pid_raw = full_id.split(":", 1)[0]
        pid = "p1" if pid_raw.startswith("p1") else "p2" if pid_raw.startswith("p2") else None
        if pid is None:
            continue
        
        # Use the currently active pokemon species (set during switch)
        species = active_pokemon.get(pid)
        if species is None:
            continue

        move_name = parts[3] if len(parts) > 3 else None
        move_name = move_name if move_name != "" else None
        # ... rest of the block stays the same


        # Check if this move switches out the active pokemon
        move_normalized = move_name.lower() if move_name else ""
        is_switch_move = move_normalized in SWITCH_MOVES

        # Format action as just the move name with optional tera prefix
        action_str = move_name
        if tera_used_this_turn[pid]:
            action_str = f"{tera_used_this_turn[pid]};{action_str}"
        
        # Add to prev_actions for environment
        if not is_switch_move:
            prev_actions.append({"player": pid, "move": action_str})
        else:
            # For switch moves, we'll update this later when we know what pokemon is switched to
            prev_actions.append({"player": pid, "move": action_str, "is_switch_move": True})

        # Update active pokemon and add moves
        # In the |move| block for p1:
        if pid == "p1":
            active_pokemon[pid] = species
            if species in players_pokemon[pid]:
                if move_name:  # Remove: and not is_switch_move
                    add_move_to_pokemon(players_pokemon[pid][species]["moves"], move_name)
                    
                    # Check if opponent has Pressure
                    opponent_pid = "p2"
                    opponent_species = active_pokemon.get(opponent_pid)
                    target_has_pressure = False
                    if opponent_species and opponent_species in players_pokemon[opponent_pid]:
                        opponent_ability = players_pokemon[opponent_pid][opponent_species].get("ability")
                        target_has_pressure = (opponent_ability == "Pressure")
                    
                    # Decrement PP when move is used
                    use_move(players_pokemon[pid][species]["moves"], move_name, target_has_pressure)

        # And for p2:
        else:
            active_pokemon[pid] = species
            if species not in players_pokemon["p2"]:
                players_pokemon["p2"][species] = init_pokemon(species)
            if move_name:  # Remove: and not is_switch_move
                add_move_to_pokemon(players_pokemon["p2"][species]["moves"], move_name)
                
                # Check if opponent has Pressure
                opponent_pid = "p1"
                opponent_species = active_pokemon.get(opponent_pid)
                target_has_pressure = False
                if opponent_species and opponent_species in players_pokemon[opponent_pid]:
                    opponent_ability = players_pokemon[opponent_pid][opponent_species].get("ability")
                    target_has_pressure = (opponent_ability == "Pressure")
                
                # Decrement PP when move is used
                use_move(players_pokemon["p2"][species]["moves"], move_name, target_has_pressure)

    elif line.startswith("|switch|"):
        parts = line.split("|")
        full_id = parts[2]
        pid_raw = full_id.split(":", 1)[0]
        species_raw = parts[3].split(",")[0].strip()
        species = standardize_species(species_raw)
        pid = "p1" if pid_raw.startswith("p1") else "p2" if pid_raw.startswith("p2") else None

        if pid is None:
            continue

        # Update active pokemon unconditionally
        active_pokemon[pid] = species
        # Check if this switch was caused by a switch move
        # Look for the most recent action by this player that is marked as a switch move
        found_switch_move = False
        for i in range(len(prev_actions) - 1, -1, -1):
            if prev_actions[i].get("player") == pid and prev_actions[i].get("is_switch_move"):
                # Update the action with the pokemon that was switched to
                # Current format is "move:u-turn" or "tera;move:u-turn"
                current_move = prev_actions[i]["move"]
                prev_actions[i]["move"] = f"{current_move}:{species}"  # becomes "move:u-turn:ninetails" or "tera;move:u-turn:ninetails"
                prev_actions[i].pop("is_switch_move", None)
                found_switch_move = True
                break
        
        # If no switch move caused this, check if it's a faint-triggered switch or a direct switch
        if not found_switch_move:
            # Check if this player already has a move action this turn
            last_move_idx = None
            for i in range(len(prev_actions) - 1, -1, -1):
                if prev_actions[i].get("player") == pid and "move" in prev_actions[i]:
                    last_move_idx = i
                    break
            
            # Check if this switch immediately follows a faint
            if player_just_fainted[pid]:
                # Faint-triggered switch
                if last_move_idx is not None:
                    # Append to existing move: "move:x" becomes "move:x,faint:species"
                    prev_actions[last_move_idx]["move"] += f",faint:{species}"
                else:
                    # No prior move, create new action
                    action_str = f"faint:{species}"
                    if tera_used_this_turn[pid]:
                        action_str = f"{tera_used_this_turn[pid]};{action_str}"
                    prev_actions.append({"player": pid, "move": action_str})
                player_just_fainted[pid] = False  # Reset after use
            else:
                # Hard switch (no prior move, no faint)
                action_str = f"switch:{species}"
                if tera_used_this_turn[pid]:
                    action_str = f"{tera_used_this_turn[pid]};{action_str}"
                prev_actions.append({"player": pid, "move": action_str})

            if pid == "p1":
                active_pokemon[pid] = species
                if species in players_pokemon[pid]:
                    # Reset stat changes on switch-in
                    players_pokemon[pid][species]["stat_changes"] = None
                    # Reset toxic counter to 1 if toxic, otherwise 0
                    if players_pokemon[pid][species]["status_effects"] == "toxic":
                        players_pokemon[pid][species]["toxic_counter"] = 1
                    else:
                        players_pokemon[pid][species]["toxic_counter"] = 0
                    if len(parts) > 4 and "/" in parts[4]:
                        hp_str = parts[4].split("/")[0]
                        players_pokemon[pid][species]["health"] = int(hp_str) if hp_str.isdigit() else 100
                    else:
                        players_pokemon[pid][species]["health"] = 100
            else:
                active_pokemon[pid] = species
                if species not in players_pokemon["p2"]:
                    players_pokemon["p2"][species] = init_pokemon(species)
                
                # Reset stat changes on switch-in
                players_pokemon["p2"][species]["stat_changes"] = None
                # Reset toxic counter to 1 if toxic, otherwise 0
                if players_pokemon[pid][species]["status_effects"] == "toxic":
                    players_pokemon[pid][species]["toxic_counter"] = 1
                else:
                    players_pokemon[pid][species]["toxic_counter"] = 0
                if len(parts) > 4 and "/" in parts[4]:
                    hp_str = parts[4].split("/")[0]
                    players_pokemon["p2"][species]["health"] = int(hp_str) if hp_str.isdigit() else 100
                else:
                    players_pokemon["p2"][species]["health"] = 100
    # Forced switches from Roar/Whirlwind/Dragon Tail (uses |drag| tag)
    elif line.startswith("|drag|"):
        parts = line.split("|")
        full_id = parts[2]
        pid_raw = full_id.split(":", 1)[0]
        species_raw = parts[3].split(",")[0].strip()
        species = standardize_species(species_raw)
        pid = "p1" if pid_raw.startswith("p1") else "p2" if pid_raw.startswith("p2") else None

        if pid is None:
            continue

        # *** ALWAYS update active_pokemon for drag ***
        active_pokemon[pid] = species

        # Reset stat changes and toxic counter on switch-in
        if pid == "p1":
            if species in players_pokemon[pid]:
                players_pokemon[pid][species]["stat_changes"] = None
                if players_pokemon[pid][species]["status_effects"] == "toxic":
                    players_pokemon[pid][species]["toxic_counter"] = 1
                else:
                    players_pokemon[pid][species]["toxic_counter"] = 0
                if len(parts) > 4 and "/" in parts[4]:
                    hp_str = parts[4].split("/")[0]
                    players_pokemon[pid][species]["health"] = int(hp_str) if hp_str.isdigit() else 100
                else:
                    players_pokemon[pid][species]["health"] = 100
        else:
            if species not in players_pokemon["p2"]:
                players_pokemon["p2"][species] = init_pokemon(species)
            players_pokemon["p2"][species]["stat_changes"] = None
            if players_pokemon["p2"][species]["status_effects"] == "toxic":
                players_pokemon["p2"][species]["toxic_counter"] = 1
            else:
                players_pokemon["p2"][species]["toxic_counter"] = 0
            if len(parts) > 4 and "/" in parts[4]:
                hp_str = parts[4].split("/")[0]
                players_pokemon["p2"][species]["health"] = int(hp_str) if hp_str.isdigit() else 100
            else:
                players_pokemon["p2"][species]["health"] = 100

    # Can't move (flinch, paralysis, sleep, freeze, etc.)
    elif line.startswith("|cant|"):
        parts = line.split("|")
        # Format: |cant|p1a: Garchomp|flinch
        # or: |cant|p1a: Garchomp|par (for paralysis)
        full_id = parts[2] if len(parts) > 2 else None
        reason = parts[3] if len(parts) > 3 else None
        
        if full_id is None or reason is None:
            continue
        
        pid_raw = full_id.split(":", 1)[0]
        pid = "p1" if pid_raw.startswith("p1") else "p2" if pid_raw.startswith("p2") else None
        
        if pid is None:
            continue
        
        # Format action as "null" or "tera-type;null" (only show tera on the turn it's used)
        action_str = "null"
        if tera_used_this_turn[pid]:
            action_str = f"{tera_used_this_turn[pid]};null"
        
        prev_actions.append({"player": pid, "move": action_str})


    # Damage / Heal
    elif line.startswith("|-damage|") or line.startswith("|-heal|"):
        parts = line.split("|")
        species_str = parts[2]
        pid = "p1" if species_str.startswith("p1") else "p2" if species_str.startswith("p2") else None
        if pid is None:
            continue
        
        # Extract item if present: [from] item: ItemName
        item_name = None
        move_name = None
        if len(parts) > 4 and "[from]" in parts[4]:
            from_str = parts[4]
            if "item:" in from_str:
                item_name = from_str.split("item:")[-1].strip()
            if "move:" in from_str:
                move_name = from_str.split("move:")[-1].strip()
        
        if pid == "p1":
            # p1: generalize by species
            species = active_pokemon.get(pid)
            if not species:
                continue
            hp = parts[3].split("/")[0] if "/" in parts[3] else parts[3].split()[0]
            if species in players_pokemon[pid]:
                players_pokemon[pid][species]["health"] = int(hp)
                if item_name and not players_pokemon[pid][species]["item"]:
                    players_pokemon[pid][species]["item"] = item_name
                # detect move-sourced volatiles (Salt Cure, Leech Seed, Taunt, Encore, Psychic Noise)
                if move_name:
                    mn = move_name.lower().strip()
                    move_to_volatile = {
                        'salt cure': 'salt_cure',
                        'leech seed': 'leech_seed',
                        'taunt': 'taunt',
                        'encore': 'encore',
                        'psychic noise': 'psychic_noise'
                    }
                    if mn in move_to_volatile:
                        vol = move_to_volatile[mn]
                        if vol not in players_pokemon[pid][species]['volatile_effects']:
                            players_pokemon[pid][species]['volatile_effects'][vol] = True
                            prev_actions.append({
                                'player': pid,
                                'volatile_start': {'effect': vol, 'source_move': move_name}
                            })
        else:
            # p2: use species, only update if we know it
            species = active_pokemon.get(pid)
            if not species:
                continue
            hp = parts[3].split("/")[0] if "/" in parts[3] else parts[3].split()[0]
            if species not in players_pokemon["p2"]:
                players_pokemon["p2"][species] = init_pokemon(species)
            players_pokemon["p2"][species]["health"] = int(hp)
            if item_name and not players_pokemon["p2"][species]["item"]:
                players_pokemon["p2"][species]["item"] = item_name
            # detect move-sourced volatiles for p2
            if move_name:
                mn = move_name.lower().strip()
                move_to_volatile = {
                    'salt cure': 'salt_cure',
                    'leech seed': 'leech_seed',
                    'taunt': 'taunt',
                    'encore': 'encore',
                    'psychic noise': 'psychic_noise'
                }
                if mn in move_to_volatile:
                    vol = move_to_volatile[mn]
                    if vol not in players_pokemon['p2'][species]['volatile_effects']:
                        players_pokemon['p2'][species]['volatile_effects'][vol] = True
                        prev_actions.append({
                            'player': 'p2',
                            'volatile_start': {'effect': vol, 'source_move': move_name}
                        })
    # After the |-damage| and |-heal| blocks, add a new block for item removal:

    # Item removal (Knock Off, Trick, etc.)
    elif line.startswith("|-enditem|"):
        parts = line.split("|")
        # Format: |-enditem|p2a: Pokemon|Item Name|[from] move: Knock Off
        pokemon_info = parts[2] if len(parts) > 2 else None
        
        if not pokemon_info:
            continue
        
        pid_raw = pokemon_info.split(":", 1)[0]
        pid = "p1" if pid_raw.startswith("p1") else "p2" if pid_raw.startswith("p2") else None
        
        if pid is None:
            continue
        
        species = active_pokemon.get(pid)
        if not species:
            continue
        
        # Ensure pokemon exists
        if pid == "p1":
            if species in players_pokemon[pid]:
                players_pokemon[pid][species]["item"] = ""  # Empty string = item removed
        else:
            if species not in players_pokemon["p2"]:
                players_pokemon["p2"][species] = init_pokemon(species)
            players_pokemon["p2"][species]["item"] = ""  # Empty string = item removed

    # Faint
    elif line.startswith("|faint|"):
        parts = line.split("|")
        species_str = parts[2] if len(parts) > 2 else ""
        pid = "p1" if species_str.startswith("p1") else "p2" if species_str.startswith("p2") else None
        if pid is None:
            continue
        
        # Use the currently active pokemon species
        species = active_pokemon.get(pid)
        if not species:
            continue
        
        if pid == "p1":
            if species in players_pokemon[pid]:
                players_pokemon[pid][species]["health"] = 0
        else:
            if species not in players_pokemon["p2"]:
                players_pokemon["p2"][species] = init_pokemon(species)
            players_pokemon["p2"][species]["health"] = 0
        
        player_just_fainted[pid] = True

    # Hazards (Stealth Rock, Spikes, Toxic Spikes, etc.)
    # Side effects (Aurora Veil, Light Screen, Reflect, Tailwind, etc.)
    elif line.startswith("|-sidestart|"):
        parts = line.split("|")
        # Format: |-sidestart|p1: playername|effect_name or |-sidestart|p1: playername|move: effect_name
        player_info = parts[2]  # "p1: playername" or "p2: playername"
        effect_info = parts[3] if len(parts) > 3 else ""
        
        # Extract player ID
        pid = "p1" if player_info.startswith("p1") else "p2" if player_info.startswith("p2") else None
        if pid is None:
            continue
        
        # Extract effect name (remove "move: " prefix if present)
        effect_name = effect_info.replace("move: ", "").strip().lower()
        
        # Check if this is a duration-based side condition or a hazard
        if effect_name in SIDE_CONDITION_DURATIONS:
            # Store as turns remaining, not layer count
            hazards[pid][effect_name] = SIDE_CONDITION_DURATIONS[effect_name]
        elif effect_name:
            # Regular hazards - track layer count
            hazards[pid][effect_name] = hazards[pid].get(effect_name, 0) + 1

    # Remove hazards
    # Remove side effects
    elif line.startswith("|-sideend|"):
        parts = line.split("|")
        player_info = parts[2]
        effect_info = parts[3] if len(parts) > 3 else ""
        
        pid = "p1" if player_info.startswith("p1") else "p2" if player_info.startswith("p2") else None
        if pid is None:
            continue
        
        effect_name = effect_info.replace("move: ", "").strip().lower()
        if effect_name and effect_name in hazards[pid]:
            del hazards[pid][effect_name]
    # Terastallization
    elif line.startswith("|-terastallize|"):
        parts = line.split("|")
        pokemon_info = parts[2]
        tera_type = parts[3] if len(parts) > 3 else None
        pid_raw = pokemon_info.split(":", 1)[0]
        pid = "p1" if pid_raw.startswith("p1") else "p2" if pid_raw.startswith("p2") else None
        
        # Use active pokemon instead of parsing from identifier
        species = active_pokemon.get(pid)
        
        if pid and species and tera_type:
            # Track tera type for this turn (normalize to lowercase)
            tera_used_this_turn[pid] = tera_type.lower()
            # Mark that this player has used tera this game
            tera_used_this_game[pid] = True
            
            if pid == "p1":
                if species in players_pokemon[pid]:
                    players_pokemon[pid][species]["tera_type"] = tera_type
            else:
                if species not in players_pokemon["p2"]:
                    players_pokemon["p2"][species] = init_pokemon(species)
                players_pokemon[pid][species]["tera_type"] = tera_type
    # Abilities
    elif line.startswith("|-ability|"):
        parts = line.split("|")
        # Format: |-ability|p2a: Corviknight|Pressure
        pokemon_info = parts[2] if len(parts) > 2 else None
        ability_name = parts[3] if len(parts) > 3 else None
        
        if not (pokemon_info and ability_name):
            continue
        
        pid_raw = pokemon_info.split(":", 1)[0]
        pid = "p1" if pid_raw.startswith("p1") else "p2" if pid_raw.startswith("p2") else None
        
        if pid is None:
            continue
        
        # Use active pokemon
        species = active_pokemon.get(pid)
        if not species:
            continue
        
        # Set ability
        if pid == "p1":
            if species in players_pokemon[pid]:
                players_pokemon[pid][species]["ability"] = ability_name
        else:
            if species not in players_pokemon["p2"]:
                players_pokemon["p2"][species] = init_pokemon(species)
            players_pokemon["p2"][species]["ability"] = ability_name

    # Form changes (Mimikyu Disguise breaking, Cramorant forms, etc.)
    elif line.startswith("|detailschange|"):
        parts = line.split("|")
        # Format: |detailschange|p2a: Mimikyu|Mimikyu-Busted, M
        pokemon_info = parts[2] if len(parts) > 2 else None
        new_details = parts[3] if len(parts) > 3 else None
        
        if not (pokemon_info and new_details):
            continue
        
        pid_raw = pokemon_info.split(":", 1)[0]
        pid = "p1" if pid_raw.startswith("p1") else "p2" if pid_raw.startswith("p2") else None
        
        if pid is None:
            continue
        
        # Extract new species form
        new_species = new_details.split(",")[0].strip()
        
        # Get the current species
        old_species = active_pokemon.get(pid)
        
        if old_species and new_species and old_species != new_species:
            # Copy data from old form to new form
            if old_species in players_pokemon[pid]:
                players_pokemon[pid][new_species] = players_pokemon[pid][old_species].copy()
                players_pokemon[pid][new_species]["species"] = new_species
                # Update active pokemon tracker
                active_pokemon[pid] = new_species

    # Volatile effects start (taunt, encore, leechseed, etc.)
    elif line.startswith("|-start|"):
        parts = line.split("|")
        pokemon_info = parts[2] if len(parts) > 2 else None
        effect = parts[3] if len(parts) > 3 else None
        extra = parts[4] if len(parts) > 4 else None
        
        if not (pokemon_info and effect):
            continue
        
        pid_raw = pokemon_info.split(":", 1)[0]
        pid = "p1" if pid_raw.startswith("p1") else "p2" if pid_raw.startswith("p2") else None
        
        if pid is None:
            continue
        
        # Use active pokemon instead
        species = active_pokemon.get(pid)
        if not species:
            continue
        
        # Ensure pokemon exists
        if pid == "p1":
            if species not in players_pokemon[pid]:
                players_pokemon[pid][species] = init_pokemon(species)
        else:
            if species not in players_pokemon["p2"]:
                players_pokemon["p2"][species] = init_pokemon(species)
        
        eff_key = effect.lower()
        players_pokemon[pid][species]['volatile_effects'][eff_key] = extra if extra else True
        
        # Only record as action if it's an immobilizing volatile effect
        if eff_key in IMMOBILIZING_VOLATILE:
            prev_actions.append({
                'player': pid,
                'action': eff_key.capitalize()
            })
        else:
            prev_actions.append({
                'player': pid,
                'volatile_start': {'effect': eff_key, 'extra': extra}
            })

    # Volatile effects end
    elif line.startswith("|-end|"):
        parts = line.split("|")
        pokemon_info = parts[2] if len(parts) > 2 else None
        effect = parts[3] if len(parts) > 3 else None
        if not pokemon_info:
            continue
        pid_raw = pokemon_info.split(":", 1)[0]
        species = active_pokemon.get(pid)
        if not species:
            continue
        pid = "p1" if pid_raw.startswith("p1") else "p2" if pid_raw.startswith("p2") else None
        if pid is None:
            continue
        if pid == "p1":
            if species not in players_pokemon[pid]:
                players_pokemon[pid][species] = init_pokemon(species)
        else:
            if species not in players_pokemon["p2"]:
                players_pokemon["p2"][species] = init_pokemon(species)
        if effect:
            eff_key = effect.lower()
            if eff_key in players_pokemon[pid][species]['volatile_effects']:
                players_pokemon[pid][species]['volatile_effects'].pop(eff_key, None)
            prev_actions.append({
                'player': pid,
                'volatile_end': {'effect': eff_key}
            })

    # Single-turn volatile (expires next turn)
    elif line.startswith("|-singleturn|"):
        parts = line.split("|")
        pokemon_info = parts[2] if len(parts) > 2 else None
        effect = parts[3] if len(parts) > 3 else None
        if not (pokemon_info and effect):
            continue
        pid_raw = pokemon_info.split(":", 1)[0]
        species = active_pokemon.get(pid)
        if not species:
            continue
        pid = "p1" if pid_raw.startswith("p1") else "p2" if pid_raw.startswith("p2") else None
        if pid is None:
            continue
        if pid == "p1":
            if species not in players_pokemon[pid]:
                players_pokemon[pid][species] = init_pokemon(species)
        else:
            if species not in players_pokemon["p2"]:
                players_pokemon["p2"][species] = init_pokemon(species)
        eff_key = effect.lower()
        players_pokemon[pid][species]['volatile_effects'][eff_key] = {'single_turn': True}
        prev_actions.append({
            'player': pid,
            'volatile_start': {'effect': eff_key, 'single_turn': True}
        })

    # Cure a volatile effect explicitly
    elif line.startswith("|-curevolatile|"):
        parts = line.split("|")
        pokemon_info = parts[2] if len(parts) > 2 else None
        effect = parts[3] if len(parts) > 3 else None
        if not pokemon_info:
            continue
        pid_raw = pokemon_info.split(":", 1)[0]
        species = active_pokemon.get(pid)
        if not species:
            continue
        pid = "p1" if pid_raw.startswith("p1") else "p2" if pid_raw.startswith("p2") else None
        if pid is None:
            continue
        if pid == "p1":
            if species not in players_pokemon[pid]:
                players_pokemon[pid][species] = init_pokemon(species)
        else:
            if species not in players_pokemon["p2"]:
                players_pokemon["p2"][species] = init_pokemon(species)
        if effect:
            eff_key = effect.lower()
            players_pokemon[pid][species]['volatile_effects'].pop(eff_key, None)
            prev_actions.append({
                'player': pid,
                'volatile_end': {'effect': eff_key, 'cured': True}
            })

    # Stat changes: boosts, unboosts, and setboosts (e.g., Dragon Dance)
    elif line.startswith("|-boost|") or line.startswith("|-unboost|") or line.startswith("|-setboost|"):
        parts = line.split("|")
        event_type = parts[1] if len(parts) > 1 else None
        pokemon_info = parts[2] if len(parts) > 2 else None
        stat = parts[3] if len(parts) > 3 else None
        amount = parts[4] if len(parts) > 4 else None

        if not (event_type and pokemon_info and stat and amount):
            continue

        pid_raw = pokemon_info.split(":", 1)[0]
        pid = "p1" if pid_raw.startswith("p1") else "p2" if pid_raw.startswith("p2") else None
        
        # FIX: Use active_pokemon, not extract from pokemon_info
        # The boost line shows the target, but we need to track by active pokemon
        species = active_pokemon.get(pid)
        
        # If no active pokemon yet, skip (shouldn't happen in normal games)
        if not species:
            continue
        
        if pid is None:
            continue

        # Ensure the pokemon exists in our tracking structures
        if pid == "p1":
            if species not in players_pokemon[pid]:
                players_pokemon[pid][species] = init_pokemon(species)
        else:
            if species not in players_pokemon["p2"]:
                players_pokemon["p2"][species] = init_pokemon(species)

        # Initialize stat_changes dict if needed
        if players_pokemon[pid][species]["stat_changes"] is None:
            players_pokemon[pid][species]["stat_changes"] = {}

        try:
            amt = int(amount)
        except ValueError:
            continue

        current = players_pokemon[pid][species]["stat_changes"].get(stat, 0)
        if event_type == "-boost":
            new_stage = current + amt
            change = amt
        elif event_type == "-unboost":
            new_stage = current - amt
            change = -amt
        else:  # -setboost
            new_stage = amt
            change = amt - current

        players_pokemon[pid][species]["stat_changes"][stat] = new_stage

        # Record the stat change
        prev_actions.append({
            "player": pid,
            "stat_change": {"stat": stat, "change": change, "new_stage": new_stage}
        })

    # Status effects (poison, paralysis, burn, freeze, sleep, toxic)
    elif line.startswith("|-status|"):
        parts = line.split("|")
        pokemon_info = parts[2] if len(parts) > 2 else None
        status = parts[3] if len(parts) > 3 else None
        
        if not (pokemon_info and status):
            continue
        
        pid_raw = pokemon_info.split(":", 1)[0]
        pid = "p1" if pid_raw.startswith("p1") else "p2" if pid_raw.startswith("p2") else None  # ← Move this up
        if pid is None:
            continue
        
        species = active_pokemon.get(pid)  # ← Now pid exists
        if not species:
            continue
        
        # Ensure the pokemon exists
        if pid == "p1":
            if species not in players_pokemon[pid]:
                players_pokemon[pid][species] = init_pokemon(species)
        else:
            if species not in players_pokemon["p2"]:
                players_pokemon["p2"][species] = init_pokemon(species)
        
        # Map shorthand status to full name
        status_map = {
            "psn": "poison",
            "brn": "burn",
            "par": "paralysis",
            "frz": "freeze",
            "slp": "sleep",
            "tox": "toxic",
        }
        full_status = status_map.get(status, status)
        
        players_pokemon[pid][species]["status_effects"] = full_status
        
        # Initialize toxic counter
        if full_status == "toxic":
            players_pokemon[pid][species]["toxic_counter"] = 1
        
        # Record action if immobilizing
        if full_status in IMMOBILIZING_STATUS:
            prev_actions.append({
                "player": pid,
                "action": f"{full_status.capitalize()}"
            })
    
    # Clear status effects (cured)
    elif line.startswith("|-curestatus|"):
        parts = line.split("|")
        # Format: |-curestatus|p1a: Salamence|psn (or just p1a: Salamence if cleared)
        pokemon_info = parts[2] if len(parts) > 2 else None
        
        if not pokemon_info:
            continue
        
        pid_raw = pokemon_info.split(":", 1)[0]
        pid = "p1" if pid_raw.startswith("p1") else "p2" if pid_raw.startswith("p2") else None  # ← Move this up
        if pid is None:
            continue
        
        species = active_pokemon.get(pid)  # ← Now pid exists
        if not species:
            continue
        
        # Ensure the pokemon exists
        if pid == "p1":
            if species not in players_pokemon[pid]:
                players_pokemon[pid][species] = init_pokemon(species)
        else:
            if species not in players_pokemon["p2"]:
                players_pokemon["p2"][species] = init_pokemon(species)
        
        players_pokemon[pid][species]["status_effects"] = None
        players_pokemon[pid][species]["toxic_counter"] = 0  # Reset toxic counter on cure
        prev_actions.append({
            "player": pid,
            "status_effect": "cured"
        })

    # Weather
    elif line.startswith("|-weather|"):
        parts = line.split("|")
        weather = None if (len(parts) > 2 and parts[2] == "none") else (parts[2] if len(parts) > 2 else None)
        state_template["environment"]["weather"] = weather
        
        # Set weather duration (default 5 turns, or check mapping)
        if weather:
            weather_key = weather.lower().replace(" ", "")
            duration = WEATHER_DURATIONS.get(weather_key, 5)
            state_template["environment"]["weather_turns"] = duration
        else:
            state_template["environment"]["weather_turns"] = None

    # Terrain
    elif line.startswith("|-terrain|"):
        parts = line.split("|")
        terrain = None if (len(parts) > 2 and parts[2] == "none") else (parts[2] if len(parts) > 2 else None)
        current_terrain = terrain
        state_template["environment"]["terrain"] = terrain
        
        # Set terrain duration
        if terrain:
            terrain_key = terrain.lower().replace(" ", "")
            duration = TERRAIN_DURATIONS.get(terrain_key, 5)
            state_template["environment"]["terrain_turns"] = duration
        else:
            state_template["environment"]["terrain_turns"] = None

    # Field effects start (Trick Room, Tailwind, etc.)
    elif line.startswith("|-fieldstart|"):
        parts = line.split("|")
        effect_info = parts[2] if len(parts) > 2 else ""
        # Extract effect name (remove "move: " prefix if present)
        effect_name = effect_info.replace("move: ", "").strip()
        
        if effect_name and effect_name not in field_effects:
            field_effects.append(effect_name)
            state_template["environment"]["field_effects"] = field_effects.copy()
            
            # Set duration for this field effect
            effect_key = effect_name.lower()
            duration = FIELD_EFFECT_DURATIONS.get(effect_key, 5)
            state_template["environment"]["field_effects_turns"][effect_name] = duration

    # Field effects end
    elif line.startswith("|-fieldend|"):
        parts = line.split("|")
        effect_info = parts[2] if len(parts) > 2 else ""
        effect_name = effect_info.replace("move: ", "").strip()
        
        if effect_name and effect_name in field_effects:
            field_effects.remove(effect_name)
            state_template["environment"]["field_effects"] = field_effects.copy()
            
            # Remove duration tracking for this effect
            if effect_name in state_template["environment"]["field_effects_turns"]:
                del state_template["environment"]["field_effects_turns"][effect_name]

    # Winner
    elif line.startswith("|win|"):
        parts = line.split("|")
        winner_name = parts[2] if len(parts) > 2 else None
        
        # Map player name to player ID
        if winner_name:
            if winner_name == player_names["p1"]:
                battle_winner = "p1"
            elif winner_name == player_names["p2"]:
                battle_winner = "p2"
            else:
                battle_winner = None
        else:
            battle_winner = None

# ---------- Save last turn ----------
if current_turn > 0 and prev_actions:  # Only save if there are actions
    snapshot = deepcopy(state_template)
    snapshot["player1"]["pokemon"] = deepcopy(list(players_pokemon["p1"].values()))
    snapshot["player2"]["pokemon"] = deepcopy(list(players_pokemon["p2"].values()))
    snapshot["environment"]["active_pokemon"] = active_pokemon.copy()
    snapshot["environment"]["prev_actions"] = prev_actions.copy()
    snapshot["environment"]["hazards"] = {"p1": hazards["p1"].copy(), "p2": hazards["p2"].copy()}
    snapshot["environment"]["tera_used"] = tera_used_this_game.copy()
    all_turns.append(snapshot)

# ---------- Convert moves to list format with PP tracking ----------
def convert_moves_to_list(pokemon_list):
    """Convert moves dict to list while preserving PP tracking."""
    for pokemon in pokemon_list:
        if isinstance(pokemon["moves"], dict):
            pokemon["moves"] = list(pokemon["moves"].values())
    return pokemon_list

# Convert all snapshots
for snapshot in all_turns:
    snapshot["player1"]["pokemon"] = convert_moves_to_list(snapshot["player1"]["pokemon"])
    snapshot["player2"]["pokemon"] = convert_moves_to_list(snapshot["player2"]["pokemon"])

# ---------- Set winner on all snapshots ----------
if battle_winner:
    for turn in all_turns:
        turn["winner"] = battle_winner

# ---------- Fill in p2_next_move for each turn ----------
for idx, turn in enumerate(all_turns):
    turn["winner"] = battle_winner
    
    if idx == 0:  # Team preview - P2's lead
        # Use P2's lead from index 1 (lead state)
        if len(all_turns) > 1:
            p2_lead = all_turns[1]["environment"]["active_pokemon"].get("p2")
            turn["p2_next_move"] = f"switch:{p2_lead}" if p2_lead else None
    else:  # Normal turns - P2's first action in next turn
        if idx + 1 < len(all_turns):
            next_actions = all_turns[idx + 1]["environment"].get("prev_actions", [])
            for act in next_actions:
                if act.get("player") == "p2" and act.get("move"):
                    turn["p2_next_move"] = act.get("move")
                    break
        else:
            turn["p2_next_move"] = None

# Convert moves dict to list for all snapshots
for snapshot in all_turns:
    snapshot["player1"]["pokemon"] = convert_moves_to_list(snapshot["player1"]["pokemon"])
    snapshot["player2"]["pokemon"] = convert_moves_to_list(snapshot["player2"]["pokemon"])

# ---------- Write output ----------
with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(all_turns, f, indent=2)
