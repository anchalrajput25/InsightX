# What-if scenarios and forecasts

The **What-if** page (sidebar, under Ask) tests a change before anyone makes it:

> What if prices rise 10% and we lose all orders shipped to the USA? Forecast the next 3 months.

It answers with the number today, the number with the changes, the change per segment,
what each change does on its own, a sensitivity curve, and a forecast with a 95% band.
Nothing is written to the database: every number is a read-only `SELECT`.

## How it works

1. **Plan (the only AI step).** The question becomes a *scenario spec*: the metric
   (`SUM(order_details.unit_price * order_details.quantity)`), the main table, and a list of
   *levers*. The AI never writes the SQL that runs.
2. **Verify.** `backend/whatif/spec.py` checks the spec against the real schema:
   - every column exists and is reachable from the main table through many-to-one joins only,
     so no join can double-count rows (the same rule as investigations);
   - arithmetic levers only touch numeric columns, and numbers stay in safe ranges;
   - text values are matched to how they are stored (`"sao paulo"` becomes `"São Paulo"`). A
     value that does not exist is an error that lists the closest stored values;
   - "the 2 worst sellers" is resolved by SQL to the actual seller ids before anything runs.
3. **Build and run.** `backend/whatif/builder.py` writes the SQL itself. The baseline and the
   scenario are columns of **one** query over the same rows, so they are always compared on
   identical data. Every query passes the existing safety gate (`sql_safety`) and runs in the
   read-only executor (`query_executor`) with its timeout and row limit.

## Levers

| Lever | Example | Effect |
|---|---|---|
| `scale` | prices +10% | `price * 1.1` (only for the lever's segment, if it has one) |
| `scale` + `elasticity` | prices +10%, elasticity -1.5 | also 15% fewer rows (demand reacts) |
| `shift` | delivery 2 days faster | value - 2 (a column, or the metric's own value per row) |
| `set` | no discounts | discount = 0 |
| `cap` / `floor` | discounts at most 10% | `CASE WHEN discount > 0.1 THEN 0.1 ...` |
| `volume` | 20% more orders | each row counts 1.2 times |
| `exclude` | São Paulo orders disappear, drop the 2 worst sellers | those rows count 0 |

Any lever can take a `where` segment (`{"column": "customers.city", "op": "=", "values": ["sao paulo"]}`
or `{"column": "sellers.seller_id", "rank": "bottom", "n": 2}`). Up to 6 levers stack.

- SUM / COUNT / AVG metrics accept every lever (AVG becomes a weighted average).
- Ratios and distinct counts accept column levers and exclusions; volume does not apply to them.
- With several levers, the page shows each one alone and the interaction between them.

## Sensitivity

One lever is swept (by default -20% to +20% in steps of 5) in a single query. The elasticity
box re-runs the curve with a demand reaction you choose. The default is 0, meaning demand
does not change, and the page labels this clearly.

## Forecast

`backend/whatif/forecast.py` is plain Python and needs no extra package:

- **Model:** Holt-Winters (additive season, damped trend) when there are at least two
  seasons of history. Otherwise Holt (damped trend), and simple exponential smoothing for
  very short series. Smoothing weights come from a grid search on one-step-ahead errors.
- **Band:** 95%, from the spread of those errors, widening with the horizon.
- **Backtest:** the last periods are hidden, the model is fitted on the rest and scored
  (MAPE) next to a naive forecast. The page shows both numbers, so you can see whether the
  model beats guessing.
- **Partial last period:** a period the data only partly covers is left out, so it does not
  look like a drop. Empty periods count as 0 for totals.
- **Scenario forecast:** the changes' measured effect on recent history is applied to every
  future period.

## Assumptions

Every result lists its assumptions in plain sentences, for example *"Assumes the number of
order rows stays the same when unit_price changes (demand unchanged)"* or *"Assumes the
removed rows are lost, not won back by other segments"*.

## API

| Endpoint | Body | Notes |
|---|---|---|
| `POST /whatif` | `connection_id` + `question`, or + `spec` | a `spec` (edited in the page) runs without the AI |
| `POST /whatif/sweep` | `connection_id`, `spec`, `lever_index`, `values?`, `elasticity?` | sensitivity curve |
| `POST /forecast` | `connection_id`, `question` or `spec`, `horizon` (1-36), `grain?` | forecast with band and backtest |
| `GET /whatif/route?question=` | | whether a question looks like a what-if |

Same rules as `/query`: sign-in, the connection must be yours, and a question that needs the AI
counts against your daily limit.

## Setup

Nothing extra is needed. The planner uses the `whatif_plan` job, which runs on the "heavy"
model tier of the AI gateway (free models). Tests are in `backend/tests/test_whatif.py`.
