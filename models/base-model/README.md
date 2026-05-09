# ML Model Artefacts

The UC-01 symptom-prediction pipeline expects a persisted scikit-learn
artefact in this directory.

## Accepted layouts

The loader in `app/clients/ml_client.py` handles any of:

1. **Full pipeline (recommended)** — `model.pkl` is a `sklearn.pipeline.Pipeline`
   whose first stage is a text vectorizer (e.g. `TfidfVectorizer`) and final
   stage is a classifier that supports `predict_proba`. No vectorizer file
   is needed.

   ```python
   from sklearn.pipeline import Pipeline
   from sklearn.feature_extraction.text import TfidfVectorizer
   from sklearn.linear_model import LogisticRegression
   import joblib

   pipe = Pipeline([
       ("tfidf", TfidfVectorizer(ngram_range=(1, 2), min_df=2)),
       ("clf",   LogisticRegression(max_iter=1000)),
   ])
   pipe.fit(X_train_text, y_train)
   joblib.dump(pipe, "models/model.pkl")
   ```

2. **Bundle** — `model.pkl` is a dict like
   `{"vectorizer": <TfidfVectorizer>, "model": <classifier>}`.

3. **Split files** — `model.pkl` is a bare classifier AND
   `vectorizer.pkl` (a fitted `TfidfVectorizer`) is present alongside it.

## Missing model

If neither `model.pkl` nor `vectorizer.pkl` is present the service falls
back to a clearly-labelled rule-based stub so the rest of the pipeline
(preprocessing, triage, KB enrichment, logging) can still be exercised end
to end during development. The stub is intentionally deterministic and is
NOT suitable for production use.

## Storage

These files are binary and should **not** be committed to git. Add them to
`.gitignore` or track them via git-lfs / an object store.
