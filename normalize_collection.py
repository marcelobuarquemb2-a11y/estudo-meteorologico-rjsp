"""Normalize saved forecast responses without inferring missing observations."""
import math

from study_config import direction_octant, local_time, within_slot

FIELDS={"direction_deg":"wind_direction_10m","mean_kt":"wind_speed_10m","gust_kt":"wind_gusts_10m","precip_mm":"precipitation"}
MODELS={"ICON":"icon_global","GFS":"ncep_gfs_global","ECMWF":"ecmwf_ifs"}

def distance(a,b):
    p1,l1=map(math.radians,a)
    p2,l2=map(math.radians,b)
    h=math.sin((p2-p1)/2)**2+math.cos(p1)*math.cos(p2)*math.sin((l2-l1)/2)**2
    return 12742*math.asin(min(1,math.sqrt(h)))

def get_at(data,field,time):
    ts=data.get("time",[])
    if time not in ts: return None
    vals=data.get(field,[])
    i=ts.index(time)
    return vals[i] if i<len(vals) else None


def local_times(data):
    return {local_time(value): value for value in data.get("time", [])}


def nearest_time(times, target):
    return min(
        (when for when in times if within_slot(when, target)),
        key=lambda when: (abs(when - target), when),
        default=None,
    )


def normalize_collection(result):
    for sid,station in result["station_results"].items():
        if station["lat"] is None or station["lon"] is None:
            station["status"]="pending_verified_coordinates"
            continue
        sources=station["sources"]
        om=sources.get("open_meteo",{}).get("raw",{})
        basic=sources.get("meteoblue_basic-1h",{}).get("raw",{})
        wind=sources.get("meteoblue_wind-3h",{}).get("raw",{})
        hourly=om.get("hourly",{})
        units=om.get("hourly_units",{})
        grid=[om.get("latitude"),om.get("longitude")]
        offset=None
        if all(x is not None for x in grid):
            offset=round(distance((station["lat"],station["lon"]),grid),3)
        station["open_meteo_grid"]={"latitude":grid[0],"longitude":grid[1],"distance_km":offset}
        b=basic.get("data_1h",{})
        w=wind.get("data_3h",{})
        bu=basic.get("units",{})
        wu=wind.get("units",{})
        open_times = local_times(hourly)
        basic_times = local_times(b)
        wind_times = local_times(w)
        common_times = basic_times.keys() & wind_times.keys()
        target_ok = True
        rows=[]
        for day,target in enumerate(result["targets_local"],1):
            nominal = local_time(target)
            open_valid = nearest_time(open_times, nominal)
            meteoblue_valid = (
                nearest_time(common_times, nominal)
                or nearest_time(basic_times, nominal)
                or nearest_time(wind_times, nominal)
            )
            target_ok &= open_valid is not None and meteoblue_valid in common_times
            for name,suffix in MODELS.items():
                values={}
                for field,base in FIELDS.items():
                    key=base+"_"+suffix
                    expected="°" if field=="direction_deg" else ("mm" if field=="precip_mm" else "kn")
                    values[field]=get_at(hourly,key,open_times.get(open_valid)) if units.get(key)==expected else None
                rows.append({"model":name,"source":"Open-Meteo","day_offset":day,"target_local":target,
                    "valid_at_local":open_valid.isoformat() if open_valid else None,"values":values})
            basic_time = basic_times.get(meteoblue_valid)
            speed=get_at(b,"windspeed",basic_time)
            gust=get_at(w,"gust",wind_times.get(meteoblue_valid))
            direction=get_at(b,"winddirection",basic_time)
            rain=get_at(b,"precipitation",basic_time)
            rows.append({"model":"Meteoblue package (AI/mLM unverified)","source":"Meteoblue",
                "day_offset":day,"target_local":target,
                "valid_at_local":meteoblue_valid.isoformat() if meteoblue_valid else None,
                "values":{"direction_deg":direction if bu.get("winddirection")=="degree" else None,
                          "mean_kt":round(speed*1.9438444924,3) if speed is not None and bu.get("windspeed")=="ms-1" else None,
                          "gust_kt":round(gust*1.9438444924,3) if gust is not None and wu.get("windspeed")=="ms-1" else None,
                          "precip_mm":rain if bu.get("precipitation")=="mm" else None},
                "gust_height":"unverified","model_identity":"AI/mLM unverified"})
        for row in rows:
            row["direction_octant"] = direction_octant(row["values"]["direction_deg"])
        station["forecasts"]=rows
        station["completeness"]={"present":sum(v is not None for r in rows for v in r["values"].values()),"expected":len(rows)*4}
        time_ok=(om.get("timezone")=="America/Sao_Paulo" and om.get("utc_offset_seconds")==-10800
                 and basic.get("metadata",{}).get("utc_timeoffset")==-3
                 and wind.get("metadata",{}).get("utc_timeoffset")==-3)
        grid_ok=(offset is not None and offset<=30 and -24.5<grid[0]<-21 and -46<grid[1]<-42)
        station["notation_audit"]={"timezone_ok":time_ok,"date_and_target_ok":target_ok,
                                   "grid_sanity_ok":grid_ok,"passed":time_ok and target_ok and grid_ok}
        if not station["notation_audit"]["passed"]:
            station["errors"].append("Notation audit failed")
    result["collection_summary"]={
        "stations_total":len(result["station_results"]),
        "stations_with_coordinates":sum(s["lat"] is not None for s in result["station_results"].values()),
        "forecast_values_present":sum(s.get("completeness",{}).get("present",0) for s in result["station_results"].values()),
        "notation_audits_passed":sum(s.get("notation_audit",{}).get("passed",False) for s in result["station_results"].values())}
