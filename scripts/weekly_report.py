#!/usr/bin/env python3
"""
Weekly chess report: descarrega partides de chess.com de l'última setmana,
les analitza amb Stockfish i genera un informe HTML autocontingut amb grafics.

Ús: python3 weekly_report.py --username baikthemaik --days 7 --depth 18
"""
import argparse
import base64
import io
import json
import math
import os
from datetime import datetime, timedelta, timezone

import chess
import chess.engine
import chess.pgn
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import requests

STOCKFISH_PATH = os.environ.get("STOCKFISH_PATH", "/usr/games/stockfish")
CAP = 1000  # limitem l'avaluacio a +-1000cp per evitar CPL absurds en posicions de mat forçat


def game_phase(move_number):
    """Heurística aproximada per fases de joc (no és una detecció real d'estructura)."""
    if move_number <= 10:
        return "obertura"
    if move_number <= 25:
        return "migjoc"
    return "final"


def cp_to_winprob(cp):
    return 1 / (1 + math.exp(-cp / 400))


def classify(cpl):
    if cpl >= 300:
        return "blunder"
    if cpl >= 100:
        return "mistake"
    if cpl >= 50:
        return "inaccuracy"
    return None


def fetch_recent_games(username, days):
    """Descarrega els arxius mensuals necessaris i filtra per finestra temporal."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    months_needed = set()
    cursor = since
    while cursor <= now:
        months_needed.add((cursor.year, cursor.month))
        # avança al mes seguent
        if cursor.month == 12:
            cursor = cursor.replace(year=cursor.year + 1, month=1, day=1)
        else:
            cursor = cursor.replace(month=cursor.month + 1, day=1)

    games = []
    headers = {"User-Agent": "personal-chess-report-script (contact: n/a)"}
    for year, month in sorted(months_needed):
        url = f"https://api.chess.com/pub/player/{username}/games/{year}/{month:02d}"
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            continue
        data = resp.json()
        for g in data.get("games", []):
            end_time = datetime.fromtimestamp(g["end_time"], tz=timezone.utc)
            if since <= end_time <= now:
                games.append(g)
    games.sort(key=lambda g: g["end_time"])
    return games


def analyze_game(engine, pgn_text, depth):
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        return None
    board = game.board()
    moves = list(game.mainline_moves())

    stats = {
        "white": {"cpl_sum": 0, "n": 0, "blunders": 0, "mistakes": 0, "inaccuracies": 0, "acc_sum": 0.0},
        "black": {"cpl_sum": 0, "n": 0, "blunders": 0, "mistakes": 0, "inaccuracies": 0, "acc_sum": 0.0},
    }
    move_details = {"white": [], "black": []}

    def capped_score(info):
        s = info["score"].white().score(mate_score=100000)
        return max(-CAP, min(CAP, s))

    info = engine.analyse(board, chess.engine.Limit(depth=depth))
    prev_score = capped_score(info)
    prev_info = info

    ply = 0
    for move in moves:
        ply += 1
        mover_is_white = board.turn == chess.WHITE
        move_number = (ply + 1) // 2

        # millor jugada segons Stockfish a la posició ABANS de jugar (si en té una calculada)
        best_move_san = None
        if prev_info.get("pv"):
            try:
                best_move_san = board.san(prev_info["pv"][0])
            except Exception:
                best_move_san = None

        san_played = board.san(move)
        board.push(move)
        info = engine.analyse(board, chess.engine.Limit(depth=depth))
        cur_score = capped_score(info)

        if mover_is_white:
            loss = max(0, prev_score - cur_score)
            key = "white"
            wp_before = cp_to_winprob(prev_score)
            wp_after = cp_to_winprob(cur_score)
        else:
            loss = max(0, cur_score - prev_score)
            key = "black"
            wp_before = 1 - cp_to_winprob(prev_score)
            wp_after = 1 - cp_to_winprob(cur_score)

        wp_drop = max(0.0, (wp_before - wp_after) * 100)
        move_acc = max(0, min(100, 103.1668 * math.exp(-0.04354 * wp_drop) - 3.1669))

        stats[key]["cpl_sum"] += loss
        stats[key]["acc_sum"] += move_acc
        stats[key]["n"] += 1
        tag = classify(loss)
        if tag == "blunder":
            stats[key]["blunders"] += 1
        elif tag == "mistake":
            stats[key]["mistakes"] += 1
        elif tag == "inaccuracy":
            stats[key]["inaccuracies"] += 1

        if tag is not None:
            move_details[key].append({
                "move_number": move_number,
                "played": san_played,
                "best": best_move_san,
                "tag": tag,
                "loss_cp": loss,
                "eval_after_cp": cur_score,
                "phase": game_phase(move_number),
            })

        prev_score = cur_score
        prev_info = info

    for side in ("white", "black"):
        n = stats[side]["n"]
        stats[side]["avg_cpl"] = stats[side]["cpl_sum"] / n if n else None
        stats[side]["accuracy"] = stats[side]["acc_sum"] / n if n else None

    return stats, move_details


def fig_to_base64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


def build_coach_payload(username, games_with_stats):
    """Construeix un resum estructurat (només dades, sense text lliure) per enviar a Claude."""
    per_color = {"white": {"n": 0, "acc_sum": 0.0, "blunders": 0, "mistakes": 0, "inaccuracies": 0,
                            "results": {"win": 0, "loss": 0, "draw": 0}},
                 "black": {"n": 0, "acc_sum": 0.0, "blunders": 0, "mistakes": 0, "inaccuracies": 0,
                           "results": {"win": 0, "loss": 0, "draw": 0}}}
    phase_errors = {"obertura": 0, "migjoc": 0, "final": 0}
    opening_stats = {}
    game_summaries = []

    for g, stats, move_details in games_with_stats:
        my_color = "white" if g["white"]["username"].lower() == username.lower() else "black"
        my_stats = stats[my_color]
        my_result = g[my_color]["result"]
        outcome = "win" if my_result == "win" else ("draw" if my_result in ("agreed", "repetition", "stalemate", "insufficient", "50move", "timevsinsufficient") else "loss")

        per_color[my_color]["n"] += 1
        per_color[my_color]["acc_sum"] += my_stats["accuracy"] or 0
        per_color[my_color]["blunders"] += my_stats["blunders"]
        per_color[my_color]["mistakes"] += my_stats["mistakes"]
        per_color[my_color]["inaccuracies"] += my_stats["inaccuracies"]
        per_color[my_color]["results"][outcome] += 1

        eco_raw = g.get("eco", "")
        eco_name = eco_raw.split("/")[-1].replace("-", " ") if eco_raw else "Desconeguda"
        slot = opening_stats.setdefault(eco_name, {"n": 0, "win": 0, "loss": 0, "draw": 0, "color_white": 0, "color_black": 0})
        slot["n"] += 1
        slot[outcome] += 1
        slot[f"color_{my_color}"] += 1

        my_moves = move_details[my_color]
        for m in my_moves:
            phase_errors[m["phase"]] += 1

        game_summaries.append({
            "color": my_color,
            "opening": eco_name,
            "result": outcome,
            "accuracy": round(my_stats["accuracy"] or 0, 1),
            "errors": [{"phase": m["phase"], "tag": m["tag"], "move": m["played"], "best": m["best"], "loss_cp": m["loss_cp"]} for m in my_moves],
        })

    for color in ("white", "black"):
        n = per_color[color]["n"]
        per_color[color]["avg_accuracy"] = round(per_color[color]["acc_sum"] / n, 1) if n else None
        del per_color[color]["acc_sum"]

    return {
        "n_games_total": len(games_with_stats),
        "per_color": per_color,
        "errors_by_phase": phase_errors,
        "openings": opening_stats,
        "games": game_summaries,
    }


def build_report(username, games_with_stats, period_days, payload_filename=None):
    rows = []
    accuracies = []
    blunders_per_game = []
    labels = []
    results_count = {"win": 0, "loss": 0, "draw": 0}
    ratings = []
    dates = []
    eco_stats = {}  # nom obertura -> {"n":, "win":, "loss":, "draw":, "acc_sum":}
    games_move_details = []  # llista de (label, opponent, url, [move_details])

    for g, stats, move_details in games_with_stats:
        my_color = "white" if g["white"]["username"].lower() == username.lower() else "black"
        opp = g["black"]["username"] if my_color == "white" else g["white"]["username"]
        my_stats = stats[my_color]
        my_result = g[my_color]["result"]
        if my_result == "win":
            results_count["win"] += 1
            outcome = "win"
        elif my_result in ("checkmated", "resigned", "timeout", "abandoned", "lose"):
            results_count["loss"] += 1
            outcome = "loss"
        else:
            results_count["draw"] += 1
            outcome = "draw"

        end_dt = datetime.fromtimestamp(g["end_time"], tz=timezone.utc)
        dates.append(end_dt)
        ratings.append(g[my_color]["rating"])
        acc = my_stats["accuracy"] or 0
        accuracies.append(acc)
        blunders_per_game.append(my_stats["blunders"])
        label = f"{end_dt.strftime('%d/%m')} vs {opp}"
        labels.append(label)

        eco_raw = g.get("eco", "")
        eco_name = eco_raw.split("/")[-1].replace("-", " ") if eco_raw else "Desconeguda"
        eco_slot = eco_stats.setdefault(eco_name, {"n": 0, "win": 0, "loss": 0, "draw": 0, "acc_sum": 0.0})
        eco_slot["n"] += 1
        eco_slot[outcome] += 1
        eco_slot["acc_sum"] += acc

        my_moves = move_details[my_color]
        if my_moves:
            games_move_details.append({
                "label": label, "opponent": opp, "url": g.get("url", ""), "moves": my_moves
            })

        rows.append({
            "date": end_dt.strftime("%Y-%m-%d %H:%M"),
            "color": my_color,
            "opponent": opp,
            "result": my_result,
            "rating": g[my_color]["rating"],
            "accuracy": round(acc, 1),
            "avg_cpl": round(my_stats["avg_cpl"], 1) if my_stats["avg_cpl"] is not None else None,
            "blunders": my_stats["blunders"],
            "mistakes": my_stats["mistakes"],
            "inaccuracies": my_stats["inaccuracies"],
            "url": g.get("url", ""),
        })

    charts_html = ""

    # Gràfic 1: accuracy per partida
    fig, ax = plt.subplots(figsize=(8, 3.5))
    ax.plot(range(len(accuracies)), accuracies, marker="o", color="#2b6cb0")
    ax.axhline(sum(accuracies) / len(accuracies) if accuracies else 0, color="#aaa", linestyle="--", linewidth=1)
    ax.set_ylabel("Accuracy (%)")
    ax.set_title("Accuracy per partida")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=60, ha="right", fontsize=7)
    ax.set_ylim(0, 100)
    charts_html += f'<img src="data:image/png;base64,{fig_to_base64(fig)}" style="max-width:100%">'

    # Gràfic 2: blunders per partida
    fig, ax = plt.subplots(figsize=(8, 3))
    ax.bar(range(len(blunders_per_game)), blunders_per_game, color="#c53030")
    ax.set_ylabel("Blunders")
    ax.set_title("Blunders per partida")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=60, ha="right", fontsize=7)
    charts_html += f'<img src="data:image/png;base64,{fig_to_base64(fig)}" style="max-width:100%">'

    # Gràfic 3: rating trend
    fig, ax = plt.subplots(figsize=(8, 3))
    ax.plot(dates, ratings, marker="o", color="#2f855a")
    ax.set_ylabel("Rating")
    ax.set_title("Evolució del rating")
    fig.autofmt_xdate(rotation=45)
    charts_html += f'<img src="data:image/png;base64,{fig_to_base64(fig)}" style="max-width:100%">'

    # Gràfic 4: resultats (pie)
    fig, ax = plt.subplots(figsize=(4, 4))
    vals = [results_count["win"], results_count["loss"], results_count["draw"]]
    ax.pie(vals, labels=["Victòries", "Derrotes", "Taules"], autopct="%1.0f%%",
           colors=["#2f855a", "#c53030", "#a0aec0"])
    ax.set_title("Resultats")
    charts_html += f'<img src="data:image/png;base64,{fig_to_base64(fig)}" style="max-width:60%">'

    avg_accuracy = sum(accuracies) / len(accuracies) if accuracies else 0
    total_blunders = sum(blunders_per_game)

    table_rows = "".join(
        f"<tr><td>{r['date']}</td><td>{r['color']}</td><td><a href='{r['url']}'>{r['opponent']}</a></td>"
        f"<td>{r['result']}</td><td>{r['rating']}</td><td>{r['accuracy']}</td><td>{r['avg_cpl']}</td>"
        f"<td>{r['blunders']}</td><td>{r['mistakes']}</td><td>{r['inaccuracies']}</td></tr>"
        for r in rows
    )

    # Taula d'obertures (ECO): partides, V-D-T, accuracy mitjana — ordenada per nombre de partides
    eco_rows_sorted = sorted(eco_stats.items(), key=lambda kv: -kv[1]["n"])
    eco_table_rows = "".join(
        f"<tr><td>{name}</td><td>{s['n']}</td><td>{s['win']}-{s['loss']}-{s['draw']}</td>"
        f"<td>{s['acc_sum']/s['n']:.1f}%</td></tr>"
        for name, s in eco_rows_sorted
    )

    # Detall de jugades a revisar, agrupat per partida
    tag_labels = {"blunder": "Blunder", "mistake": "Mistake", "inaccuracy": "Inaccuracy"}
    tag_colors = {"blunder": "#c53030", "mistake": "#dd6b20", "inaccuracy": "#a0aec0"}
    move_detail_blocks = ""
    for gm in games_move_details:
        moves_rows = "".join(
            f"<tr><td>{m['move_number']}</td><td>{m['played']}</td>"
            f"<td style='color:{tag_colors[m['tag']]};font-weight:600'>{tag_labels[m['tag']]}</td>"
            f"<td>{m['best'] or '—'}</td><td>-{m['loss_cp']}cp</td></tr>"
            for m in gm["moves"]
        )
        move_detail_blocks += f"""
        <details style="margin-bottom:10px;">
          <summary style="cursor:pointer;font-weight:600;">{gm['label']} — {len(gm['moves'])} jugades a revisar</summary>
          <table>
            <tr><th>Jugada #</th><th>Vas jugar</th><th>Tipus</th><th>Millor jugada</th><th>Pèrdua</th></tr>
            {moves_rows}
          </table>
        </details>"""

    coach_html = ""
    if payload_filename:
        coach_html = f"""
<div style="background:#fff8ec;border:1px solid #e9d8a6;border-radius:10px;padding:16px 20px;margin:20px 0;">
  <div style="font-weight:600;margin-bottom:6px;">🎯 Anàlisi de patrons</div>
  <div style="font-size:13px;line-height:1.5;margin-bottom:10px;">
    Copia les dades d'aquesta setmana (i l'evolució de setmanes anteriors: accuracy,
    blunders, resultats) i enganxa-les en un xat amb Claude (o un altre assistent)
    per obtenir una lectura de patrons — obertures, fases de joc, tendències per color.
  </div>
  <button onclick="copyPayload()" id="copyBtn" style="background:#2d2a26;color:#fff;border:none;border-radius:8px;padding:10px 18px;font-size:14px;cursor:pointer;">📋 Copiar dades</button>
  <span id="copyStatus" style="font-size:12px;color:#2f855a;margin-left:8px;"></span>
</div>
<script>
async function copyPayload() {{
  try {{
    const res = await fetch('{payload_filename}');
    const text = await res.text();
    await navigator.clipboard.writeText(text);
    document.getElementById('copyStatus').textContent = 'Copiat! ✓';
  }} catch (e) {{
    document.getElementById('copyStatus').textContent = 'Error copiant — obre {payload_filename} manualment.';
  }}
}}
</script>"""

    html = f"""<!DOCTYPE html>
<html lang="ca"><head><meta charset="utf-8">
<title>Informe setmanal d'escacs — {username}</title>
<style>
body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; background:#faf7f2; color:#2d2a26; max-width:900px; margin:0 auto; padding:24px; }}
h1 {{ font-family: 'Playfair Display', Georgia, serif; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; margin-top:16px; }}
th, td {{ border:1px solid #ddd; padding:6px 8px; text-align:left; }}
th {{ background:#f0e9de; }}
.summary {{ display:flex; gap:24px; margin:16px 0; flex-wrap:wrap; }}
.card {{ background:#fff; border:1px solid #e5ddd0; border-radius:8px; padding:12px 20px; }}
.card b {{ font-size:22px; display:block; }}
details summary::-webkit-details-marker {{ display:none; }}
</style></head>
<body>
<h1>Informe setmanal — {username}</h1>
<p>Període: últims {period_days} dies · {len(rows)} partides analitzades (Stockfish, depth configurat)</p>
<div class="summary">
  <div class="card"><b>{avg_accuracy:.1f}%</b>Accuracy mitjana</div>
  <div class="card"><b>{total_blunders}</b>Blunders totals</div>
  <div class="card"><b>{results_count['win']}-{results_count['loss']}-{results_count['draw']}</b>V-D-T</div>
</div>
{coach_html}
{charts_html}
<h2>Detall de partides</h2>
<table>
<tr><th>Data</th><th>Color</th><th>Rival</th><th>Resultat</th><th>Rating</th><th>Accuracy</th><th>AvgCPL</th><th>Blunders</th><th>Mistakes</th><th>Inaccuracies</th></tr>
{table_rows}
</table>
<h2>Anàlisi d'obertures</h2>
<table>
<tr><th>Obertura</th><th>Partides</th><th>V-D-T</th><th>Accuracy mitjana</th></tr>
{eco_table_rows}
</table>
<h2>Jugades a revisar</h2>
<p style="font-size:13px;color:#666">Clica cada partida per veure el detall de blunders/mistakes/inaccuracies.</p>
{move_detail_blocks}
</body></html>"""
    return html, avg_accuracy, total_blunders, results_count


def update_reports_index(out_dir, filename, avg_acc, total_blunders, results):
    """Manté docs/reports_index.json (usat per la PWA per llistar informes)."""
    docs_dir = os.path.dirname(out_dir.rstrip("/"))  # docs/reports -> docs
    index_path = os.path.join(docs_dir, "reports_index.json")
    index = []
    if os.path.exists(index_path):
        with open(index_path) as f:
            index = json.load(f)
    index.insert(0, {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "file": os.path.basename(filename),
        "avg_accuracy": round(avg_acc, 1),
        "total_blunders": total_blunders,
        "win": results["win"],
        "loss": results["loss"],
        "draw": results["draw"],
    })
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--depth", type=int, default=18)
    parser.add_argument("--out", default="docs/reports")
    args = parser.parse_args()

    print(f"Descarregant partides dels últims {args.days} dies per a {args.username}...")
    games = fetch_recent_games(args.username, args.days)
    print(f"Trobades {len(games)} partides.")

    if not games:
        print("Cap partida a analitzar aquesta setmana.")
        return

    engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
    engine.configure({"Threads": 2})

    games_with_stats = []
    for i, g in enumerate(games):
        print(f"Analitzant partida {i+1}/{len(games)}...", flush=True)
        result = analyze_game(engine, g["pgn"], args.depth)
        if result:
            stats, move_details = result
            games_with_stats.append((g, stats, move_details))
        print(f"  ...partida {i+1}/{len(games)} completada.", flush=True)

    engine.quit()

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    os.makedirs(args.out, exist_ok=True)

    payload_basename = f"payload_{date_str}.json"

    html, avg_acc, total_blunders, results = build_report(args.username, games_with_stats, args.days, payload_basename)

    filename = f"{args.out}/report_{date_str}.html"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Informe generat a {filename}")
    print(f"Accuracy mitjana: {avg_acc:.1f}% | Blunders totals: {total_blunders} | V-D-T: {results}")

    update_reports_index(args.out, filename, avg_acc, total_blunders, results)

    # llegim l'índex ja actualitzat (inclou aquesta setmana + totes les anteriors)
    # per adjuntar l'evolució històrica dins del mateix payload que es copia al mòbil
    index_path = f"{args.out}/reports_index.json"
    historical_trend = []
    if os.path.exists(index_path):
        with open(index_path, encoding="utf-8") as f:
            historical_trend = json.load(f)

    payload = build_coach_payload(args.username, games_with_stats)
    payload["evolucio_setmanal"] = historical_trend
    payload_path = f"{args.out}/{payload_basename}"
    with open(payload_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"Payload de dades desat a {payload_path} (amb {len(historical_trend)} setmanes d'historial)")

    # per a que el workflow pugui llegir el resum i enviar-lo per email
    with open(f"{args.out}/latest_summary.json", "w") as f:
        json.dump({
            "avg_accuracy": round(avg_acc, 1),
            "total_blunders": total_blunders,
            "results": results,
            "n_games": len(games_with_stats),
            "report_file": filename,
        }, f, indent=2)


if __name__ == "__main__":
    main()
