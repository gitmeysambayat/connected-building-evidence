"""Reproduce the full publisher RDF-to-CSV audit without inflating the demo pack.

Offline: --cache PATH containing the 36 original MeasurementsTurtle files.
Online: --download fetches about 116 MB of public pinned upstream RDF to cache.
The compact application retains a prior audit and short excerpts. Normal data
imports do not claim to rerun this full optional check.
"""
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from datetime import datetime, timezone
import argparse
import csv
import hashlib
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from twin import data, settings


def verify(cache: Path, download: bool = False) -> dict:
    info, audit = data.manifest(), data.source_audit()
    data._verify_sources(info)
    checks = audit['rdf_to_csv_checks']
    cache.mkdir(parents=True,exist_ok=True)

    def fetch(check):
        path = cache / check['rdf_file']
        if not path.exists():
            if not download:
                raise FileNotFoundError('Missing cached RDF: ' + check['rdf_file'] + '. Pass --download to fetch the pinned public source.')
            url = 'https://raw.githubusercontent.com/TechnicalBuildingSystems/OpenSmartHomeData/' + settings.SOURCE_COMMIT + '/MeasurementsTurtle/' + quote(check['rdf_file'])
            with urlopen(url,timeout=60) as response:
                payload = response.read(8_000_001)
            if len(payload)>8_000_000:
                raise ValueError('Unexpectedly large RDF file.')
            if hashlib.sha256(payload).hexdigest() != check['source_sha256']:
                raise ValueError('Downloaded source SHA-256 mismatch: ' + check['rdf_file'])
            temporary = path.with_suffix('.tmp')
            temporary.write_bytes(payload)
            temporary.replace(path)
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != check['source_sha256']:
            raise ValueError('Cached source SHA-256 mismatch: ' + check['rdf_file'])
        return check,payload

    with ThreadPoolExecutor(max_workers=6) as executor:
        sources = list(executor.map(fetch,checks))
    results = []
    for check,payload in sources:
        csv_path = settings.SOURCE_DIR / 'Measurements' / check['csv_file']
        with csv_path.open() as stream:
            rows = [(int(row[0]),float(row[1])) for row in csv.reader(stream,delimiter='\t')]
        raw = payload.decode('utf-8')
        namespace = re.search(r'@prefix osh:\s*<([^>]+)>',raw).group(1)
        blocks = re.findall(r'osh:[^\s]+?-obs(\d+)\s+(.*?)(?=\n\n|\Z)',raw,re.DOTALL)
        seen, features, sensors, actuators, units = set(),Counter(),Counter(),Counter(),Counter()
        mismatches = []
        for index_text,block in blocks:
            index = int(index_text)
            if index in seen:
                mismatches.append({'index':index,'reason':'Repeated observation index.'})
            seen.add(index)
            feature = re.search(r'sosa:hasFeatureOfInterest\s+osh:([^\s;]+)',block)
            sensor = re.search(r'sosa:madeBySensor\s+osh:([^\s;]+)',block)
            actuator = re.search(r'sosa:(?:madeByActuator|actuationMadeBy)\s+osh:([^\s;]+)',block)
            unit = re.search(r'om:hasUnit\s+om:([^\s;\]]+)',block)
            time = re.search(r'sosa:resultTime\s+"([^"]+)"',block)
            value = re.search(r'om:hasNumericalValue\s+([^\s;]+)',block)
            for term,counter in ((feature,features),(sensor,sensors),(actuator,actuators),(unit,units)):
                if term: counter[term.group(1)] += 1
            if index>=len(rows) or not time or not value or not feature or not unit:
                mismatches.append({'index':index,'reason':'Missing required field or out-of-range source index.'})
                continue
            stamp = datetime.fromisoformat(time.group(1))
            if stamp.tzinfo is None or int(stamp.timestamp()) != rows[index][0] or abs(float(value.group(1))-rows[index][1])>1e-7:
                mismatches.append({'index':index,'reason':'Timestamp/UTC offset or numerical value differs from CSV.'})
        coverage = seen == set(range(len(rows)))
        identity = dict(features)==check['feature_ids'] and dict(sensors)==check['sensor_ids'] and dict(actuators)==check['actuator_ids']
        units_match = dict(units)==check['units']
        passed = not mismatches and coverage and identity and units_match and namespace==check['source_osh_namespace']
        results.append({'rdf_file':check['rdf_file'],'csv_file':check['csv_file'],
                        'rdf_sha256':hashlib.sha256(payload).hexdigest(),'rdf_observations':len(blocks),
                        'csv_rows':len(rows),'all_source_indices_present':coverage,
                        'feature_device_identity_matches':identity,'units_match':units_match,
                        'source_osh_namespace':namespace,'timestamp_value_mismatches':len(mismatches),
                        'first_mismatches':mismatches[:5],'passed':passed})
    return {'generated_at_utc':datetime.now(timezone.utc).isoformat(),'repository':info['repository'],
            'source_commit':settings.SOURCE_COMMIT,'licence':info['licence'],
            'verification_method':'All pinned original RDF file hashes verified, then each observation/actuation block matched to the indexed CSV row, explicit-offset timestamp, numerical value, feature, device and unit.',
            'rdf_files_checked':len(results),'rdf_observations_checked':sum(x['rdf_observations'] for x in results),
            'passed':all(x['passed'] for x in results),'details':results,
            'unverified_csv_without_rdf':audit['csv_without_rdf_file'],
            'claim_limit':'This verifies source consistency and the published room association. It does not independently verify device calibration, survey accuracy, sensor positions or a live physical connection.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache',type=Path,default=ROOT/'data'/'rdf_verification_cache')
    parser.add_argument('--download',action='store_true')
    parser.add_argument('--output',type=Path,default=ROOT/'reports'/'20261018_v1_Upstream_RDF_Verification.json')
    args=parser.parse_args()
    report=verify(args.cache,args.download)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({key:value for key,value in report.items() if key!='details'},indent=2))
    raise SystemExit(0 if report['passed'] else 1)
