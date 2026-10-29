"""Preview or import the local input files. Leaves originals untouched."""
import argparse
import json
from contextlib import closing
from pathlib import Path
from core import connect, parse_file, preview, commit_file, report, rupees

ROOT=Path(__file__).resolve().parent

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--save',action='store_true')
    parser.add_argument('--db',type=Path,default=ROOT/'data'/'flipkart.sqlite3')
    args=parser.parse_args()
    parsed=[]
    for path in sorted((ROOT/'input').iterdir()):
        if path.suffix.lower() not in ('.xlsx','.csv'):
            continue
        payload=path.read_bytes()
        result=parse_file(path.name,payload)
        parsed.append((result,payload))
        print(json.dumps(preview(result),ensure_ascii=True))
    if any(r['errors'] for r,_ in parsed):
        raise SystemExit('Import stopped: correct the errors shown above.')
    if args.save:
        with closing(connect(args.db)) as db:
            for r,payload in sorted(parsed,key=lambda p:p[0]['kind']!='orders'):
                result=commit_file(db,r,payload)
                print(r['name'],json.dumps({k:result.get(k) for k in ('added','updated','skipped','duplicate_file')}))
            data=report(db)
            print(json.dumps(data['totals'],ensure_ascii=True))

if __name__=='__main__':
    main()
