"""Versioned, self-contained export consumed by the local Go API."""
import hashlib
import json


SCHEMA_VERSION = 1
RULE_VERSION = 'roles-v2-temporal-exact'


def make_snapshot(payload, transactions, csvs):
    """Use validated, canonically ordered inputs; never round IDs through float.

    row_id is an export ordinal, not a bank transaction ID. Identical source
    operations remain distinct rows. CSV digests bind the API to the same run.
    """
    rows = []
    for index, row in enumerate(transactions.itertuples(index=False)):
        cents = int(round(row.sum_kzt * 100))
        rows.append(dict(row_id=index, src=str(row.src), dst=str(row.dst),
                         date=row.date.date().isoformat(), sum_kzt=cents / 100, cents=cents))
    digests = {name: hashlib.sha256(content.encode('utf-8')).hexdigest()
               for name, content in csvs.items()}
    identity = dict(schema_version=SCHEMA_VERSION, rule_version=RULE_VERSION,
                    input_sha256=payload['summary']['input_sha256'],
                    config=payload['config'], output_sha256=digests)
    snapshot_id = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False,
                                            allow_nan=False, separators=(',', ':')).encode('utf-8')).hexdigest()
    return dict(schema_version=SCHEMA_VERSION, rule_version=RULE_VERSION,
                snapshot_id=snapshot_id, nodes=payload['nodes'], edges=payload['edges'],
                transactions=rows, clusters=payload['clusters'], top=payload['top'],
                summary=payload['summary'], config=payload['config'], output_sha256=digests)
