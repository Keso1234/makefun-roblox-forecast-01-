#!/usr/bin/env python3
"""roblox-up-and-coming-forecast-01 entry (Python 3.13, standard library only).

Gaussian-kernel forecaster over the last chart in /input/history.ndjson.

Design (from backtests on the practice window, starter baseline -43.512039):
- mu = current rank. Rank velocity hurt in every backtest (short-term rank
  moves are noise, not momentum), so mu uses the current rank only, plus a
  small measured playing-growth term: the slope of rank on hourly log
  playing growth is -0.24 ranks per unit, applied over a 12-hour horizon.
- sigma = 3.0, flat. v1 used sigma = 3.0 + 0.04*12 + per-game playing
  volatility and named every game at every place: practice log score
  -38.36. The wider, fully-spread kernel diluted the mass on the plausible
  games, so v2 keeps the tuned flat sigma 3.0 (rolling backtests: 3.0 wins
  or nearly wins in 3 of 4 windows; 2.5 blows up in the high-churn window,
  4.5 is too diffuse) and names only the 12 strongest games per place.
- Each place's named mass is 0.80 (the other 0.20 is left unnamed and is
  shared by the scorer over the games not named at that place), with a
  per-game cap of 0.50 at any one place.
- top10 is the ten games with the smallest expected rank (mu), in order.

Deterministic: no clock, no unseeded randomness, fixed iteration order.
Reads $MAKEFUN_INPUT_DIR (else /input), writes $MAKEFUN_OUTPUT_DIR (else
/output) / <task.json output, default forecast.json>.
"""
import json
import math
import os
from collections import defaultdict

HORIZON = 12.0          # hours over which the playing-growth term applies
SIGMA = 3.0             # tuned flat kernel width (see module docstring)
GROWTH_SLOPE = -0.24    # ranks per unit hourly log-playing growth
NAMED_MASS = 0.80       # per-place probability mass assigned to named games
CAP = 0.50              # per-game cap at any one place
NAMED_PER_PLACE = 12    # strongest games named at each place


def parse_time(text):
    from datetime import datetime

    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def main():
    input_dir = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
    output_dir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    with open(os.path.join(input_dir, "task.json"), encoding="utf-8") as fh:
        task = json.load(fh)
    places = int(task["places"])
    output_name = task.get("output", "forecast.json")

    reads = []
    with open(os.path.join(input_dir, "history.ndjson"), encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                reads.append(json.loads(line))
    if not reads:
        raise SystemExit("history.ndjson is empty")

    # Per-game playing history: (timestamp, playing) in read order.
    playing_hist = defaultdict(list)
    for read in reads:
        at = parse_time(read["at"])
        for row in read["up_and_coming"]:
            playing = row.get("playing")
            if isinstance(playing, (int, float)) and playing > 0:
                playing_hist[int(row["universe_id"])].append((at, float(playing)))

    last = sorted(reads[-1]["up_and_coming"], key=lambda r: r["rank"])
    games = []
    for row in last:
        uid = int(row["universe_id"])
        rank = float(row["rank"])
        hist = playing_hist.get(uid, [])
        # Playing-growth term: hourly log growth over the last interval.
        mu = rank
        if len(hist) >= 2:
            (t_prev, p_prev), (t_last, p_last) = hist[-2], hist[-1]
            dt_hours = (t_last - t_prev).total_seconds() / 3600.0
            if dt_hours > 0:
                growth_hr = math.log(p_last / p_prev) / dt_hours
                growth_hr = max(-0.5, min(0.5, growth_hr))
                mu = rank + GROWTH_SLOPE * growth_hr * HORIZON
        games.append({"universe_id": uid, "rank": rank, "mu": mu, "sigma": SIGMA})

    # Gaussian kernel mass of each game at each place. Only the strongest
    # NAMED_PER_PLACE games per place are named, normalised to NAMED_MASS,
    # then capped at CAP with the freed mass redistributed among the
    # uncapped named games (a few passes converge for these widths).
    probabilities = []
    for place in range(1, places + 1):
        weighted = []
        for game in games:
            z = (place - game["mu"]) / game["sigma"]
            weighted.append((math.exp(-0.5 * z * z), game["universe_id"]))
        weighted.sort(key=lambda item: (-item[0], item[1]))
        chosen = weighted[:NAMED_PER_PLACE]
        total = sum(w for w, _ in chosen)
        probs = {uid: NAMED_MASS * w / total for w, uid in chosen}
        for _ in range(20):
            capped = {uid: p for uid, p in probs.items() if p >= CAP}
            if not capped:
                break
            for uid in capped:
                probs[uid] = CAP
            remaining = NAMED_MASS - CAP * len(capped)
            free = {uid: p for uid, p in probs.items() if uid not in capped}
            free_total = sum(free.values())
            if free_total <= 0 or remaining <= 0:
                break
            for uid in free:
                probs[uid] = remaining * free[uid] / free_total
            if all(p < CAP for uid, p in probs.items() if uid not in capped):
                break
        for uid in sorted(probs):
            p = probs[uid]
            if p > 0:
                probabilities.append(
                    {"universe_id": uid, "place": place, "p": round(p, 6)}
                )

    # top10 by expected rank (mu), ties broken by current rank, then id.
    top10 = [
        game["universe_id"]
        for game in sorted(games, key=lambda g: (g["mu"], g["rank"], g["universe_id"]))[:places]
    ]

    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, output_name), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"probabilities": probabilities, "top10": top10}) + "\n")


if __name__ == "__main__":
    main()
