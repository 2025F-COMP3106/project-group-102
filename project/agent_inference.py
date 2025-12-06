"""
agent_inference.py

Utilities to run inference with the trained MultiHeadPolicy on a single parsed
Pokémon Showdown state dict.

Usage:

    from agent_inference import PokemonPolicyAgent
    from model_def import MultiHeadPolicy  # your model definition

    # load vocabs
    import json
    with open("vocab/moves2id.json") as f:
        moves2id = json.load(f)
    with open("vocab/types2id.json") as f:
        types2id = json.load(f)
    with open("vocab/species2id.json") as f:
        species2id = json.load(f)

    # build and load model
    INPUT_DIM = 512  # or 1024, must match training
    model = MultiHeadPolicy(
        input_dim=INPUT_DIM,
        n_action_types=...,  # len(ACTION_TYPE_LABELS)
        n_moves=len(moves2id),
        n_tera=len(types2id),
        n_switch_targets=len(species2id),
    )
    ckpt = torch.load("checkpoints/policy_best.pt", map_location="cpu")
    model.load_state_dict(ckpt["model_state_dict"])

    agent = PokemonPolicyAgent(model, moves2id, types2id, species2id, vector_size=INPUT_DIM)

    # given a parsed state dict
    import json
    state = json.load(open("one_parsed_turn.json", "r"))
    top5_moves, raw_outputs = agent.top5_moves(state)
"""

from __future__ import annotations

import json
from typing import Dict, Any, List, Tuple
from vectorize_states import state_to_feature_tokens
import numpy as np
import torch
import torch.nn as nn

# =====================
# Feature extraction (COPY FROM vectorize_states.py)
# =====================

def norm_move(s: str) -> str:
    return (
        s.lower()
        .replace(" ", "")
        .replace("-", "")
        .replace("'", "")
        .replace("é", "e")
    )
    
PIVOT_MOVES = {
    "U-turn",
    "Volt Switch",
    "Flip Turn",
    "Teleport",
    "Parting Shot",
    "Baton Pass",
    "Chilly Reception",
}

def _load_optional_vocab(root: str):
    """No-op here; only needed if you want prev-move parsing to use shared vocab."""
    return None, None


def _parse_prev_raw_move(raw_move: str, moves_set=None, species_set=None):
    """
    COPY YOUR IMPLEMENTATION FROM vectorize_states.py HERE.

    The current stub just returns no move / no faint / no switch / no tera.
    """
    if not raw_move or not isinstance(raw_move, str):
        return None, [], [], False
    # TODO: paste real implementation
    return None, [], [], False



def dict_to_hashed_vector(feat_dict: Dict[str, float], size: int = 1024) -> np.ndarray:
    """Convert feature dict to fixed-size dense vector with simple hashing."""
    import hashlib

    vec = np.zeros(size, dtype=np.float32)
    for k, v in feat_dict.items():
        h = hashlib.md5(k.encode("utf-8")).hexdigest()
        idx = int(h, 16) % size
        vec[idx] += float(v)
    return vec


# =====================
# Agent wrapper
# =====================

class PokemonPolicyAgent:
    """
    Wraps a trained MultiHeadPolicy model and vocab mappings to provide
    convenient inference on a single parsed state dict.
    """

    def __init__(
        self,
        model: nn.Module,
        moves2id: Dict[str, int],
        types2id: Dict[str, int],
        species2id: Dict[str, int],
        vector_size: int,
        device: str | torch.device | None = None,
        action_type_labels: Dict[str, int] | None = None,
    ):
        self.model = model
        self.moves2id = moves2id
        self.types2id = types2id
        self.species2id = species2id
        self.id2move = {v: k for k, v in moves2id.items()}
        self.id2species = {v: k for k, v in species2id.items()}
        self.vector_size = vector_size
        
        # normalized name -> move_id, based on training vocab
        self.norm_to_move_id: Dict[str, int] = {}
        for name, mid in moves2id.items():
            self.norm_to_move_id[norm_move(name)] = mid
        
        self.pivot_move_ids: set[int] = set()
        for name in PIVOT_MOVES:
            key = norm_move(name)
            mid = self.norm_to_move_id.get(key)
            if mid is not None:
                self.pivot_move_ids.add(mid)
            
        with open("../data/species_to_moves_gen9.json") as f:
            self.species_to_moves_gen9 = json.load(f)


        # reverse map for action types if provided
        self.action_type_labels = action_type_labels or {}
        self.id2actiontype = {v: k for k, v in self.action_type_labels.items()}

        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = torch.device(device)
        self.model.to(self.device)
        self.model.eval()

    def state_to_input(self, state: Dict[str, Any]) -> np.ndarray:
        feats = state_to_feature_tokens(state)
        for k in list(feats.keys()):
            if "p2_next_move" in k or "winner" in k:
                del feats[k]
        hv = dict_to_hashed_vector(feats, size=self.vector_size)
        return hv

    def forward_raw(self, state: Dict[str, Any]) -> Dict[str, torch.Tensor]:
        x_np = self.state_to_input(state)
        x = torch.from_numpy(x_np).float().unsqueeze(0).to(self.device)
        with torch.no_grad():
            outputs = self.model(x)
        return outputs

    def top5_moves(self, state: Dict[str, Any]):
        outputs = self.forward_raw(state)
        move_logits = outputs["move_id"]
        move_probs = torch.softmax(move_logits, dim=-1)
        top_probs, top_idx = torch.topk(move_probs, k=5, dim=-1)
        top_probs = top_probs[0].cpu().tolist()
        top_idx = top_idx[0].cpu().tolist()
        top_names = [self.id2move.get(i, f"<unk:{i}>") for i in top_idx]
        return list(zip(top_names, top_probs)), outputs

    def _legal_move_ids_for_active_p2(self, state: Dict[str, Any]) -> List[int] | None:
        env = state.get("environment", {}) or {}
        active_name = env.get("active_pokemon", {}).get("p2")
        if not active_name:
            return None

        legal_ids: set[int] = set()

        # 1) moves actually present on the active mon in this state
        for mon in state.get("player2", {}).get("pokemon", []) or []:
            if mon.get("species") == active_name:
                for mv in (mon.get("moves") or []):
                    n = mv.get("name")
                    if not n:
                        continue
                    nid = self.norm_to_move_id.get(norm_move(n))
                    if nid is not None:
                        legal_ids.add(nid)
                break

        # 2) Gen 9 learnset moves from your JSON
        sp_id = norm_move(active_name)  # same normalization you used before
        for move_id in self.species_to_moves_gen9.get(sp_id, []):
            nid = self.norm_to_move_id.get(norm_move(move_id))
            if nid is not None:
                legal_ids.add(nid)

        if not legal_ids:
            return None
        return sorted(legal_ids)

    def describe_action_candidates(self, state: Dict[str, Any], k: int = 5) -> None:
        """
        Print a human-readable summary of all heads:
        - MOVE / PIVOT_MOVE: top moves (and pivot subset)
        - SWITCH: top switches
        - FAINT_SWITCH: top faint targets from faint head
        - PIVOT_MOVE / TERA_PIVOT_MOVE: pivot move + expected switch after pivot
        - TERA_*: top tera types + corresponding moves/switches
        """

        def get_p2_team_ids(state, species2id):
            p2_pokemon = state["player2"]["pokemon"]
            valid_ids = []
            for mon in p2_pokemon:
                sp = mon.get("species")
                if sp in species2id:
                    valid_ids.append(species2id[sp])
            return sorted(set(valid_ids))

        outputs = self.forward_raw(state)

        # ----- action type distribution -----
        act_logits = outputs["action_type"]  # (1, N_ACTION_TYPES)
        act_probs = torch.softmax(act_logits, dim=-1)[0]  # (N_ACTION_TYPES,)
        act_probs_np = act_probs.cpu().numpy()

        ranked_actions = sorted(
            [(i, p) for i, p in enumerate(act_probs_np)],
            key=lambda x: x[1],
            reverse=True,
        )

        # heads
        move_logits   = outputs["move_id"][0]
        switch_logits = outputs["switch"][0]
        faint_logits  = outputs["faint_switch"][0]
        tera_logits   = outputs["tera"][0]

        # ----- legal masks -----

        # legal moves for p2
        legal_move_ids = self._legal_move_ids_for_active_p2(state)
        if legal_move_ids:
            move_mask = torch.full_like(move_logits, float("-inf"))
            for mid in legal_move_ids:
                if 0 <= mid < move_mask.numel():
                    move_mask[mid] = 0.0
            move_logits = move_logits + move_mask

        move_probs = torch.softmax(move_logits, dim=-1)

        # legal switch/faint targets: any species on p2 team
        p2_ids = get_p2_team_ids(state, self.species2id)
        switch_mask = torch.full_like(switch_logits, float("-inf"))
        for sid in p2_ids:
            if 0 <= sid < switch_mask.numel():
                switch_mask[sid] = 0.0

        masked_switch_logits = switch_logits + switch_mask
        masked_faint_logits  = faint_logits + switch_mask

        switch_probs = torch.softmax(masked_switch_logits, dim=-1)
        faint_probs  = torch.softmax(masked_faint_logits,  dim=-1)

        # tera type distribution (no mask; any tera type is allowed)
        tera_probs = torch.softmax(tera_logits, dim=-1)

        # ----- helper: print top-k species given probs -----
        def _print_species_block(title: str, probs: torch.Tensor, ids_to_name: Dict[int, str],
                                act_p: float, k: int = 5):
            top_probs, top_idx = torch.topk(probs, k=k, dim=-1)
            top_probs = top_probs.cpu().tolist()
            top_idx = top_idx.cpu().tolist()
            print(f"  {title}:")
            for sid, p_cond in zip(top_idx, top_probs):
                name = ids_to_name.get(sid, f"<unk_species:{sid}>")
                joint = act_p * p_cond
                print(f"    {name}: P(cond)={p_cond:.3f}, P(joint)={joint:.3f}")

        # ----- per action type -----
        for act_id, act_p in ranked_actions:
            if act_p < 1e-4 or self.id2actiontype.get(act_id) == "PIVOT_MOVE" or self.id2actiontype.get(act_id) == "TERA_PIVOT_MOVE":
                continue  # ignore tiny mass

            act_name = self.id2actiontype.get(act_id, f"action_{act_id}")
            print(f"Turn type: {act_name} (P={act_p:.3f})")

            upper = act_name.upper()

            # ============= NON-TERA PIVOT MOVE =============
            # Show pivot move + expected switch after pivot
            # if "PIVOT_MOVE" in upper and "TERA" not in upper:
            #     pivot_ids = sorted(self.pivot_move_ids.intersection(set(legal_move_ids or [])))
            #     if not pivot_ids:
            #         print("  no legal pivot moves on this set")
            #         print()
            #         continue

            #     # pivot move distribution (conditional on action being pivot)
            #     mask_pivot = torch.full_like(move_logits, float("-inf"))
            #     for mid in pivot_ids:
            #         if 0 <= mid < mask_pivot.numel():
            #             mask_pivot[mid] = 0.0
            #     pivot_logits = move_logits + mask_pivot
            #     pivot_probs = torch.softmax(pivot_logits, dim=-1)

            #     top_p, top_idx = torch.topk(pivot_probs, k=min(k, len(pivot_ids)), dim=-1)
            #     top_p = top_p.cpu().tolist()
            #     top_idx = top_idx.cpu().tolist()
            #     print("  top pivot moves (legal):")
            #     for mid, p_cond in zip(top_idx, top_p):
            #         move_name = self.id2move.get(mid, f"<unk:{mid}>")
            #         joint_move = act_p * p_cond
            #         print(f"    {move_name}: P(cond)={p_cond:.3f}, P(joint)={joint_move:.3f}")

            # ============= NON-TERA MOVE =============
            if "MOVE" in upper and "PIVOT" not in upper and "TERA" not in upper:
                top_probs, top_idx = torch.topk(move_probs, k=5, dim=-1)
                top_probs = top_probs.cpu().tolist()
                top_idx = top_idx.cpu().tolist()
                print("  top moves:")
                for pid, p_cond in zip(top_idx, top_probs):
                    name = self.id2move.get(pid, f"<unk:{pid}>")
                    joint = act_p * p_cond
                    print(f"    {name}: P(cond)={p_cond:.3f}, P(joint)={joint:.3f}")

            # ============= PLAIN SWITCH =============
            if "SWITCH" in upper and "FAINT" not in upper and "TERA" not in upper:
                _print_species_block("top switches", switch_probs, self.id2species, act_p, k=k)

            # ============= TERA VARIANTS =============
            if "TERA" in upper:
                # top tera types
                TERA_AMOUNT = 2
                top_t, top_t_idx = torch.topk(tera_probs, k=min(2, tera_probs.numel()), dim=-1)
                top_t = top_t.cpu().tolist()
                top_t_idx = top_t_idx.cpu().tolist()
                print("  top tera types:")
                id2tera = {v: k for k, v in self.types2id.items()}
                for tid, p_cond in zip(top_t_idx, top_t):
                    tname = id2tera.get(tid, f"<unk_tera:{tid}>")
                    joint = act_p * p_cond
                    print(f"    {tname}: P(cond)={p_cond:.3f}, P(joint)={joint:.3f}")
        
            print()
            
    def p1_win_prob(self, state: Dict[str, Any]) -> float:
        """
        Return probability (0-1) that player1 wins this game state,
        as predicted by the trained WinPredictor.
        """
        if not hasattr(self, "win_model") or self.win_model is None:
            raise RuntimeError("win_model not attached to agent")

        x_np = self.state_to_input(state)
        x = torch.from_numpy(x_np).float().unsqueeze(0).to(self.device)

        with torch.no_grad():
            logit = self.win_model(x)  # (1,)
            prob = torch.sigmoid(logit)[0].item()

        return prob



class MultiHeadPolicy(nn.Module):
    def __init__(self,
                input_dim: int,
                n_action_types: int,
                n_moves: int,
                n_tera: int,
                n_switch_targets: int):
        super().__init__()
        self.backbone = nn.Sequential(
            nn.Linear(input_dim, 1024),
            nn.ReLU(),
            nn.LayerNorm(1024),
            nn.Dropout(0.1),
            nn.Linear(1024, 512),
            nn.ReLU(),
            nn.LayerNorm(512),
            nn.Dropout(0.1),
            nn.Linear(512, 256),
            nn.ReLU(),
        )
        self.head_action_type = nn.Linear(256, n_action_types)
        self.head_move_id     = nn.Linear(256, n_moves)
        self.head_tera        = nn.Linear(256, n_tera)
        self.head_switch      = nn.Linear(256, n_switch_targets)
        self.head_faint_switch= nn.Linear(256, n_switch_targets)

    def forward(self, x: torch.Tensor) -> Dict[str, torch.Tensor]:
        h = self.backbone(x)
        return {
            "action_type": self.head_action_type(h),
            "move_id":     self.head_move_id(h),
            "tera":        self.head_tera(h),
            "switch":      self.head_switch(h),
            "faint_switch":self.head_faint_switch(h),
        }
        
class WinPredictor(nn.Module):
    def __init__(self, input_dim: int):
        super().__init__()
        self.backbone = nn.Sequential(
            nn.Linear(input_dim, 512),
            nn.ReLU(),
            nn.Linear(512, 256),
            nn.ReLU(),
        )
        self.head = nn.Linear(256, 1)  # logit for P1 win

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = self.backbone(x)
        logit = self.head(h).squeeze(-1)  # (batch,)
        return logit

with open("vocab/shared_moves2id.json") as f:
    moves2id = json.load(f)
with open("vocab/shared_types2id.json") as f:
    types2id = json.load(f)
with open("vocab/shared_species2id.json") as f:
    species2id = json.load(f)

ACTION_TYPE_MAP_PATH = "../data/gen9ou_full_action_type_map.json"  # e.g. "data/gen9ou_full_action_type_map.json"
with open(ACTION_TYPE_MAP_PATH, "r") as f:
    ACTION_TYPE_LABELS = json.load(f)
ACTION_TYPES = list(ACTION_TYPE_LABELS.keys())
N_ACTION_TYPES = len(ACTION_TYPES)               # from your existing ACTION_TYPES list
N_MOVES = len(moves2id)
N_TERA_TYPES = len(types2id)
N_SWITCH_TARGETS = len(species2id)
# build and load model
INPUT_DIM = 512  # or 1024, must match training

if __name__ == "__main__":
        
    model = MultiHeadPolicy(
        input_dim=INPUT_DIM,
        n_action_types=N_ACTION_TYPES,
        n_moves=N_MOVES,
        n_tera=N_TERA_TYPES,
        n_switch_targets=N_SWITCH_TARGETS,
    ).to("cuda")
    ckpt = torch.load("checkpoints/policy_best.pt", map_location="cpu")
    model.load_state_dict(ckpt["model_state_dict"])

    # win model
    win_model = WinPredictor(INPUT_DIM).to("cuda")
    win_ckpt = torch.load("checkpoints/win_best.pt", map_location="cpu")
    win_model.load_state_dict(win_ckpt["model_state_dict"])
    win_model.eval()

    agent = PokemonPolicyAgent(model, moves2id, types2id, species2id, vector_size=INPUT_DIM, action_type_labels=ACTION_TYPE_LABELS)
    agent.win_model = win_model 



    # given a parsed state dict
    state = json.load(open("one_parsed_turn.json", "r"))
    agent.describe_action_candidates(state, k=5)

    p1_prob = agent.p1_win_prob(state)
    print(f"P1 win chance: {p1_prob * 100:.1f}%")
    # print("Top 5 moves:", top5_moves)