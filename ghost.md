# Ghost nodes

A ghost node is a simulation that has not been run yet: a set of settings, and a guess for every
descriptor that run would produce. Ghosts turn the graph from a record of past work into a guide to
the next run.

This document is the rule book: what the graph models, how every guess and every relationship gets
its confidence, how all of it is tested (only ever against real simulations: every new run is
predicted blind before it arrives), and how it corrects itself. The app (`predict.py`) and the MILO MCP
both follow it.

---

## 1. Philosophy

1. **Every model exists from the first run.** Ghosts and relationships are always calculated, however
   little data there is. Little data does not hide them; it makes them low-confidence.
2. **A ghost is a claim, not a guess.** Every number is stated so a real run can prove it right or
   wrong: "90 % chance the density lands between 1.08 and 1.13 g/cm³", not "about 1.1".
3. **Confidence is a probability, and it gets tested.** Values stated at 30 % confidence must come
   true about 30 % of the time. If they do not, the system is wrong and corrects itself.
4. **Honest about ignorance.** With few runs the ranges are wide and confidence is low. That is the
   correct answer. Nothing may make a ghost look surer than the evidence allows.
5. **Reality is the judge.** Every real simulation is first used as a blind test of what was predicted
   before it existed. The track record is kept and shown.
6. **Everything is traceable and reproducible.** Every ghost and relationship records exactly which
   runs, settings, method and code produced it; re-running the record gives the same numbers.
7. **It learns and adjusts itself,** from evidence and never by hand: each run updates the model
   exactly, and the track record recalibrates confidence.
8. **Same rules for every source.** A ghost the MCP reasons out is held to the same claims, the same
   tests against real runs, and a track record of its own.
9. **No silent extrapolation.** A ghost outside the range of settings ever run says so.

---

## 2. What the graph models

The graph is modelled as one connected system, a **Bayesian network** (a probabilistic graphical
model):

- **Descriptors are the variables:** every input, output and numeric bundle field a simulation carries.
- **Knobs are the causes:** the settings a person chooses, recorded by the MILO scripts as
  `requested.*` inputs. Every simulation is an experiment on them, so a link from a knob to another
  descriptor is cause and effect, not just correlation. (Knobs that always move together count once.)
- **Relationship nodes are the links:** each pair of descriptors that can be related has a
  relationship node, with a strength and a confidence that it is real (section 5).
- **A ghost is a question to the network:** "if the knobs were set like this, what would every other
  descriptor be?" The answer is a guess and a range for every descriptor (section 4).

More runs sharpen everything: ranges narrow, real relationships gain confidence, and coincidences
lose it. Relationships can fade as well as grow.

---

## 3. What a ghost states

| Part | Meaning |
|---|---|
| Proposed run | A real run with one knob moved, e.g. that run with PLA mass fraction 0.3 → 0.59 |
| A guess for every descriptor | A most likely value and a **90 % range** (the real value falls inside it 90 % of the time) |
| Value confidence | For each guess: the probability it comes out *right*, meaning within tolerance of the real value |
| Ghost confidence | The whole ghost: the share of its guesses expected to come out right |
| Why | What the confidence rests on, in plain words |
| Calculation record | Everything needed to reproduce it (section 8) |
| Status | `open` (not run yet) → `verified` (run, and checked against the claim) |

---

## 4. The first method

### 4.1 Guessing a number: Bayesian linear regression

Each numeric descriptor is modelled from the knobs with **Bayesian linear regression** using the
conjugate Normal–Inverse-Gamma prior (Gelman et al., *Bayesian Data Analysis*, 3rd ed., ch. 14).

    descriptor = b0 + b1·knob1 + b2·knob2 + … + noise

- The knobs are scaled to 0–1 and centred, and the descriptor to its own mean and spread, so the priors
  mean the same thing for every descriptor.
- **Priors, stated openly, and which one depends on the runs:**
  - **More runs than unknowns** (the intercept and one slope per knob): the standard noninformative
    prior p(slopes, σ²) ∝ 1/σ² (Bayesian Data Analysis 14.2). The scatter is measured from the data,
    slopes are not pulled toward zero, and the 90 % ranges hold 90 % of the time. The tests check it.
  - **Too few runs for that** (like the first 3): the weakly informative conjugate prior, each slope ~
    Normal(0, σ²) and σ² ~ Inverse-Gamma(1, 1). In words: a knob is expected to move a descriptor by
    about its typical spread or less, and the noise is expected to be about that spread too. This keeps
    ranges wide and confidence low, which is the honest answer with so little data.
  - Used with many runs, the conjugate prior would keep ranges far wider than the data supports and
    pull slopes toward zero, which is why it applies only when there are too few runs.
- **Updating is exact.** With this prior the posterior has a closed form, so each new run updates the
  model exactly, with no fitting procedure and no randomness. The same runs always give the same numbers.
- **The guess for new settings** is a Student-t distribution (the "posterior predictive"): a most
  likely value, a spread, and degrees of freedom that grow with the runs. The 90 % range comes from it
  directly.

Why this first: it is exact, it behaves sensibly from 3 runs, every assumption sits in two written-down
priors, and it can be checked by hand. Its limit is that it draws straight lines. Curved relationships
come through model competition (section 9) once there are runs enough to see a curve, and only if
the curved model proves better on the tests.

### 4.2 Guessing a value that never changes, or a word

When every run so far gave the same value (cell angles of 90°, forcefield COMPASSIII), the guess is
that value and its confidence is **Laplace's rule of succession**:

    P(next run gives it again) = (n + 1) / (n + 2)        n = runs so far

For text descriptors that do change (e.g. status), the guess is the most common value, with the
same rule generalised to categories: `(count + 1) / (n + K)`, where K counts the values seen plus one
for "something new".

### 4.3 How a ghost is made

A ghost is built from a real run, the way a person would plan the next one: **take a run that
happened and move one knob.**

- Runs with the same knobs form a **family**, and every descriptor is modelled within its family, so a
  different kind of simulation (without these knobs) never blurs another's guesses.
- For each real run and each knob, the knob gets a new value drawn at random from the range of
  values tried so far (never outside it), rounded to a tenth of the range's order of magnitude
  (a range of 0.4 rounds to 0.01; a range of 152 K rounds to 10 K). It must differ from the run's own
  value by at least 5 % of that range, and must not repeat any run already made.
- **Everything else stays as in that run.** The ghost is made of the same descriptors as a simulation
  node, and every descriptor gets the models' guess at the ghost's knob settings (4.1, 4.2).
- The random draw is seeded by the run and the knob, so a ghost keeps its value from one
  recalculation to the next.
- The ghost links to the run it came from, so it sits beside it on the graph.
- How many: `MILO_GHOSTS` in `.env` ghosts per run for each knob (default 1). Three runs and two
  knobs make six ghosts.

### 4.4 Formulas between any descriptors (the Ghosts tab)

The app's **Ghosts** tab answers "how does one descriptor follow from others?" for any descriptors
with numbers. Check two or more; the one solved for is fitted from the others with the same Bayesian
linear regression as 4.1, over every run that has all of them:

    Amorphous Cell Cell Volume = 16,472 − 2,795 × PLA Mass Fraction   (Å³)

The formula is typeset like LaTeX (matplotlib's mathtext, Computer Modern), each coefficient with
its ± uncertainty, and **Copy LaTeX** puts its source on the clipboard for a paper. **Symbols** writes
every descriptor as a symbol (V volume, T temperature, ρ density, φ fraction, …; clashes get subscripts)
on one centred line with a legend, and then Copy LaTeX gives the equation followed by a "where V is …"
sentence. Shown with it:
each slope with its ± uncertainty, the relationship confidence of each pair (5.3), how
much of the variation the line explains (R²), the runs used, the run-to-run scatter, and with one
explaining descriptor a chart of the runs, the line and its 90 % band. A calculator turns values of
the explaining descriptors into a guess with its range and confidence (5.1).

Between two outputs the formula describes how they move together across the runs so far; it is
cause and effect only when the explaining descriptor is a knob (5.3).

**Campaign** (the picker at the top of the tab) narrows everything on the tab to one campaign's runs,
or to the runs in no campaign: the descriptors listed, the formula and its chart, the relationship
confidences (5.3 worked out again over just those runs) and the track record. The stored models,
relationships and ghosts stay over every run; this only isolates a campaign to look at it on its own.

---

## 5. Confidence

### 5.1 One guessed value

    value confidence = P( |real − guess| ≤ tolerance )  =  2 · T_df( tolerance / spread ) − 1

T_df is the Student-t distribution from 4.1. **Tolerance** is what counts as right. The default is
5 % of the guess, but never less than 5 % of the range seen across runs (so values near zero stay
fair); it can be set per descriptor (e.g. Tg ± 5 K).

Tolerance is only the yardstick for this one score. **The model always learns from the exact real
value**: a guess of 1.00 against a real 1.04 teaches it the full 0.04, not "right" or "wrong". Next to
every score, the **expected error** is shown in real units ("usually off by ±0.03 g/cm³").

### 5.2 The whole ghost

    ghost confidence = the average of its value confidences

This is the **share of the ghost's guesses expected to come out right**, which a real run checks
directly. It averages **every** descriptor the ghost guesses. The weakest value is always shown next
to it.

A ghost node is the display of one question to the model ("what if these settings were run?"). The
model itself, every descriptor and every relationship, is what is being solved; ghosts are how it is
asked and shown.

### 5.3 A relationship

This uses a standard, published method rather than a formula of MILO's own: **Efron's local false
discovery rate** (Efron, Tibshirani, Storey & Tusher 2001; Efron 2005, 2010), the method used in
genomics for exactly this problem: thousands of possible relationships measured on few samples.

1. **Each pair gets a p-value** from the standard test for a correlation (the t-test on Pearson's r,
   n − 2 degrees of freedom), turned into a z-score.
2. **Efron's two-groups model** looks at the z-scores of *all* pairs together and estimates two things
   from them: the share of pairs that are not related (π0), and how unrelated pairs' scores are spread.
   Nothing is assumed by hand; both come from the data. This is Efron's **empirical null**, which he
   recommends whenever the tests are linked to each other, as here: every descriptor is driven by the
   same few knobs. (On the first 3 runs, unrelated pairs' scores came out 1.6 times wider than the
   textbook null assumes; the textbook null would have made many coincidences look solid.) With fewer
   than 100 testable pairs it cannot be estimated, and the cautious textbook null is used, with every
   pair assumed unrelated to start.
3. **Each pair's local false discovery rate** is `lfdr = π0 · f0(z) / f(z)`: the posterior probability
   that this particular relationship is **not** real.

        relationship confidence = 1 − lfdr   (the probability that this relationship is real)

It is computed with the implementation in `statsmodels` (`statsmodels.stats.multitest.local_fdr`,
with `NullDistribution` estimating π0), not reimplemented.

- **Strength** is the relationship exactly as the data shows it (the measured correlation, −1 to 1),
  never weakened or hidden. Every relationship is modelled in full; judging it is the confidence's job,
  and the confidence is what the MCP reads to decide whether a relationship is solid.
- Links from a knob are marked as **causes**; links between two other descriptors as **associations**
  (they usually share a knob as their cause).
- Descriptors that are the same thing recorded twice (Length A/B/C, one temperature in three places)
  are merged before pairing.
- With very few runs every p-value is weak, so every lfdr stays near 1 and every confidence near 0.
  Confidence rises as runs confirm a relationship, and falls if they do not.

### 5.4 What this gives today (3 simulations)

Worked out on the three real runs, for the knob settings PLA mass fraction 0.7 at 450 K:

| Guess | 90 % range | Value confidence |
|---|---|---|
| Amorphous cell volume 14 795 Å³ | 11 162 – 18 429 | 30 % |
| NPT density 0.840 g/cm³ | 0.706 – 0.974 | 44 % |
| Run time 246 s | 199 – 293 | 38 % |
| Geometry-optimisation bond energy 2 712 kcal/mol | −3 446 – 8 870 | 6 % |

Ghost confidence: **57 %** over all 158 descriptors it guesses, numbers and text (the many settings
that never changed sit at 80 % each, by the rule of succession; the weakest value is PCL chain count
at 6 %).

Relationships:

Relationships (1 127 pairs tested; median confidence 6.5 %):

| Relationship | r | Confidence |
|---|---|---|
| PLA mass fraction → cell volume (the two runs at 0.3 gave identical volumes) | −1.00 | 80 % |
| Cell volume ↔ NPT density | −0.38 | 0 % |
| PLA mass fraction → NPT density | +0.38 | 0 % |

---

## 6. Testing against real simulations

Both tests are automatic:

1. **Blind test on every new simulation.** When a bundle arrives, *before* it joins the data, the
   current model guesses every descriptor from its knobs. The guesses are stored, then compared with
   what the run produced. Every real run is a blind test, whether or not a ghost proposed it.
2. **Ghost follow-up.** When a new run's knobs match an open ghost's proposed run, the ghost is checked
   descriptor by descriptor, marked `verified`, and linked to that simulation (`VERIFIED_BY`). Verified
   ghosts stay as history.

Each result records the guess, its range and confidence, the real value, and whether it was inside
the range and inside tolerance.

---

## 7. Confidence tests

**Only real simulations are ever used.** No made-up, synthetic, sample or hand-made data is used to
test, tune, demonstrate or fill MILO, ever: not in the graph, not in tests, not in checks. Every
number MILO reports is judged against runs that really happened.

### 7.1 Blind test of every new run

The main test, and it is automatic (section 6): every new run is predicted *before* it joins the
data, then compared with what it really produced. Each result is kept, and together they are the
track record (7.3), shown in the app.

### 7.2 Back-tests on the real runs already made

- **Leave-one-out:** each real run is hidden in turn and guessed from all the others.
- **Replay in time order:** each run is guessed using only the runs that finished before it. This is
  the most honest test, because it is exactly the situation of a real ghost.

### 7.3 The track record

Scores use the size of every miss, not only right or wrong.

- **Coverage:** the share of real values inside their 90 % ranges (target 90 %).
- **Calibration error (ECE):** the gap between stated confidence and how often things came true (target near 0).
- **Brier score:** the mean of (confidence − outcome)², where outcome is 1 if right and 0 if not. Lower is better.
- **Log score:** the probability the model gave to what really happened. Used to compare models fairly.

---

## 8. Traceability

Every calculation writes a **calculation record** (`data/ghosts/<calc_id>.json`), and every ghost and
relationship names its `calc_id`:

- every simulation used, with its bundle fingerprint
- the knobs, the descriptors, the merged duplicates, and the tolerances
- the priors, the estimated share of unrelated pairs (π0), and the posterior of every descriptor's model
- the method version (a hash of `predict.py`) and the library versions, plus the time
- the results: every candidate scored, the ghosts chosen, and every value and relationship with its confidence

`milo-app ghost reproduce <calc_id>` re-runs a record and confirms that every number matches.

---

## 9. Learning and self-adjusting

1. **Exact updating:** every new run updates each descriptor's model and each relationship exactly (4.1, 5.3).
2. **Relationship confidence re-estimated:** the local false discovery rate is recomputed over all
   pairs on every update, including its estimate of how many pairs are unrelated (5.3).
3. **Recalibration from the track record:** if the values stated at 30 % have come true only 20 % of
   the time, the spreads are widened until the stated confidence matches what happens (and narrowed if
   the model has been too cautious). Each source (the calculation, the MCP) keeps its own track record.
4. **Model competition:** once there are runs enough, other models, starting with curved ones (a
   Gaussian process, or a quadratic with interactions), compete on the time-order replay. Whichever
   scores best on the log score is used, and each ghost records which one made it.

---

## 10. Ghosts from the MCP

The MCP can add ghosts it reasons out (from the data, physics, or literature) with
`milo_add_prediction`. They follow the same rules: ranges and a confidence for their guesses, the
reasoning in words, blind tests against real runs, and a track record of their own shown next to them.

---

## 11. What machine learning can and cannot do

- **It can** model every relationship the runs cover, put a measured confidence on every guess and
  every relationship, and sharpen with every run.
- **It cannot** know what the runs have not explored. Far from tried settings, guesses fall back to
  "anything seen so far" and confidence falls toward zero. That is why ghosts are placed where the
  model is least sure: running them is how the explored region grows.
- **Small data favours simple, honest models.** Deep learning and graph neural networks need hundreds
  to thousands of runs. The competition in section 9 lets a bigger model take over only once it proves
  better on these runs.
- **Physics can help later.** Known laws, units and trends can be built in to cut the runs needed, and
  they too must pass the tests.

---

## 12. Build order

**Done:** step 1. Every recalculation runs in the background after each new simulation, stores
`Model`, `Relationship` and `Prediction` nodes, and writes its calculation record to
`data/ghosts/<calc_id>.json`. The math of every model, relationship and ghost is shown in the app's
side panel.

1. **The first method** (done): Bayesian linear regression for every descriptor, rule of succession for
   unchanging and text values, value and ghost confidence, and relationship nodes for all pairs with
   local-false-discovery-rate confidence. Relationship nodes shown between the descriptors checked in the sidebar.
2. **Reproduce** (section 8): the record is written; the `reproduce` check is still to build.
   *(Done alongside step 1: ghosts built from real runs, 4.3, and the formula tab, 4.4.)*
3. *(Dropped: no made-up data is ever used, section 7.)*
4. **Blind test of every new run + ghost follow-up + track record**, shown in the app (sections 6, 7.2). *(Built: `blind.py`; results on the run's panel, on verified ghosts, and as the track record in the Ghosts tab.)*
5. **Self-adjusting:** recalibration from the track record, then model competition (section 9).
6. **MCP ghosts under the same rules** (section 10).

## 13. Settled defaults

- **Tolerance** (the yardstick for a value's score only): 5 % of the guess, at least 5 % of the range
  seen; can be set per descriptor. Learning always uses the exact difference.
- **Ghost confidence** averages every descriptor the ghost guesses.
- **Ranges** are drawn at 90 %. This sets only how wide they are shown; they shrink as runs come in,
  down to the natural run-to-run scatter of the simulations, and only where runs have been made.
- **Relationships** are all modelled at full strength; their confidence is Efron's local false
  discovery rate, which estimates the share of unrelated pairs from the data itself.
