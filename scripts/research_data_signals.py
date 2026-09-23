"""Local-only exploratory measurements; does not modify product or source data."""
import json
from datetime import datetime, timezone
from collections import defaultdict, deque
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import networkx as nx
import pandas as pd
from moneygraph.io import load, input_hashes
from moneygraph.analysis import analyze, read_config

root = Path(__file__).resolve().parents[1]
nodes, edges, tx = load(root / 'data')
cfg = read_config()
g, f, clusters, top = analyze(nodes, edges, tx, cfg)
ranked_gids = f.sort_values(['priority_score','gid'],ascending=[False,True]).gid.tolist()
rank_by_gid = {int(gid): rank for rank, gid in enumerate(ranked_gids,1)}
f['rank'] = f.gid.map(rank_by_gid)
f['component_size'] = f.component_id.map(f.component_id.value_counts())
seeds = sorted(int(x) for x in nodes.loc[nodes.is_seed, 'gid'])

def quant(s):
    return {str(k):float(v) for k,v in s.quantile([0,.25,.5,.75,.9,.95,.99,1]).items()}

def records(frame):
    result = json.loads(frame.to_json(orient='records'))
    # All input gids have <=18 digits; pandas JSON parser here preserves integer tokens.
    for r in result:
        if 'gid' in r: r['gid'] = str(r['gid'])
    return result

daily_edges=tx.groupby(['src','dst']).agg(n_days=('date','nunique'), n_tx=('date','size'))
daily=defaultdict(lambda:defaultdict(lambda:[0,0]))
for row in tx.itertuples():
    if row.src == row.dst: continue
    day=row.date.toordinal()
    daily[int(row.dst)][day][0]+=int(round(row.sum_kzt*100))
    daily[int(row.src)][day][1]+=int(round(row.sum_kzt*100))

def lagged_compat(min_lag=0,max_lag=2):
    result={}
    for gid,days in daily.items():
        pending=deque();matched=total=0
        for day,(incoming,outgoing) in sorted(days.items()):
            total+=incoming
            while pending and pending[0][0] < day-max_lag: pending.popleft()
            if min_lag == 0 and incoming:pending.append([day,incoming])
            while outgoing and pending:
                if pending[0][0] > day-min_lag:break
                amount=min(outgoing,pending[0][1]);outgoing-=amount;matched+=amount;pending[0][1]-=amount
                if pending[0][1] == 0:pending.popleft()
            if min_lag > 0 and incoming:pending.append([day,incoming])
        result[gid]=matched/total if total else 0
    return result

f['temporal_1_2d']=f.gid.map(lagged_compat(1,2)).fillna(0)
f['timing_depends_same_day']=f.temporal_2d-f.temporal_1_2d

# Count date-compatible seed paths of at most 4 edges. Nondecreasing dates;
# therefore optimistic when operations share a date, not causal proof.
outgoing=defaultdict(list)
for r in tx.itertuples():
    if r.src != r.dst:outgoing[int(r.src)].append((int(r.dst),r.date.toordinal()))
temporal_seeds=defaultdict(set)
struct_seeds=defaultdict(set)
for seed in seeds:
    for target in nx.single_source_shortest_path_length(g,seed,cutoff=cfg['max_depth']):
        if target != seed:struct_seeds[target].add(seed)
    frontier={seed:0}
    for depth in range(cfg['max_depth']):
        nxt={}
        for source,earliest in frontier.items():
            for target,day in outgoing[source]:
                if day >= earliest and day < nxt.get(target,10**10):nxt[target]=day
        frontier=nxt
        for target,day in nxt.items():
            if target != seed:temporal_seeds[target].add(seed)
f['temporal_seed_reach']=f.gid.map(lambda x:len(temporal_seeds[x]))
f['seed_path_gap']=f.seed_reach-f.temporal_seed_reach

cols=['gid','depth','is_seed','role','role_score','priority_score','rank','component_id','component_size','in_deg','out_deg','in_kzt','out_kzt','in_tx','out_tx','pass_through','seed_reach','temporal_seed_reach','seed_path_gap','temporal_2d','temporal_1_2d','active_days','truncated_by_depth','matched_roles']
transit_candidates=f[(~f.is_seed)&(~f.truncated_by_depth)&(f.in_deg>0)&(f.out_deg>0)&f.pass_through.between(.8,1.2)]
top20=f.set_index('gid').loc[top.head(20).gid].reset_index()
component_sizes=sorted([len(x) for x in nx.weakly_connected_components(g)],reverse=True)
scc=[x for x in nx.strongly_connected_components(g) if len(x)>1]
round_trip=sum(1 for u,v in g.edges if u<v and g.has_edge(v,u))
date_3cycles=set()
for a in g:
    for b in g.successors(a):
        if a==b:continue
        for c in g.successors(b):
            if len({a,b,c})==3 and g.has_edge(c,a):
                ring=(a,b,c);start=ring.index(min(ring));date_3cycles.add(ring[start:]+ring[:start])

result={
 'metadata':{'generated_at_utc':datetime.now(timezone.utc).isoformat(),'input_sha256':input_hashes(root/'data'),'configuration':cfg,'python_version':sys.version.split()[0],'pandas_version':pd.__version__,'networkx_version':nx.__version__,'method':{'seed_paths':'Directed paths of at most max_depth edges; dynamic programming retaining earliest achievable date per node and path length. Dates nondecreasing, same-day allowed, no amount conservation, no maximum edge lag. This is compatibility, not attribution.','temporal_0_2d':'Production FIFO matched incoming volume within zero to two calendar days.','temporal_1_2d':'Same FIFO algorithm, matching prior days only. Excludes same-day ambiguity; does not establish source of funds.','counts':'Source frames are validated by moneygraph.io.load, including exact integer-cent reconciliation. No transaction deduplication.'}},
 'summary':{'n_nodes':len(nodes),'n_edges':len(edges),'n_tx':len(tx),'date_from':str(tx.date.min().date()),'date_to':str(tx.date.max().date()),'observed_days':int(tx.date.nunique()),'dates_all_midnight':bool((tx.date.dt.hour==0).all()),'total_kzt':int(tx._cents.sum())/100,'depth_counts':nodes.depth.value_counts().sort_index().to_dict(),'edge_depth_counts':edges.depth.value_counts().sort_index().to_dict(),'self_loops':nx.number_of_selfloops(g),'reciprocal_pairs':round_trip,'directed_3cycles':len(date_3cycles),'nontrivial_scc_sizes':sorted(map(len,scc),reverse=True),'weak_component_sizes':component_sizes,'duplicate_tx_rows_all_fields':int(tx.duplicated(['src','dst','date','sum_kzt']).sum()),'single_tx_edges':int((edges.n_tx==1).sum()),'single_day_edges':int((daily_edges.n_days==1).sum()),'single_tx_nodes':int(((f.in_tx+f.out_tx)==1).sum()),'one_active_day_nodes':int((f.active_days==1).sum()),'no_outgoing_nodes':int((f.out_deg==0).sum()),'depth4_no_outgoing_nodes':int(f.truncated_by_depth.sum()),'nonseed_nonboundary_no_outgoing_nodes':int(((f.out_deg==0)&~f.is_seed&~f.truncated_by_depth).sum()),'seed_no_outgoing':int(((f.out_deg==0)&f.is_seed).sum()),'seed_no_incoming':int(((f.in_deg==0)&f.is_seed).sum()),'observed_out_gt_in':int((f.out_kzt>f.in_kzt).sum()),'nonseed_out_gt_in':int(((f.out_kzt>f.in_kzt)&~f.is_seed).sum()),'gid_above_js_safeint':int((nodes.gid>2**53).sum()),'clusters_multi_seed':int((clusters.n_seed>1).sum())},
 'distributions':{'tx_kzt':quant(tx.sum_kzt),'edge_kzt':quant(edges.sum_kzt),'edge_n_tx':quant(edges.n_tx),'in_degree':quant(f.in_deg),'out_degree':quant(f.out_deg),'active_days':quant(f.active_days),'seed_reach':quant(f.seed_reach),'temporal_seed_reach':quant(f.temporal_seed_reach)},
 'seed_path_validity':{'total_structural_seed_node_pairs':int(f.seed_reach.sum()),'total_date_compatible_seed_node_pairs':int(f.temporal_seed_reach.sum()),'nodes_losing_some_seed_paths':int((f.seed_path_gap>0).sum()),'nodes_with_structural_but_no_date_compatible_seed_paths':int(((f.seed_reach>0)&(f.temporal_seed_reach==0)).sum()),'nodes_structural_reach_at_least5':int((f.seed_reach>=5).sum()),'nodes_temporal_reach_at_least5':int((f.temporal_seed_reach>=5).sum()),'top20_seed_path_gap':records(top20[cols]),'examples_big_gap':records(f.sort_values(['seed_path_gap','priority_score'],ascending=False).head(5)[cols])},
 'transit':{'ratio_candidates':len(transit_candidates),'classified_transit':int((f.role=='transit').sum()),'ratio_candidates_with_zero_0_2d_compatibility':int((transit_candidates.temporal_2d==0).sum()),'ratio_candidates_below_20pct_0_2d':int((transit_candidates.temporal_2d<.2).sum()),'ratio_candidates_at_least80pct_0_2d':int((transit_candidates.temporal_2d>=.8).sum()),'ratio_candidates_at_least80pct_1_2d':int((transit_candidates.temporal_1_2d>=.8).sum()),'weak_time_examples':records(transit_candidates.sort_values(['temporal_2d','in_kzt'],ascending=[True,False]).head(5)[cols]),'strong_time_examples':records(transit_candidates.sort_values(['temporal_2d','in_kzt'],ascending=False).head(5)[cols]),'same_day_dependency_examples':records(transit_candidates.sort_values(['timing_depends_same_day','in_kzt'],ascending=False).head(5)[cols])},
 'terminal':{'count':int((f.role=='terminal').sum()),'one_tx':int(((f.role=='terminal')&(f.in_tx==1)).sum()),'one_day':int(((f.role=='terminal')&(f.active_days==1)).sum()),'one_payer':int(((f.role=='terminal')&(f.in_deg==1)).sum())},
 'boundary_examples':records(f[f.truncated_by_depth].sort_values(['in_kzt','in_deg'],ascending=False).head(5)[cols]),
 'consolidator_examples':records(f[f.matched_roles.map(lambda x:'consolidator' in x)].sort_values(['in_deg','in_kzt'],ascending=False).head(8)[cols]),
 'top_distributor_examples':records(f.sort_values(['out_deg','out_kzt'],ascending=False).head(5)[cols]),
 'edge_volume_concentration':{str(n):float(edges.nlargest(n,'sum_kzt').sum_kzt.sum()/edges.sum_kzt.sum()) for n in [1,10,20,50,100]},
 'notes':['Date-compatible paths permit same-day transfers and ignore amount conservation, so are optimistic compatibility, not attribution.','Equal rows are not deduplicated: absent transaction ids can represent repeated legitimate transfers.','FIFO money compatibility neither identifies provenance nor reconstructs account balance.']}

result['summary'].update({
 'observed_out_gt_in_positive_in':int(((f.out_kzt>f.in_kzt)&(f.in_kzt>0)).sum()),
 'observed_out_gt_in_zero_in':int(((f.out_kzt>f.in_kzt)&(f.in_kzt==0)).sum()),
 'out_gt_in_zero_in_all_seed':bool(f.loc[(f.out_kzt>f.in_kzt)&(f.in_kzt==0),'is_seed'].all()),
 'observed_out_gt_in_integer_cents_crosscheck':sum(g.out_degree(x,weight='cents')>g.in_degree(x,weight='cents') for x in g),
 'all_ratio_08_12_candidates_including_seed':int(f.pass_through.between(.8,1.2).sum()),
 'top20_role_counts':top20.role.value_counts().to_dict(),
 'top20_component_counts':top20.component_id.value_counts().to_dict(),
 'top50_component_counts':f[f.gid.isin(top.gid)].component_id.value_counts().to_dict(),
 'largest_components':records(f.groupby('component_id').agg(nodes=('gid','size'),seeds=('is_seed','sum'),max_priority=('priority_score','max')).sort_values('nodes',ascending=False).head(3).reset_index()),
})

# Check the dynamic program independently against explicit simple path enumeration
# on algorithmically selected high-gap and high-priority examples (no hardcoded gid).
date_by_edge=defaultdict(list)
for r in tx.itertuples(): date_by_edge[(int(r.src),int(r.dst))].append(r.date.toordinal())
check_targets=set(f.nlargest(5,'seed_path_gap').gid).union(top.head(5).gid)
for target in check_targets:
    brute_seeds=set()
    for seed in seeds:
        if seed == target:continue
        for path in nx.all_simple_paths(g,seed,int(target),cutoff=cfg['max_depth']):
            last=0
            for a,b in zip(path,path[1:]):
                choices=[d for d in date_by_edge[(a,b)] if d>=last]
                if not choices:break
                last=min(choices)
            else:
                brute_seeds.add(seed)
                break
    assert brute_seeds == temporal_seeds[target], ('Date-path crosscheck failed',target)
assert (f.temporal_seed_reach<=f.seed_reach).all()
assert (f.temporal_1_2d<=f.temporal_2d+1e-12).all()
result['metadata']['verification']={'explicit_simple_path_targets_checked':len(check_targets),'date_reach_not_above_structural_reach':True,'prior_day_fifo_not_above_including_same_day_fifo':True}

# Provide local transaction timelines for selected demonstration cases.
demo_gids=set(int(r['gid']) for key in ['boundary_examples','consolidator_examples'] for r in result[key][:2])
demo_gids.update(int(r['gid']) for key in ['weak_time_examples','strong_time_examples','same_day_dependency_examples'] for r in result['transit'][key][:2])
demo_gids.update(int(r['gid']) for r in result['seed_path_validity']['examples_big_gap'][:2])
result['demo_timelines']={str(gid):[{'date':pd.Timestamp.fromordinal(day).date().isoformat(),'incoming_kzt':vals[0]/100,'outgoing_kzt':vals[1]/100} for day,vals in sorted(daily[gid].items())] for gid in sorted(demo_gids)}
out=root/'artifacts'/'research';out.mkdir(parents=True,exist_ok=True)
(out/'data_signals.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
print(json.dumps({'output':str(out/'data_signals.json'),'summary':result['summary'],'transit':{k:v for k,v in result['transit'].items() if not isinstance(v,list)},'seed_paths':{k:v for k,v in result['seed_path_validity'].items() if not isinstance(v,list)},'verification':result['metadata']['verification']},ensure_ascii=False,indent=2,allow_nan=False))
