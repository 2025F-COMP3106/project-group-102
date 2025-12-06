import re
import json
from pathlib import Path
import numpy as np

LEARNSETS_PATH = Path(r"data\learnsets.ts")  # adjust
OUT_JSON_PATH = Path("species_to_moves_gen9.json")

def extract_species_blocks(ts_text: str):
    """
    Extract {speciesId: { ... }} blocks from:
      export const Learnsets: ... = { ... };
    Returns dict speciesId -> full block text (including learnset: {...}).
    """
    m = re.search(
        r"export const Learnsets[^=]*=\s*\{(.*)\};",
        ts_text,
        flags=re.DOTALL,
    )
    if not m:
        raise RuntimeError("Could not find Learnsets object in learnsets.ts")
    body = m.group(1)

    # Match species blocks up to the next top-level species or end of object.
    # This avoids one block eating multiple species.
    pattern = r"(\w+)\s*:\s*\{(.*?)\n\s*\},(?=\s*\w+\s*:|\s*\}\s*;)"
    blocks = {}
    for species_id, block in re.findall(pattern, body, flags=re.DOTALL):
        blocks[species_id] = block
    return blocks

def extract_gen9_moves_from_block(block: str):
    """
    From one species block, grab learnset: { ... } and return moves that have a '9*' source.
    """
    m = re.search(r"learnset\s*:\s*\{(.*?)\}", block, flags=re.DOTALL)
    if not m:
        return []
    learnset_body = m.group(1)

    moves = []
    line_pattern = r"(\w+)\s*:\s*\[([^\]]*)\]"
    for move_id, source_list in re.findall(line_pattern, learnset_body):
        sources = re.findall(r'"([^"]+)"', source_list)
        if any(s.startswith("9") for s in sources):
            moves.append(move_id)
    return sorted(set(moves))

def main():
    text = LEARNSETS_PATH.read_text(encoding="utf-8")
    species_blocks = extract_species_blocks(text)

    species_to_moves = {}
    for species_id, block in species_blocks.items():
        moves = extract_gen9_moves_from_block(block)
        species_to_moves[species_id] = moves

    OUT_JSON_PATH.write_text(json.dumps(species_to_moves, indent=2), encoding="utf-8")
    print(f"Wrote {OUT_JSON_PATH}")

if __name__ == "__main__":
    # main()
    import json

    sample_path = "one_parsed_game.json"  # one of the files you streamed
    parsed = json.load(open(sample_path, "r"))

    print(type(parsed), len(parsed))
    print(parsed[0].keys())
    print(parsed[-1].get("winner", None))
    y_winner = np.load("data/gen9ou_full_y_winner.npy")  # shape (N,)
    print(y_winner[:10])
    print(np.unique(y_winner), y_winner.shape)
