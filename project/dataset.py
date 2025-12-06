import numpy as np
import torch
from torch.utils.data import Dataset
import bisect

class MultiFileTurnsDataset(Dataset):
    def __init__(self, prefixes, seed=42):
        self.prefixes = prefixes
        self.batch_sizes = []
        self.cum_sizes = [0]
        self._open_batch_index = None
        self._open_X = None
        self._open_y_action = None
        self._open_y_move = None
        self._open_y_tera = None
        self._open_y_switch = None
        self._open_y_faint = None
        self.perm = None

        for pref in prefixes:
            X_path = pref + "_X_hashed.npy"
            n = np.load(X_path, mmap_mode="r").shape[0]
            self.batch_sizes.append(n)
            self.cum_sizes.append(self.cum_sizes[-1] + n)

        self.n = self.cum_sizes[-1]

        # One-time global permutation of indices
        rng = np.random.default_rng(seed)
        self.perm = rng.permutation(self.n)

    def __len__(self):
        return self.n

    def _open_batch(self, bidx):
        if self._open_batch_index == bidx:
            return
        pref = self.prefixes[bidx]
        self._open_X        = np.load(pref + "_X_hashed.npy", mmap_mode="r")
        self._open_y_action = np.load(pref + "_y_action_type.npy", mmap_mode="r")
        self._open_y_move   = np.load(pref + "_y_move.npy",        mmap_mode="r")
        self._open_y_tera   = np.load(pref + "_y_tera.npy",        mmap_mode="r")
        self._open_y_switch = np.load(pref + "_y_switch.npy",      mmap_mode="r")
        self._open_y_faint  = np.load(pref + "_y_faint.npy",       mmap_mode="r")
        self._open_batch_index = bidx

    def _locate(self, real_idx):
        bidx = bisect.bisect_right(self.cum_sizes, real_idx) - 1
        local_idx = real_idx - self.cum_sizes[bidx]
        return bidx, local_idx

    def __getitem__(self, idx):
        # Map logical idx -> permuted real index
        real_idx = int(self.perm[idx])
        bidx, local_idx = self._locate(real_idx)
        self._open_batch(bidx)

        x = torch.from_numpy(self._open_X[local_idx].astype("float32"))
        labels = {
            "action_type": torch.tensor(self._open_y_action[local_idx], dtype=torch.long),
            "move_id":     torch.tensor(self._open_y_move[local_idx],   dtype=torch.long),
            "tera":        torch.tensor(self._open_y_tera[local_idx],   dtype=torch.long),
            "switch":      torch.tensor(self._open_y_switch[local_idx], dtype=torch.long),
            "faint_switch":torch.tensor(self._open_y_faint[local_idx],  dtype=torch.long),
        }
        return x, labels
        
class MultiFileWinDataset(Dataset):
    def __init__(self, prefixes, seed=42):
        self.prefixes = prefixes
        self.batch_sizes = []
        self.cum_sizes = [0]

        # indices of valid (0/1) labels per batch
        self.valid_indices_per_batch = []
        for pref in prefixes:
            y_path = pref + "_y_winner.npy"
            y = np.load(y_path, mmap_mode="r")
            mask = (y == 0) | (y == 1)
            idxs = np.nonzero(mask)[0]
            self.valid_indices_per_batch.append(idxs)
            n_valid = len(idxs)
            self.batch_sizes.append(n_valid)
            self.cum_sizes.append(self.cum_sizes[-1] + n_valid)

        self.n = self.cum_sizes[-1]

        # optional global permutation, like turns dataset
        rng = np.random.default_rng(seed)
        self.perm = rng.permutation(self.n)

        self._open_batch_index = None
        self._open_X = None
        self._open_y = None

    def __len__(self):
        return self.n

    def _open_batch(self, bidx):
        if self._open_batch_index == bidx:
            return
        pref = self.prefixes[bidx]
        self._open_X = np.load(pref + "_X_hashed.npy", mmap_mode="r")
        self._open_y = np.load(pref + "_y_winner.npy", mmap_mode="r")
        self._open_batch_index = bidx

    def _locate(self, real_idx):
        bidx = bisect.bisect_right(self.cum_sizes, real_idx) - 1
        local_pos = real_idx - self.cum_sizes[bidx]
        local_idx = self.valid_indices_per_batch[bidx][local_pos]
        return bidx, local_idx

    def __getitem__(self, idx):
        real_idx = int(self.perm[idx])
        bidx, local_idx = self._locate(real_idx)
        self._open_batch(bidx)

        x_np = self._open_X[local_idx].astype("float32")
        y_val = float(self._open_y[local_idx])  # 0 or 1

        x = torch.from_numpy(x_np)
        y = torch.tensor(y_val, dtype=torch.float32)
        return x, y