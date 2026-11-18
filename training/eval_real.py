"""Leave-one-real-part-out evaluation and final training on real-part variants.

python eval_real.py REAL_TABLE.csv SYNTH_TABLE.csv SYNTH_MODEL.joblib OUT_MODEL.joblib OUT_METRICS.json
"""
import sys, json
import numpy as np, pandas as pd, joblib
from sklearn.ensemble import HistGradientBoostingClassifier

real = pd.read_csv(sys.argv[1]); synth = pd.read_csv(sys.argv[2]); M_syn = joblib.load(sys.argv[3])
NON = {"part", "family", "key", "label"}
FEATS = [c for c in real.columns if c not in NON]
for c in FEATS:
    if c not in synth.columns:
        synth[c] = 0
synth = synth[list(real.columns)]


def clf():
    return HistGradientBoostingClassifier(max_iter=300, learning_rate=0.08, max_leaf_nodes=31,
                                          l2_regularization=1.0, random_state=0)


def scores(yt, yp, df):
    yt = yt.astype(bool); yp = yp.astype(bool); tp = (yt & yp).sum()
    P = tp / max(1, yp.sum()); R = tp / max(1, yt.sum()); F = 2 * P * R / max(1e-9, P + R)
    exact = df.assign(ok=(yt == yp)).groupby("part")["ok"].all().mean()
    return dict(precision=round(float(P), 3), recall=round(float(R), 3), f1=round(float(F), 3),
                variants_exactly_right=round(float(exact), 3))


res = {"per_part": {}}
held_frames = []
agg = {k: ([], []) for k in ("real_only", "synthetic_only", "real_plus_synthetic")}
for held in sorted(real.family.unique()):
    tr, te = real[real.family != held], real[real.family == held]
    yt = te.label.values
    held_frames.append(te)
    m = clf().fit(tr[FEATS].astype(float), tr.label)
    p_real = m.predict_proba(te[FEATS].astype(float))[:, 1] > 0.5
    Xs = te[M_syn["features"]].astype(float).values
    p_syn = M_syn["model"].predict_proba(Xs)[:, 1] > M_syn["threshold"]
    both = pd.concat([tr, synth], ignore_index=True)
    w = np.where(both.family.isin(real.family.unique()), 3.0, 1.0)      # real variants weighted higher
    m2 = clf().fit(both[FEATS].astype(float), both.label, sample_weight=w)
    p_mix = m2.predict_proba(te[FEATS].astype(float))[:, 1] > 0.5
    res["per_part"][held] = {k: scores(yt, p, te) for k, p in
                             (("real_only", p_real), ("synthetic_only", p_syn), ("real_plus_synthetic", p_mix))}
    for k, p in (("real_only", p_real), ("synthetic_only", p_syn), ("real_plus_synthetic", p_mix)):
        agg[k][0].append(yt); agg[k][1].append(p)
    print(held, {k: v["f1"] for k, v in res["per_part"][held].items()}, flush=True)

all_te = pd.concat(held_frames, ignore_index=True)
res["overall"] = {k: scores(np.concatenate(a), np.concatenate(b), all_te) for k, (a, b) in agg.items()}
if len(sys.argv) > 6:                       # score only parts whose labels the reviewer actually edited
    not_edited = set(json.load(open(sys.argv[6])).get("_not_edited", []))
    mask = ~all_te.family.isin(not_edited).values
    res["edited_parts_only"] = {k: scores(np.concatenate(a)[mask], np.concatenate(b)[mask], all_te[mask])
                                for k, (a, b) in agg.items()}
    res["edited_parts_only"]["_parts"] = int(all_te[mask].family.nunique())
    print("edited parts only:", json.dumps(res["edited_parts_only"], indent=1))
print(json.dumps(res["overall"], indent=1))

final = clf().fit(real[FEATS].astype(float), real.label)
joblib.dump(dict(model=final, features=FEATS, threshold=0.5,
                 trained_on=f"{real.part.nunique()} variants of {real.family.nunique()} real parts"), sys.argv[4])
json.dump(res, open(sys.argv[5], "w"), indent=1)
