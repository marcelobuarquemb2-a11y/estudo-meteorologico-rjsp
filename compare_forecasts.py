#!/usr/bin/env python3
"""Join original 15h forecast vintages with measured observations; missing remains null."""
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parent
TZ=ZoneInfo("America/Sao_Paulo")
START=dt.date(2026,11,6)
KEYS=("direction_deg","mean_kt","gust_kt","precip_mm")
def main():
    from datetime import datetime
    now=dt.datetime.now(TZ)
    observation_files=sorted((ROOT/"data"/"observations"/"reports").glob("*/*.json"))
    forecast_files=sorted((ROOT/"data"/"2026-11").glob("*/*.json"))
    # Each observed timestamp is joined only with a distinct forecast archive.
    # A source may be polled repeatedly; use the strongest genuine measured response
    # per observation instant, then most recent retrieval. Never count retries twice.
    by_valid_time={}
    for path in observation_files:
        ob=json.loads(path.read_text(encoding="utf-8"))
        key=ob.get("target_local")
        if not key:continue
        quality=sum(sum(v is not None for v in (st.get("observed") or {}).values())
                    for st in ob.get("stations",{}).values())
        rank=(quality,ob.get("acquired_at_brt",""))
        if key not in by_valid_time or rank>by_valid_time[key][0]:
            by_valid_time[key]=(rank,path)
    reports=[]
    for rank,path in sorted(by_valid_time.values(),key=lambda x:x[1].name):
        ob=json.loads(path.read_text(encoding="utf-8"))
        valid=dt.datetime.fromisoformat(ob["target_local"])
        if valid.hour not in (9,12,15,18) or valid.date()<START: continue
        matched=[]
        for sid,observed in ob["stations"].items():
            if sid=="dois_rios":continue
            actual=observed.get("observed")
            for hours in (24,48,72):
                issue_date=valid.date()-dt.timedelta(days=hours//24)
                if issue_date<START:continue
                # Only archived predictions issued on the appropriate calendar day and nominal time.
                possible=[]
                for src in forecast_files:
                    if src.parent.name!=issue_date.isoformat():continue
                    if src.stem.isdigit() and src.stem[:2]!=f"{valid.hour:02d}":continue
                    candidate=json.loads(src.read_text(encoding="utf-8"))
                    if not candidate.get("official_series_eligible"):continue
                    if candidate.get("nominal_slot_hour_brt")!=valid.hour:continue
                    station=candidate.get("station_results",{}).get(sid,{})
                    for row in station.get("forecasts",[]):
                        if row.get("target_local")==valid.strftime("%Y-%m-%dT%H:%M"):
                            possible.append((candidate["consulted_at"],str(src.relative_to(ROOT)),row))
                # Never choose multiple conflicting snapshots as though they were independent.
                if not possible:continue
                consultation,path_src,row=max(possible,key=lambda x:x[0])
                values=row.get("values",{})
                forecast_dt=dt.datetime.fromisoformat(consultation)
                lead=(valid-forecast_dt).total_seconds()/3600
                matched.append({"station_id":sid,"valid_local":valid.isoformat(),
                    "model":row["model"],"nominal_horizon_hours":hours,
                    "actual_lead_hours":round(lead,3),
                    "forecast_source":path_src,"observation_source":str(path.relative_to(ROOT)),
                    "forecast":{key:values.get(key) for key in KEYS},
                    "observed":actual,
                    "comparable":actual is not None and all(actual.get(k) is not None for k in KEYS)})
        reports.append({"observed_time":valid.isoformat(),"observation_file":str(path.relative_to(ROOT)),
            "rows":matched,"matched_rows":len(matched),
            "rows_with_real_observations":sum(x["comparable"] for x in matched)})
    out=ROOT/"data"/"comparisons"
    out.mkdir(parents=True,exist_ok=True)
    filename=out/"status.json"
    filename.write_text(json.dumps({"generated_at":now.isoformat(),
         "series_start_local":"2026-11-06T15:00:00-03:00",
         "first_complete_nominal_15h_frame":"2026-11-09T15:00:00-03:00",
         "frames":reports},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("Comparison frames:",len(reports))
    print("Real matched comparisons:",sum(x["rows_with_real_observations"] for x in reports))
if __name__=="__main__":
    main()
