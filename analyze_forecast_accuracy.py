#!/usr/bin/env python3
"""Accuracy by model, lead, variable and station, after the 11 Nov collection change.

Uses unchanged official snapshots + separately recovered historical model runs.
The direction test follows the USER-APPROVED same-octant OR circular <30°
criterion, consistent with the current compare_forecasts.py rule.
"""
import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from compare_forecasts import MODEL_NAMES, forecast_archives, measured_by_time
from study_config import ROOT, STATIONS, TZ, SLOT_HOURS, direction_octant

ISSUE_START=datetime(2026, 11, 11, 12, tzinfo=TZ)
TARGET_END=datetime(2026, 11, 15, 18, tzinfo=TZ)
LEADS=(24,48,72)
FIELDS=("mean_kt","gust_kt","direction_deg","precip_mm")
LABELS={"mean_kt":"Vento médio","gust_kt":"Rajadas","direction_deg":"Direção","precip_mm":"Precipitação"}
OUTPUT=ROOT/"data"/"comparisons"
RECOVERED=ROOT/"data"/"recovered_forecasts"
SITE_IDS=[x["id"] for x in STATIONS if x.get("enabled",True)]
SITE_NAMES={x["id"]:x["name"] for x in STATIONS}
METEOBLUE="Meteoblue package (AI/mLM unverified)"
MODELS=("ICON","GFS","ECMWF",METEOBLUE)
assert set(MODELS)==set(MODEL_NAMES)
assert len(SITE_IDS)==6

def number(value):
    return type(value) in (int,float) and math.isfinite(value)

def calc_hit(field, forecast, actual):
    if field=="direction_deg":
        circular=abs((forecast-actual+180)%360-180)
        same_octant=direction_octant(forecast)==direction_octant(actual)
        return (same_octant or circular<30.0, round(circular,6))
    if field in ("mean_kt","gust_kt"):
        difference=abs(forecast-actual)
        return (difference<=2.0,round(difference,6))
    if field=="precip_mm":
        return ((forecast>0.2)==(actual>0.2),None)
    raise ValueError(field)

def original_row(station, model, target, lead):
    for item in station.get("forecasts",[]):
        if item.get("model")==model and item.get("day_offset")==lead//24:
            if item.get("target_local")!=target.strftime("%Y-%m-%dT%H:%M"):
                continue
            return item
    return None

def recovered_rows(issue):
    path=RECOVERED/issue.strftime("%Y-%m-%d")/f"{issue:%H%M}.json"
    if not path.exists():
        return {},None,None
    report=json.loads(path.read_text(encoding="utf-8"))
    assert not report["official_series_eligible"]
    assert datetime.fromisoformat(report["nominal_issue_at_local"])==issue
    return report.get("station_results",{}),report.get("recovery_status"),str(path.relative_to(ROOT))

def new_counter():
    return {"expected":0,"complete_pairs":0,"hits":0,"confirmed_pairs":0,
            "confirmed_hits":0,"provisional_pairs":0,"provisional_hits":0,
            "original_pairs":0,"snapshot_matched_pairs":0}

def pct(hit,count):
    return hit/count if count else None

def main():
    unreadable=[]
    original=forecast_archives(unreadable)
    observed=measured_by_time(set(SITE_IDS),unreadable)
    counters=defaultdict(new_counter)
    records=[]
    dt=ISSUE_START
    while dt<=TARGET_END:
        if dt.hour not in SLOT_HOURS:
            dt+=timedelta(hours=1)
            continue
        orig_pack=original.get(dt)
        orig_sites=orig_pack[3] if orig_pack else {}
        orig_file=orig_pack[2] if orig_pack else None
        historic_sites,rec_status,rec_file=recovered_rows(dt)
        for lead in LEADS:
            target=dt+timedelta(hours=lead)
            if target>TARGET_END:
                continue
            measurements=observed.get(target,{})
            for sid in SITE_IDS:
                st=orig_sites.get(sid,{})
                same_coordinates=(
                    st.get("lat")==next(s["lat"] for s in STATIONS if s["id"]==sid)
                    and st.get("lon")==next(s["lon"] for s in STATIONS if s["id"]==sid)
                )
                observed_station=measurements.get(sid)
                actual_values=(observed_station[2] or {}) if observed_station else {}
                actual_source=observed_station[1] if observed_station else None
                rec_site=historic_sites.get(sid,{})
                for model in MODELS:
                    original_item=original_row(st,model,target,lead) if same_coordinates else None
                    rec_item=next((x for x in rec_site.get("forecasts",[])
                                   if x.get("model")==model and x.get("nominal_horizon_hours")==lead),None)
                    for field in FIELDS:
                        forecast=(original_item or {}).get("values",{}).get(field)
                        source_class="original_capture" if number(forecast) else "missing_forecast"
                        forecast_file=orig_file if number(forecast) else None
                        run_utc=None
                        if not number(forecast) and rec_item:
                            fallback=rec_item.get("values",{}).get(field)
                            if number(fallback):
                                forecast=fallback
                                source_class=("recovered_snapshot_matched" if rec_status=="snapshot_matched"
                                              else "recovered_inferred")
                                forecast_file=rec_file
                                run_utc=rec_item.get("run_utc")
                        actual=actual_values.get(field)
                        available=number(forecast) and number(actual)
                        verified=available and source_class!="recovered_inferred"
                        hit,error=calc_hit(field,forecast,actual) if available else (None,None)
                        rec={
                            "issue_brt":dt.isoformat(),"target_brt":target.isoformat(),
                            "station":sid,"station_name":SITE_NAMES[sid],
                            "model":model,"lead_h":lead,"variable":field,
                            "forecast":forecast if number(forecast) else None,
                            "actual":actual if number(actual) else None,
                            "forecast_source_class":source_class,
                            "forecast_file":forecast_file,"observations_file":actual_source,
                            "historical_run_utc":run_utc,
                            "pair_complete":int(available),
                            "vintage_verified":int(verified),
                            "hit":int(hit) if available else None,
                            "error_abs":error
                        }
                        records.append(rec)
                        for key in [
                            (model,lead,field,sid),(model,lead,field,"TOTAL"),
                            (model,"ALL",field,sid),(model,"ALL",field,"TOTAL"),
                            ("ALL",lead,field,sid),("ALL",lead,field,"TOTAL"),
                            ("ALL","ALL",field,sid),("ALL","ALL",field,"TOTAL"),
                            (model,lead,"ALL",sid),(model,lead,"ALL","TOTAL"),
                            (model,"ALL","ALL",sid),(model,"ALL","ALL","TOTAL")
                        ]:
                            agg=counters[key]
                            agg["expected"]+=1
                            if available:
                                agg["complete_pairs"]+=1
                                agg["hits"]+=int(hit)
                                if verified:
                                    agg["confirmed_pairs"]+=1
                                    agg["confirmed_hits"]+=int(hit)
                                else:
                                    agg["provisional_pairs"]+=1
                                    agg["provisional_hits"]+=int(hit)
                                if source_class=="original_capture":
                                    agg["original_pairs"]+=1
                                if source_class=="recovered_snapshot_matched":
                                    agg["snapshot_matched_pairs"]+=1
        dt+=timedelta(hours=1)
    expected=33*6*4*4
    assert len(records)==expected,(len(records),expected)
    main_total=counters[("ALL","ALL","ALL","TOTAL")]
    assert main_total["expected"]==expected
    # Preserve any changed observation availability rather than forcing stale old dashboard metrics.
    summary=[]
    for (model,lead,field,sid),v in sorted(counters.items()):
        row={"model":model,"lead_h":lead,"variable":field,"station":sid,
             "station_name":SITE_NAMES.get(sid,"Todas as estações"),
             **v,
             "accuracy_pct":pct(v["hits"],v["complete_pairs"]),
             "confirmed_accuracy_pct":pct(v["confirmed_hits"],v["confirmed_pairs"]),
             "coverage_pct":pct(v["complete_pairs"],v["expected"])}
        summary.append(row)
    OUTPUT.mkdir(parents=True,exist_ok=True)
    report={
        "created_at_brt":datetime.now(TZ).isoformat(),
        "analysis_period":{"first_issue":ISSUE_START.isoformat(),"last_target":TARGET_END.isoformat(),
                           "mature_nominal_issue_lead_combinations":33},
        "source":"Operational archives and separately recovered model cycles; actuals from verified stations",
        "source_files":["data/comparisons/status.json","data/comparisons/recovered_pairs_status.json",
                        "data/recovered_forecasts/index.json"],
        "direction_rule":"Same octant (45 degrees sectors centered on N, NE, E, SE, S, SW, W, NW) OR circular angular difference strictly <30 degrees. Boundary 30 is error unless octants equal.",
        "mean_kt_rule":"abs(pred-actual) <=2 kt",
        "gust_kt_rule":"abs(pred-actual) <=2 kt",
        "precip_mm_rule":"(pred >0.2 mm/h)==(actual>0.2 mm/h); 0.2 is no rain",
        "denominator":"complete forecast x actual pairs; incomplete pairs excluded (not misses)",
        "provisional_rule":"Historically inferred rounds not confirmed available at nominal; separate strict counts",
        "meteoblue_note":"basic-1h + wind-3h package; AI/mLM identity unverified",
        "expected_records":expected,
        "overall":main_total,"summary":summary,
        "unreadable_archives":unreadable
    }
    (OUTPUT/"accuracy_postchange_20261115.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    with (OUTPUT/"accuracy_pairs_postchange_20261115.csv").open("w",encoding="utf-8",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    with (OUTPUT/"accuracy_summary_postchange_20261115.csv").open("w",encoding="utf-8",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)
    print("Scored "+str(len(records))+" scheduled pairs, "+str(main_total["complete_pairs"])+" complete, "+
          str(main_total["hits"])+" hits. Confirmed "+str(main_total["confirmed_pairs"])+
          " Provisional "+str(main_total["provisional_pairs"]))
    print("Overall hit rate",pct(main_total["hits"],main_total["complete_pairs"]))
    print("Summary dimensions",len(summary),"Unreadable sources",len(unreadable))

if __name__=="__main__":
    main()
