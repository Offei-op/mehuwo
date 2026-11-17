# Probabilistic skill diagnosis

The default scorer is now a fixed-parameter DINA model, replacing raw single-skill
averages as the default. The original scorer remains available with `--method simple`.
This is a model-based estimate, not a clinically or educationally validated diagnosis.

## Model and interpretation

Each binary latent profile says which skills a student has mastered. For question j,
the probability of a correct answer is `1 - slip[j]` if the profile contains every
skill marked in that question's Q-vector; otherwise it is `guess[j]`. Responses are
assumed conditionally independent given the skill profile. We enumerate profiles,
combine response likelihoods with independent Bernoulli skill priors, and marginalize
the posterior to obtain each skill's mastery probability.

Reference: Jimmy de la Torre (2009), [DINA Model and Parameter Estimation: A Didactic](https://doi.org/10.3102/1076998607309474),
Journal of Educational and Behavioral Statistics, 34(1), 115–130.

The response model follows DINA. This implementation does **not** estimate item
parameters with EM/MCMC, enforce a knowledge-space structure, or claim those
parameters have been calibrated. Defaults are slip=0.10, guess=0.25, and prior=0.50.
Guess=0.25 is a starting assumption for four-option items, not an empirical estimate.
Set alternatives in the CLI or UI. An item may override the defaults with:

```json
"diagnostic_parameters": {"slip": 0.12, "guess": 0.20}
```

The exporter records the values and whether they were supplied or assumed; supplied
does not mean validated. Probabilities and entropy are conditional on these fixed
parameters and the Q-matrix. They do not include parameter or skill-mapping uncertainty.

By default, probability >=0.80 is `likely_mastered`, probability <=0.20 is
`likely_not_mastered`, and the interval between is `uncertain`. Uncertain is not
partial mastery. Skills with no observed question requiring them have NaN scores
and `insufficient_evidence`, not a displayed prior. Thresholds are a configurable
decision policy, not a validated educational standard.

Q-vectors are used as supplied, including single- and multi-skill items. Ancestors
are not automatically added and the prerequisite graph does not constrain the
posterior. Experts should validate skill mappings; prerequisite order alone does
not imply an item directly measures every ancestor. Exact inference is limited to
16 skills and 8 million item/profile combinations. Split larger curricular units.

## Inputs and outputs

Results require unique `student_idx`, `student_name`, and exactly `item_1` through
`item_N`, corresponding to bank single-skill items followed by multi-skill items.
Column order in the spreadsheet is irrelevant; question order is not. Do not use
shuffled tests unless responses have been remapped to original bank order.
`1` is correct; `0` is incorrect; a blank is missing evidence. `scored=0` ignores
the whole row. Missing responses are omitted, not counted as failures. This does
not model informative missingness (such as timeouts caused by difficulty).

```powershell
python recognition/scoring_layer.py --results results.xlsx --bank bank.json --output mastery.xlsx --method dina --confidence 0.8
python recognition/scoring_layer.py --results results.xlsx --bank bank.json --output baseline.xlsx --method simple
```

`mastery.xlsx` contains:

- `mastery`: compatible per-student skill columns, now probabilities; individual
  statuses, evidence counts, and counts of likely mastered/uncertain/unmeasured skills.
- `diagnostics`: long-format probability, status, observed item count, and binary
  entropy in bits for every student/skill. Entropy is largest near probability 0.5.
- `follow_up`: up to three missing-response bank items ranked by expected information
  gain about the joint skill profile. These are recommendations for teacher review,
  not automatic exam administration. If the bank is exhausted, lists skills needing
  new targeted questions. The engine cannot distinguish unadministered from skipped
  items, and does not generate new follow-up questions itself.
- `model` and `item_parameters`: reproducible model settings.

Clustering uses probabilities and excludes rows missing any skill estimate (cluster
`-1`). Such students still appear in the diagnostic and follow-up sheets. Clusters
are classroom organization aids, not validated learner categories. A centroid is
an average of individual probabilities, not a group posterior.

For DINA outputs, a remediation skill is marked `reteach` if at least half the
cluster's members are individually likely not mastered, and `assess` otherwise
unless every member is likely mastered. This is an explicit heuristic policy;
teachers must review mixed groups. Individual paths use each student's status.
Prerequisites order both types of action. Uncertain skills are listed for assessment
in the teaching pack and PDF; no LLM lesson is generated for those steps. Simple
baseline paths retain the existing centroid threshold policy.

On Page 3, choose the diagnostic method and inspect a student's evidence and follow-up
recommendations. Re-running scoring clears downstream workspace pointers, so clusters
and packs must be rebuilt. Historical runs remain unchanged until explicitly rerun.

## Validation and next research steps

Run `python -m pytest tests/test_diagnostics.py`. Tests cover analytical posteriors,
multi-skill attribution, missing evidence, parameter validation, information gain,
spreadsheet round trips, and downstream uncertainty handling. These establish
implementation behavior; they do not establish educational validity.

Before research claims about improved diagnosis: obtain expert-reviewed Q-matrices,
collect student responses, estimate item parameters on training data, and evaluate
on held-out students/items against the simple baseline. Report calibration (Brier
score/log loss), agreement with independent skill assessments, uncertainty coverage,
and sensitivity to priors and slip/guess assumptions. Do not fit and evaluate on the
same responses or treat synthetic recovery as classroom evidence. A later classroom
study should measure learning gains, retention, and teacher time with comparable
instructional exposure across conditions.
