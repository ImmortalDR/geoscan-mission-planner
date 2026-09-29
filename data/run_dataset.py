"""Build or verify the H3 handoff. Verification does not import H1/H2."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET

from h3check import check_result, read_json, timestamp
from generate_inputs import write_json

ROOT=Path(__file__).resolve().parent


def cases(prefixes=None):
    for config in sorted((ROOT/"scenarios").glob("*/scenario.json")):
        if prefixes and not any(config.parent.name.startswith(p) for p in prefixes):
            continue
        yield config.parent
        alternate=config.parent/"alternative"
        if (alternate/"scenario.json").exists():
            yield alternate


def expectations(case, result, report):
    spec=read_json(case/"scenario.json"); checks=spec["checks"]; errors=[]
    if result["status"]!=spec["expected_status"]:
        errors.append("Unexpected status")
    sorties=result["sorties"]
    if len(sorties)<checks.get("minimum_sorties",0): errors.append("Too few sorties")
    if len({s["uav_id"] for s in sorties})<checks.get("minimum_used_uavs",0): errors.append("Too few used UAVs")
    if checks.get("different_start_end") and not any(s["start_site"]!=s["landing_site"] for s in sorties):
        errors.append("Different start/end not exercised")
    if checks.get("not_before") and any(timestamp(s["t_start"],"start")<timestamp(checks["not_before"],"limit") for s in sorties):
        errors.append("Temporal closure not respected")
    if "survey_area_m2" in checks and abs(report["metrics"]["required_area_m2"]-checks["survey_area_m2"])>1:
        errors.append("Incorrect survey area")
    if "max_fleet" in checks and len(read_json(case/"input/fleet.json")["uavs"])>checks["max_fleet"]:
        errors.append("Fleet limit exceeded")
    return errors


def exports(result, directory):
    ns="http://www.opengis.net/kml/2.2"; ET.register_namespace("",ns)
    k=ET.Element(f"{{{ns}}}kml"); document=ET.SubElement(k,"Document")
    features=[]; schedule=[]
    for s in result["sorties"]:
        coords=[[p["lon"],p["lat"],p["amsl_m"]] for p in s["waypoints"]]
        features.append(dict(type="Feature",geometry=dict(type="LineString",coordinates=coords),
                             properties={k:v for k,v in s.items() if k!="waypoints"}))
        placemark=ET.SubElement(document,"Placemark"); ET.SubElement(placemark,"name").text=s["id"]
        line=ET.SubElement(placemark,"LineString"); ET.SubElement(line,"altitudeMode").text="absolute"
        ET.SubElement(line,"coordinates").text=" ".join(",".join(map(str,c)) for c in coords)
        schedule.append({k:v for k,v in s.items() if k!="waypoints"})
    write_json(directory/"routes.geojson",dict(type="FeatureCollection",features=features))
    ET.ElementTree(k).write(directory/"routes.kml",encoding="utf-8",xml_declaration=True)
    write_json(directory/"schedule.json",schedule)


def check_exports(result, directory):
    geo=read_json(directory/"routes.geojson")
    kml=ET.parse(directory/"routes.kml")
    ns={"k":"http://www.opengis.net/kml/2.2"}
    lines=kml.findall(".//k:LineString/k:coordinates",ns)
    if len(lines)!=len(result["sorties"]) or len(geo["features"])!=len(lines):
        raise ValueError("Export sortie count mismatch")
    for s,f,line in zip(result["sorties"],geo["features"],lines):
        expected=[[p["lon"],p["lat"],p["amsl_m"]] for p in s["waypoints"]]
        parsed=[[float(x) for x in triple.split(",")] for triple in line.text.split()]
        if expected!=f["geometry"]["coordinates"] or expected!=parsed:
            raise ValueError("Export coordinate roundtrip mismatch")
    schedule=read_json(directory/"schedule.json")
    if schedule!=[{k:v for k,v in s.items() if k!="waypoints"} for s in result["sorties"]]:
        raise ValueError("Schedule roundtrip mismatch")


def build(case, objective):
    spec=read_json(case/"scenario.json")
    if spec["expected_status"]=="INFEASIBLE":
        result=dict(schema="geoscan.h3.result.v1",scene_id=read_json(case/"input/metadata.json")["scenario_id"],
                    objective=objective,status="INFEASIBLE",optimality={"status":"unknown"},metrics={},sorties=[],
                    diagnosis={"proof":spec["proof"],"message":spec["title"]})
        if (case/"alternative").exists():
            result["recommendations"]=[{"change":"Activate the separate NEAR site input", "paired_case":"alternative/",
                                         "requires_new_input_and_revalidation":True}]
    else:
        from reference_builder import build_reference
        result=build_reference(case/"input",objective)
    report=check_result(case/"input",result,check_claims=False)
    result["metrics"]=report["metrics"]
    destination=case/"expected"/objective
    if not report["passed"]:
        destination=ROOT/".work"/case.parent.name/case.name/objective
    write_json(destination/"result.json",result)
    write_json(destination/"validation.json",report)
    if not report["passed"]:
        raise ValueError(f"Independent check failed: {report['violations'][:5]}; details {destination}")
    report=check_result(case/"input",result)
    extra=expectations(case,result,report)
    if not report["passed"] or extra: raise ValueError(str(report["violations"][:5])+str(extra))
    write_json(destination/"validation.json",report)
    exports(result,destination); check_exports(result,destination)
    return report


def verify(case, objective, refresh=False):
    destination=case/"expected"/objective
    result=read_json(destination/"result.json")
    report=check_result(case/"input",result)
    extra=expectations(case,result,report)
    if not report["passed"] or extra: raise ValueError(str(report["violations"][:5])+str(extra))
    check_exports(result,destination)
    if refresh:
        write_json(destination/"validation.json",report)
    return report


def manifest():
    paths=[]
    for path in sorted(ROOT.rglob("*")):
        rel=path.relative_to(ROOT)
        if not path.is_file() or path.name=="manifest.json" or any(part.startswith(".") or part=="__pycache__" for part in rel.parts):
            continue
        paths.append(dict(path=str(rel),bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    write_json(ROOT/"manifest.json",dict(schema="geoscan.h3.manifest.v1",files=paths))


def check_integrity():
    index=read_json(ROOT/"manifest.json")
    if index.get("schema")!="geoscan.h3.manifest.v1" or not index.get("files"):
        raise ValueError("Invalid manifest")
    for entry in index["files"]:
        path=(ROOT/entry["path"]).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            raise ValueError(f"Missing or invalid manifest path: {entry['path']}")
        if hashlib.sha256(path.read_bytes()).hexdigest()!=entry["sha256"] or path.stat().st_size!=entry["bytes"]:
            raise ValueError(f"Integrity mismatch: {entry['path']}")
    print(f"Integrity: {len(index['files'])} files PASS",flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=["build","verify","manifest"])
    parser.add_argument("--only",nargs="*")
    parser.add_argument("--integrity",action="store_true")
    parser.add_argument("--refresh-reports",action="store_true")
    args=parser.parse_args()
    if args.command=="manifest": manifest(); return 0
    if args.integrity:
        check_integrity()
    failures=[]; count=0
    for case in cases(args.only):
        for objective in read_json(case/"scenario.json")["objectives"]:
            label=f"{case.relative_to(ROOT/'scenarios')} / {objective}"
            print(f"{args.command}: {label}",flush=True)
            try:
                report=build(case,objective) if args.command=="build" else verify(case,objective,args.refresh_reports)
                print(f"  PASS {report['metrics'].get('coverage_percent','proof')}%, {report['metrics'].get('sortie_count',0)} sorties",flush=True)
                count+=1
            except Exception as exc:
                print(f"  FAIL {exc}",flush=True); failures.append({"case":label,"error":str(exc)})
    if not args.only and count+len(failures)!=14:
        failures.append({"case":"suite","error":"Expected exactly 14 objective/input pairs across 12 main scenes"})
    print(json.dumps(dict(passed=count,failed=len(failures),failures=failures),ensure_ascii=False,indent=2))
    return 1 if failures else 0


if __name__=="__main__":
    raise SystemExit(main())
