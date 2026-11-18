"""Final model: real-part variants (weight 3) + synthetic parts (weight 1)."""
import sys, json, numpy as np, pandas as pd, joblib
from sklearn.ensemble import HistGradientBoostingClassifier
real = pd.read_csv(sys.argv[1]); synth = pd.read_csv(sys.argv[2])
FEATS = [c for c in real.columns if c not in {"part", "family", "key", "label"}]
for c in FEATS:
    if c not in synth.columns: synth[c] = 0
both = pd.concat([real, synth[list(real.columns)]], ignore_index=True)
w = np.where(both.family.isin(real.family.unique()), 3.0, 1.0)
m = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.08, max_leaf_nodes=31, l2_regularization=1.0,
                                   random_state=0).fit(both[FEATS].astype(float), both.label, sample_weight=w)
joblib.dump(dict(model=m, features=FEATS, threshold=0.5,
                 trained_on=f"{real.part.nunique()} variants of {real.family.nunique()} reviewed real parts (weight 3) + "
                            f"{synth.part.nunique()} synthetic parts"), sys.argv[3])
print("saved", sys.argv[3], "rows", len(both))
