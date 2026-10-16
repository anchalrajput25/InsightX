# backend/investigation/reasons.py
#
# The "real reason" behind a change, checked against the data. The
# drivers say WHERE a change sits; these checks look for WHY:
#
#   1. unusual_before   the earlier period was itself unusual (a sale
#                       spike, a holiday), so the "drop" is a return
#                       to normal
#   2. seasonal         the same move happened a year earlier
#   3. changed_with     other numbers (delivery time, cancellations,
#                       prices) changed at the same time
#   4. customers        new vs returning customers: who stopped / started
#
# Every check is plain SQL and arithmetic. A check that cannot run on
# this data is skipped; one that runs and finds nothing goes to
# "ruled_out", so the user sees what was looked at.

import re
import statistics
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from backend import dialects
from backend.investigation.engine import Investigation, _num, _pct, _period_key, col_sql, period_label

SPIKE_PCT = 15.0  # the earlier period is "unusual" when this far from the expected level
HISTORY = 6       # periods before the baseline used to find the expected level
CUSTOMER_RE = re.compile(r"(customer|client|buyer|shopper|member|user|account)", re.IGNORECASE)
CUSTOMER_ID_RE = re.compile(r"(id|key|code|email|number|no)$", re.IGNORECASE)


def _fmt(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    if float(value).is_integer():
        return f"{value:,.0f}"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    if abs(value) >= 10:
        return f"{value:,.1f}"
    return f"{value:,.2f}"


def _r(value: Optional[float], digits: int = 1) -> Optional[float]:
    return None if value is None else round(value, digits)


def _regular(points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    typical_n = statistics.median([point["n"] for point in points]) or 1
    return [point for point in points
            if not point["partial"] and point["value"] is not None and point["n"] >= 0.2 * typical_n]


# ---------------- 1. was the earlier period unusual? ----------------
def unusual_before(report: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    h = report["headline"]
    points = report["trend"]
    index = {point["period"]: i for i, point in enumerate(points)}
    base_i, cur_i = index.get(h["baseline_period"]), index.get(h["current_period"])
    old, new = h.get("baseline"), h.get("current")
    if base_i is None or cur_i is None or old is None or new is None or old == new:
        return None, None
    history = [p for p in _regular(points) if index[p["period"]] < base_i][-HISTORY:]
    if len(history) < 3:
        return None, None
    # expected level: a straight line through the periods before (so a
    # growing business is not mistaken for a spike), or their median
    xs = [index[p["period"]] for p in history]
    ys = [p["value"] for p in history]
    mean_x, mean_y = statistics.mean(xs), statistics.mean(ys)
    spread = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / spread if spread else 0.0

    def expected(i: int) -> float:
        value = mean_y + slope * (i - mean_x)
        return value if value > 0 or mean_y <= 0 else statistics.median(ys)

    expected_old, expected_new = expected(base_i), expected(cur_i)
    if not expected_old:
        return None, None
    old_dev = _pct(old, expected_old)
    new_dev = _pct(new, expected_new)
    base_label, cur_label = h["baseline_label"], h["current_label"]
    evidence = {"usual_level_before": _r(expected_old), "usual_level_now": _r(expected_new),
                "baseline_vs_usual_pct": _r(old_dev), "current_vs_usual_pct": _r(new_dev)}
    change = new - old
    spike = old - expected_old  # how far the earlier period stood out
    if old_dev is None or abs(old_dev) < SPIKE_PCT or spike * change >= 0:
        return None, {
            "kind": "unusual_before",
            "title": f"{base_label} was a normal period",
            "detail": (f"{base_label} ({_fmt(old)}) was close to its usual level of about {_fmt(expected_old)} "
                       f"({old_dev:+.0f}%), so the change is not just a one-off before it."
                       if old_dev is not None else f"{base_label} was close to its usual level."),
            "evidence": evidence,
        }
    explained = max(0.0, min(1.0, spike / (old - new)))
    word = "high" if spike > 0 else "low"
    if new_dev is not None and abs(new_dev) < SPIKE_PCT / 2:
        now = f"{cur_label} ({_fmt(new)}) is back near its usual level of about {_fmt(expected_new)}."
    elif new_dev is not None and new_dev * spike > 0:
        now = (f"{cur_label} ({_fmt(new)}) is still {abs(new_dev):.0f}% {'above' if new_dev > 0 else 'below'} "
               f"its usual level of about {_fmt(expected_new)}, so nothing is actually wrong now.")
    else:
        now = (f"{cur_label} ({_fmt(new)}) is {abs(new_dev or 0):.0f}% {'below' if spike > 0 else 'above'} "
               f"its usual level of about {_fmt(expected_new)}, so part of the change is real.")
    title = (f"{base_label} was unusually {word} (a one-off spike), so this is mostly a return to normal"
             if explained >= 0.6 else f"{base_label} was unusually {word}, which explains part of the change")
    evidence["share_explained_pct"] = _r(explained * 100, 0)
    return {
        "kind": "unusual_before",
        "title": title,
        "detail": (f"{base_label} ({_fmt(old)}) was {abs(old_dev):.0f}% {'above' if spike > 0 else 'below'} "
                   f"the usual level of about {_fmt(expected_old)} (from the {len(history)} periods before it). "
                   f"{now} About {explained * 100:.0f}% of the change comes from that one-off."),
        "strength": round(explained * 100),
        "evidence": evidence,
    }, None


# ---------------- 2. seasonality ----------------
def _shift(period: str, grain: str) -> Optional[str]:
    try:
        day = date.fromisoformat(period[:10])
    except ValueError:
        return None
    if grain in ("month", "quarter"):
        return day.replace(year=day.year - 1).isoformat()
    if grain == "week":
        return (day - timedelta(weeks=52)).isoformat()
    if grain == "day":
        return (day - timedelta(days=364)).isoformat()  # same weekday
    return None


def seasonal(report: Dict[str, Any], grain: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    h = report["headline"]
    change_pct = h.get("change_pct")
    if not change_pct:
        return None, None
    regular = {p["period"]: p for p in _regular(report["trend"])}
    then_base, then_cur = _shift(h["baseline_period"], grain), _shift(h["current_period"], grain)
    if then_base not in regular or then_cur not in regular:
        return None, None
    then_pct = _pct(regular[then_cur]["value"], regular[then_base]["value"])
    if then_pct is None:
        return None, None
    a, b = period_label(then_base, grain), period_label(then_cur, grain)
    evidence = {"last_year_baseline": _r(regular[then_base]["value"]), "last_year_current": _r(regular[then_cur]["value"]),
                "last_year_change_pct": _r(then_pct)}
    same = then_pct * change_pct > 0 and abs(then_pct) >= 0.5 * abs(change_pct)
    if not same:
        return None, {
            "kind": "seasonal",
            "title": "Not a seasonal pattern",
            "detail": (f"A year earlier, {a} to {b} changed {then_pct:+.1f}% "
                       f"(this time {change_pct:+.1f}%), so the season does not explain it."),
            "evidence": evidence,
        }
    explained = min(1.0, abs(then_pct) / abs(change_pct))
    return {
        "kind": "seasonal",
        "title": "Seasonal: the same thing happened last year",
        "detail": (f"A year earlier, {a} to {b} also changed {then_pct:+.1f}% "
                   f"({_fmt(regular[then_base]['value'])} to {_fmt(regular[then_cur]['value'])}). "
                   f"This time it changed {change_pct:+.1f}%, so this is mostly the normal yearly pattern."),
        "strength": round(explained * 80),
        "evidence": evidence,
    }, None


# ---------------- 3. what changed at the same time ----------------
def _signal_label(signal: Dict[str, Any]) -> str:
    name = signal["signal"]
    if signal["kind"] == "time_gap":
        match = re.match(r"days (.+) → (.+)", name)
        if match:
            return f"Days from {match.group(1).replace('_', ' ')} to {match.group(2).replace('_', ' ')}"
    if signal["kind"] == "share":
        match = re.match(r"share (.+?) = (.+)", name)
        if match:
            return f"Share of {match.group(1).replace('_', ' ')} '{match.group(2)}'"
    if signal["kind"] == "average" and name.startswith("avg "):
        return "Average " + name[4:].replace("_", " ")
    return name


def changed_with(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    found = []
    for signal in report.get("signals") or []:
        old, new, change = signal.get("overall_baseline"), signal.get("overall_current"), signal.get("overall_change")
        if old is None or new is None or change is None:
            continue
        if signal["kind"] == "time_gap":
            big = abs(change) >= max(0.5, 0.1 * abs(old))
            moved = f"went from {_fmt(old)} to {_fmt(new)} days"
        elif signal["kind"] == "share":
            big = abs(change) >= 1
            moved = f"went from {_fmt(old)}% to {_fmt(new)}%"
        else:
            big = abs(change) >= 5
            moved = f"went from {_fmt(old)} to {_fmt(new)} ({change:+.1f}%)"
        if not big:
            continue
        label = _signal_label(signal)
        detail = f"{label} {moved} between the two periods."
        if signal.get("segment_change") is not None and signal.get("scope"):
            detail += (f" Inside {signal['scope']} it went from {_fmt(signal.get('segment_baseline'))} "
                       f"to {_fmt(signal.get('segment_current'))}.")
        found.append({
            "kind": "changed_with",
            "title": f"{label} changed at the same time",
            "detail": detail + " It moved together with the metric; check whether it is the cause.",
            "strength": round(min(45.0, 15 + signal.get("strength", 0) / 4)),
            "evidence": {"signal": signal["signal"], "before": old, "after": new, "change": change},
        })
        if len(found) == 3:
            break
    return found


# ---------------- 4. new vs returning customers ----------------
def customer_column(inv: Investigation) -> Optional[Tuple[str, str]]:
    """The column that best identifies a customer, among joinable tables."""
    best, best_rank = None, None
    for table_name in inv.graph.paths:
        table = inv.graph.tables[table_name]
        key = table.get("primary_key") or []
        for column in table.get("columns", []):
            name = str(column.get("column_name") or "")
            if not (CUSTOMER_RE.search(name) or CUSTOMER_RE.search(table_name) and name in key):
                continue
            if not CUSTOMER_ID_RE.search(name):
                continue
            if table_name == inv.plan.fact_table and name in key and len(key) == 1 and not CUSTOMER_RE.search(table_name):
                continue  # the fact table's own id (an order id) is not a customer
            rank = (0 if "unique" in name.lower() else 1, len(inv.graph.paths[table_name]), name)
            if best_rank is None or rank < best_rank:
                best, best_rank = (table_name, name), rank
    return best


def customers(inv: Investigation, report: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    found = customer_column(inv)
    if not found:
        return None, None
    h = report["headline"]
    baseline, current = h["baseline_period"], h["current_period"]
    table, column = found
    customer = col_sql(table, column)
    tables = inv._base_tables([table])
    firsts = (f"SELECT {customer} AS ix_customer, MIN({inv.period_sql}) AS ix_first "
              f"FROM {inv.graph.from_clause(tables)} WHERE {inv._where(extra=f'{customer} IS NOT NULL')} "
              f"GROUP BY {customer}")
    segment = f"CASE WHEN {inv.period_sql} = ix_f.ix_first THEN 'new' ELSE 'returning' END"
    sql = (f"SELECT {inv.period_sql} AS period, {segment} AS segment, {inv._measure_columns()}, "
           f"COUNT(DISTINCT {customer}) AS customers "
           f"FROM {inv.graph.from_clause(tables)} JOIN ({firsts}) ix_f ON {customer} = ix_f.ix_customer "
           f"WHERE {inv._where([baseline, current])} GROUP BY 1, 2")
    rows = inv._query("New vs returning customers", sql)["rows"]
    data: Dict[str, Dict[str, Dict[str, Optional[float]]]] = {"new": {}, "returning": {}}
    for row in rows:
        group = str(row["segment"])
        if group in data:
            data[group][_period_key(row["period"])] = {"value": _num(row["value"]), "customers": _num(row["customers"])}

    def get(group: str, period: str, field: str) -> float:
        return (data[group].get(period) or {}).get(field) or 0.0

    additive = report["plan"].get("metric_kind") == "additive"
    field = "value" if additive else "customers"
    old = {g: get(g, baseline, field) for g in data}
    new = {g: get(g, current, field) for g in data}
    total_old, total_change = sum(old.values()), sum(new.values()) - sum(old.values())
    if not total_old or not total_change:
        return None, None
    counts = {g: (get(g, baseline, "customers"), get(g, current, "customers")) for g in data}
    evidence = {
        "new_customers_before": counts["new"][0], "new_customers_after": counts["new"][1],
        "returning_customers_before": counts["returning"][0], "returning_customers_after": counts["returning"][1],
    }
    what = h["metric"] if additive else "customers"
    lines = []
    for group in ("new", "returning"):
        share = (new[group] - old[group]) / total_change * 100
        evidence[f"{group}_share_of_change_pct"] = _r(share, 0)
        evidence[f"{group}_share_before_pct"] = _r(old[group] / total_old * 100, 0)
        lines.append(f"{group.capitalize()} customers: {_fmt(counts[group][0])} to {_fmt(counts[group][1])} people"
                     + (f", {share:.0f}% of the change in {what}" if 0 <= share <= 100 else "") + ".")
    gap = evidence["new_share_of_change_pct"] - evidence["new_share_before_pct"]
    detail = " ".join(lines)
    if abs(gap) < 10:
        return None, {"kind": "customers", "title": "New and returning customers moved about evenly",
                      "detail": detail + " Neither group stands out.", "evidence": evidence}
    fell = total_change < 0
    group = "new" if gap > 0 else "returning"
    if group == "new":
        title = "Fewer new customers came in" if fell else "More new customers came in"
    else:
        title = "Returning customers bought less" if fell else "Returning customers bought more"
    return {
        "kind": "customers",
        "title": title,
        "detail": (detail + f" {group.capitalize()} customers made up {evidence[f'{group}_share_before_pct']:.0f}% "
                   f"of {what} before but {evidence[f'{group}_share_of_change_pct']:.0f}% of the change."),
        "strength": round(min(70.0, 20 + abs(gap))),
        "evidence": evidence,
    }, None


# ---------------- all together ----------------
def find_reasons(inv: Investigation, report: Dict[str, Any]) -> Dict[str, Any]:
    reasons, ruled_out = [], []
    checks = [
        lambda: unusual_before(report),
        lambda: seasonal(report, inv.plan.grain),
        lambda: customers(inv, report),
    ]
    for check in checks:
        try:
            found, not_it = check()
        except Exception as exc:  # a check that cannot run here is skipped
            inv.plan.notes.append(f"A reason check was skipped: {str(exc)[:160]}")
            continue
        if found:
            reasons.append(found)
        if not_it:
            ruled_out.append(not_it)
    try:
        reasons += changed_with(report)
    except Exception:
        pass
    reasons.sort(key=lambda reason: -reason["strength"])
    for position, reason in enumerate(reasons):
        if position == 0 and reason["strength"] >= 50:
            reason["likelihood"] = "Most likely"
        elif reason["strength"] >= 35:
            reason["likelihood"] = "Likely"
        else:
            reason["likelihood"] = "Possible"
    return {"reasons": reasons, "ruled_out": ruled_out}
