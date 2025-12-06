from enum import Enum
from pathlib import Path


class SaveReplay(Enum):
    Never = "never"
    Always = "always"
    OnLoss = "onloss"


class GbolshnikConfig:
    pokemon_mode = "gen9ou"
    battle_bot_module = "default"
    log_to_file = False
    file_log_handler = None
    save_replay = SaveReplay.Always  # Save replays after every turn
    username = None
    smogon_stats = None
    replay_log_dir = str(Path(__file__).parent / "replays")  # Directory to save turn-by-turn replays

