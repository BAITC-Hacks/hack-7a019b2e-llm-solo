"""Versioned, self-contained JSON for the offline TRACE frontend."""
import hashlib
import json


SCHEMA_VERSION = 1
RULE_VERSION = 'roles-v3-witnesses-motifs'


def analysis_digest(snapshot):
    """Bind explanatory facts and overview data, as well as the required CSVs."""
    content = {key: snapshot[key] for key in
               ('nodes', 'components', 'motif_rankings', 'sensitivity_profiles')}
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True,
                         allow_nan=False, separators=(',', ':')).encode('utf-8')).hexdigest()


def make_snapshot(payload, transactions, csvs):
    """Use validated, canonically ordered inputs; never round IDs through float.

    row_id is an export ordinal, not a bank transaction ID. Identical source
    operations remain distinct rows. CSV digests identify the corresponding exports.
    """
    rows = []
    for index, row in enumerate(transactions.itertuples(index=False)):
        cents = int(round(row.sum_kzt * 100))
        rows.append(dict(row_id=index, src=str(row.src), dst=str(row.dst),
                         date=row.date.date().isoformat(), sum_kzt=cents / 100, cents=cents))
    digests = {name: hashlib.sha256(content.encode('utf-8')).hexdigest()
               for name, content in csvs.items()}
    details = dict(nodes=payload['nodes'], components=payload['components'],
                   motif_rankings=payload['motif_rankings'],
                   sensitivity_profiles=payload['sensitivity_profiles'])
    detail_hash = analysis_digest(details)
    identity = dict(schema_version=SCHEMA_VERSION, rule_version=RULE_VERSION,
                    input_sha256=payload['summary']['input_sha256'],
                    config=payload['config'], output_sha256=digests, analysis_sha256=detail_hash)
    snapshot_id = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False,
                                            allow_nan=False, separators=(',', ':')).encode('utf-8')).hexdigest()
    return dict(schema_version=SCHEMA_VERSION, rule_version=RULE_VERSION,
                snapshot_id=snapshot_id, **details, edges=payload['edges'],
                transactions=rows, clusters=payload['clusters'], top=payload['top'],
                summary=payload['summary'], config=payload['config'], output_sha256=digests,
                analysis_sha256=detail_hash)
