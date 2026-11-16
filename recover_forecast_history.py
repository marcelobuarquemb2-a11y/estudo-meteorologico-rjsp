#!/usr/bin/env python3
"""Reconstruct archived model-run values with explicit scientific provenance.

The operational archive data/YYYY-MM/DD/HHMMSS.json is immutable.
Historically inferred vintages are NEVER marked as real-time captures.
"""
import argparse
import json
import math
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from study_config import ROOT, STATIONS, TZ
from compare_forecasts import forecast_archives, measured_by_time

CANDIDATES = ROOT / "data/recovery_candidates/single_runs_probe_20261115.json"
ASSESSMENT = ROOT / "data/recovery_candidates/recovery_assessment_20261115.json"
RECOVERED = ROOT / "data/recovered_forecasts"
OUTPUT = ROOT / "data/comparisons/recovered_pairs_status.json"
MODELS = {"GFS": "ncep_gfs_global", "ICON": "icon_global", "ECMWF": "ecmwf_ifs"}
FIELDS = {"direction_deg": "wind_direction_10m", "mean_kt": "wind_speed_10m",
          "gust_kt": "wind_gusts_10m", "precip_mm": "precipitation"}
STATION_IDS = [s["id"] for s in STATIONS if s.get("enabled", True)]

def read(path):
    return json.loads(path.read_text(encoding="utf-8"))

def save(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")

def numeric(value):
    return type(value) in (int, float) and math.isfinite(value)

def normal_issue(value):
    return datetime.fromisoformat(value).replace(tzinfo=TZ)

def archive_path(issue):
    return RECOVERED / issue.strftime("%Y-%m-%d") / f"{issue:%H%M}.json"

def original_comparison(issue, station_id, model, lead):
    """Use the most complete retained capture, not every attempt in the window.

    The ICON cycle changed between 18:14 and 18:26 on 14 November.
    The 18:26 capture has more data and is the reference for the recovery.
    """
    folder = ROOT / "data" / issue.strftime("%Y-%m") / issue.strftime("%Y-%m-%d")
    candidates=[]
    for file in folder.glob("*.json"):
        report=read(file)
        nominal=report.get("nominal_issue_at_local")
        if not nominal or datetime.fromisoformat(nominal)!=issue:
            continue
        if not report.get("official_series_eligible"):
            continue
        richness=report.get("collection_summary",{}).get("forecast_values_present",0)
        consulted=datetime.fromisoformat(report["consulted_at"])
        candidates.append((-richness,abs((consulted-issue).total_seconds()),str(file),report))
    if not candidates:
        return []
    _,_,file,report=min(candidates)
    station=report.get("station_results",{}).get(station_id,{})
    return [(Path(file),row,station) for row in station.get("forecasts",[])
            if row.get("model")==model and row.get("day_offset")==lead]

def build_archives():
    probe = read(CANDIDATES)
    assessment = read(ASSESSMENT)
    candidates = {(r["nominal_brt"], r["model"], r["run_utc"]): r
                  for r in probe["attempts"] if r["status"] == "retrieved_candidate"}
    created = []
    all_counts = Counter()
    for proposal in assessment["proposals"]:
        slot = proposal["nominal_local"]
        issue = normal_issue(slot)
        is_matched = proposal["validated_at_nominal"] and proposal["confidence"].startswith("historically_reproduced")
        status = "snapshot_matched" if is_matched else "vintage_inferred_publication_unverified"
        payload = {
            "record_type": "historical_forecast_recovery",
            "nominal_issue_at_local": issue.isoformat(),
            "operational_collection": False,
            "official_series_eligible": False,
            "historical_round_publication_verified": False,
            "recovery_status": status,
            "method": "Open-Meteo Single Runs API, model-specific historical run",
            "original_probe": str(CANDIDATES.relative_to(ROOT)),
            "selection_manifest": str(ASSESSMENT.relative_to(ROOT)),
            "selection_basis": proposal["confidence"],
            "model_runs_utc": proposal["models"],
            "station_results": {},
        }
        for station in STATIONS:
            sid = station["id"]
            rows = []
            grid = {}
            for model, api_name in MODELS.items():
                run_utc = proposal["models"][model]
                candidate = candidates.get((slot, api_name, run_utc))
                if not candidate:
                    raise AssertionError(f"Missing candidate {slot} {model} {run_utc}")
                site = candidate["stations"].get(sid)
                if not site or len(site["targets"]) != 3:
                    raise AssertionError(f"Missing station/lead {slot} {model} {sid}")
                if [station["lat"], station["lon"]] != site["requested_coordinates"]:
                    raise AssertionError(f"Coordinates changed {sid}")
                grid[model] = site["returned_grid"]
                units = site["units"]
                expected_units = {"direction_deg": "°", "mean_kt": "kn",
                                  "gust_kt": "kn", "precip_mm": "mm"}
                for lead in (24, 48, 72):
                    target = (issue + timedelta(hours=lead)).strftime("%Y-%m-%dT%H:%M")
                    p = site["targets"][str(lead)]
                    if p["target_brt"] != target:
                        raise AssertionError(f"Wrong target {slot} {model} {sid} {lead}")
                    vals = {}
                    for field, api_field in FIELDS.items():
                        value = p["values"].get(api_field)
                        if not numeric(value) or units.get(api_field) != expected_units[field]:
                            raise AssertionError(f"Bad forecast or unit {slot} {model} {sid} {lead} {field}")
                        vals[field] = value
                        all_counts["retrieved_values"] += 1
                    rows.append({
                        "model": model, "source": "Open-Meteo Single Runs historical",
                        "day_offset": lead // 24,
                        "nominal_horizon_hours": lead,
                        "target_local": target, "valid_at_local": target,
                        "values": vals,
                        "run_utc": run_utc,
                        "provenance": status,
                        "original_publication_at_slot_verified": False,
                    })
            payload["station_results"][sid] = {
                "name": station["name"], "lat": station["lat"], "lon": station["lon"],
                "grid_by_model": grid, "forecasts": rows}
        # On 14 Nov 18h, the retained snapshot verifies the *same run* at other stations.
        if is_matched:
            for model in MODELS:
                compared, exact = 0, 0
                for sid in STATION_IDS:
                    for lead in (24, 48, 72):
                        originals = original_comparison(issue, sid, model, lead // 24)
                        historical = next(r for r in payload["station_results"][sid]["forecasts"]
                                          if r["model"] == model and r["nominal_horizon_hours"] == lead)
                        for _, original, _ in originals:
                            for field in FIELDS:
                                value = original.get("values", {}).get(field)
                                if numeric(value):
                                    compared += 1
                                    exact += int(value == historical["values"][field])
                if compared < 48 or compared != exact:
                    raise AssertionError(f"Calibration mismatch {model}: {exact}/{compared}")
                payload.setdefault("snapshot_calibration", {})[model] = {
                    "identical_variables": exact, "compared_variables": compared}
        outfile = archive_path(issue)
        save(outfile, payload)
        created.append(str(outfile.relative_to(ROOT)))
        all_counts["slots"] += 1
        all_counts[status] += 1
    save(RECOVERED / "index.json", {
        "description": "Historical runs recovered; original operational forecast archives remain unchanged",
        "record_type": "historical_forecast_recovery_index",
        "publication_at_nominal_proven_for_missing_slots": False,
        "slots": created, "counts": dict(all_counts),
        "classification": {
            "snapshot_matched": "Matched original snapshot values exactly for retained stations/models; archive still obtained later",
            "vintage_inferred_publication_unverified": "Historical model run exists, but no timestamped proof of availability in original slot"}},
    )
    print("Archived recovered slots:", len(created), "values:", all_counts["retrieved_values"])

def build_pairs():
    """A parallel recovery-aware comparison, never altering status.json operational KPIs."""
    unreadable=[]
    originals = forecast_archives(unreadable)
    actuals = measured_by_time(set(STATION_IDS), unreadable)
    timestamp = datetime.now(TZ)
    report = {
        "generated_at": timestamp.isoformat(),
        "purpose": "recovery-aware available pairs with original vs inferred vintages kept separate",
        "baseline_status_unchanged": "data/comparisons/status.json",
        "rounds_original_publication_verified_for_missing_slots": False,
        "model_scope": list(MODELS),
        "by_slot": [], "summary": {},
    }
    total=Counter()
    for path in sorted(RECOVERED.glob("????-??-??/????.json")):
        archive=read(path)
        issue=datetime.fromisoformat(archive["nominal_issue_at_local"])
        original=originals.get(issue)
        orig_stations=original[3] if original else {}
        classification=archive["recovery_status"]
        slot=Counter()
        details=[]
        for lead in (24,48,72):
            target=issue+timedelta(hours=lead)
            if target > timestamp:
                continue
            measured = actuals.get(target, {})
            for station_id in STATION_IDS:
                st_orig=orig_stations.get(station_id, {})
                st_rec=archive["station_results"][station_id]
                actual=(measured.get(station_id) or (None,None,None,None))[2] or {}
                for model in MODELS:
                    row_orig=next((row for row in st_orig.get("forecasts", [])
                                   if row.get("model")==model and row.get("day_offset")==lead//24), None)
                    row_rec=next(r for r in st_rec["forecasts"]
                                 if r["model"]==model and r["nominal_horizon_hours"]==lead)
                    for field in FIELDS:
                        prev=row_orig.get("values",{}).get(field) if row_orig else None
                        orig_ok=numeric(prev)
                        value=prev if orig_ok else row_rec["values"][field]
                        observed=actual.get(field)
                        available=numeric(value) and numeric(observed)
                        kind=("original_capture" if orig_ok else
                              "recovered_snapshot_matched" if classification=="snapshot_matched" else
                              "recovered_inferred")
                        verified=(kind!="recovered_inferred")
                        slot["expected_pairs"]+=1
                        slot["available_pairs"]+=int(available)
                        slot["eligible_pairs"]+=int(available and verified)
                        slot[f"pair_source_{kind}"]+=int(available)
                        slot[f"forecast_source_{kind}"]+=1
                        details.append({
                            "station_id": station_id, "model": model,
                            "horizon_h": lead, "target_local":target.isoformat(),
                            "variable": field, "forecast":value, "actual": observed,
                            "pair_available":bool(available),
                            "eligible_as_verified_pair":bool(available and verified),
                            "forecast_vintage_class":kind,
                            "model_run_utc": archive["model_runs_utc"][model] if not orig_ok else None,
                            "forecast_source": (original[2] if orig_ok and original else str(path.relative_to(ROOT))),
                            "observation_source": measured.get(station_id,(None,None,None,None))[1]
                                              if station_id in measured else None,
                        })
        total.update(slot)
        report["by_slot"].append({
            "nominal_issue_at_local":issue.isoformat(),
            "recovery_status":classification,
            "source_archive":str(path.relative_to(ROOT)),
            "counts":dict(slot),"pairs":details,
        })
    report["summary"]=dict(total)
    report["unreadable_archives"]=unreadable
    save(OUTPUT,report)
    print("Recovery-aware mature pairs:",total["available_pairs"],"/",total["expected_pairs"],
          "verified:",total["eligible_pairs"],"inferred:",total["pair_source_recovered_inferred"])

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--pairs-only", action="store_true")
    args=parser.parse_args()
    if not args.pairs_only:
        build_archives()
    build_pairs()
