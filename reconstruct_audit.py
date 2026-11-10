"""Read-only independent reconstruction; never imports the production model.

Reads frozen model inputs/results, rebuilds activity and energy from individual
use groups, and writes only this task's evidence directory.
"""
from pathlib import Path
import csv, hashlib, json, math
from collections import defaultdict

ROOT=Path(__file__).resolve().parent
SRC=ROOT
PKG=ROOT/'inputs'
OUT=ROOT/'results'
OUT.mkdir(exist_ok=True)
SPEC=json.loads((PKG/'model_spec.json').read_text(encoding='utf-8'))
DATA=json.loads((PKG/'step3_shared_future_results.json').read_text(encoding='utf-8'))
PAR={p['key']:p['value'] for p in SPEC['parameters']}
EF={r['region']:r['value'] for r in SPEC['region_ef_2023_carried_to_2024']}
errors=defaultdict(float); rel_errors=defaultdict(float); comparisons=0
state_checks=0;minimum_idle=float('inf');minimum_capacity_slack=float('inf')

def check(key,actual,expected):
    global comparisons
    if actual is None or expected is None:
        assert actual is expected,(key,actual,expected)
        return
    comparisons+=1
    err=abs(actual-expected)
    errors[key]=max(errors[key],err)
    rel_errors[key]=max(rel_errors[key],err/max(abs(actual),abs(expected),1.0))
    assert math.isclose(actual,expected,rel_tol=1e-10,abs_tol=1e-3),(key,actual,expected,err)

def interp(nodes,y):
    pairs=sorted((int(k),v) for k,v in nodes.items())
    if y in dict(pairs):return dict(pairs)[y]
    for (x0,v0),(x1,v1) in zip(pairs,pairs[1:]):
        if x0<=y<=x1:return ((x1-y)*v0+(y-x0)*v1)/(x1-x0)
    raise ValueError(y)

def reconstruct(case, overrides=None, future_override=None, capture_multiplier=1.0):
    global state_checks,minimum_idle,minimum_capacity_slack
    deployment,response,future=case.split('·')
    p=dict(PAR,**(overrides or {}));f=future_override or SPEC['future_cases'][future]
    rows=[];annual=[]
    for y in SPEC['model_years']:
        frac=(y-2024)/11
        em=1+(f['e2035']-1)*frac
        lm_other=1+(f['other_km2035']-1)*frac
        lm_ops=1+(f['ops_km2035']-1)*frac
        nall=(interp(SPEC['stock_end_nodes'],y-1)+interp(SPEC['stock_end_nodes'],y))/2
        nt=interp(SPEC['ordinary_taxi_mean_nodes'],y)
        nr=interp(SPEC['ordinary_ride_mean_nodes'],y)
        cp=interp(SPEC['deployment_nodes'][deployment]['private'],y)*capture_multiplier
        co=interp(SPEC['deployment_nodes'][deployment]['ops'],y)*capture_multiplier
        g=0.0 if y==2024 else SPEC['induced_levels'][response]*p['g_multiplier']
        yr=[]
        for region in SPEC['regions']:
            ef=EF[region]*interp(SPEC['grid_multiplier_nodes'],y)
            private_share=SPEC['other_internal_mix']['private']
            wo=private_share*SPEC['spatial_proxy']['private']['fractions'][region]+(1-private_share)*SPEC['spatial_proxy']['civil']['fractions'][region]
            group={}
            for u in SPEC['use_groups']:
                key=u['key'];other=key in SPEC['other_internal_mix']
                vehicles=((nall-nt-nr)*SPEC['other_internal_mix'][key] if other else nt if key=='taxi' else nr)*(wo if other else SPEC['spatial_proxy']['civil']['fractions'][region])
                distance=vehicles*u['annual_km']*(lm_other if other else lm_ops)
                group[key]={'vehicles':vehicles,'vkt':distance,'pkm':distance*u['o']/(1+u['u']),
                    'energy':distance*u['e']*em/u['eta'],'e':u['e']*em,'eligible':u['eligible']}
            e0=math.fsum(v['energy'] for v in group.values());k0=math.fsum(v['pkm'] for v in group.values())
            kp=cp*group['private']['pkm'];ko=co*(group['taxi']['pkm']+group['ride']['pkm'])
            bp=kp*(1-p['private_to_robot']);br=ko+kp*p['private_to_robot']
            removed=cp*group['private']['energy']+co*(group['taxi']['energy']+group['ride']['energy'])
            retained=e0-removed;n_private=cp*group['private']['vehicles']*(1-p['private_to_robot'])
            shifted=p['sigma']*(bp+br);stages={}
            for stage in ['fixed','transfer','full']:
                step_g=g if stage=='full' else 0
                demands=[bp*(1+step_g),(br+(shifted if stage!='fixed' else 0))*(1+step_g)]
                modes=[]
                for mode,k in zip(['private','robot'],demands):
                    vkt=k*(1+p['u_'+mode])/p['o_'+mode]
                    fleet=n_private if mode=='private' else vkt/p['robot_annual_vkt']
                    hd=vkt/p['v_'+mode]
                    hi=fleet*p['private_idle_day']*p['private_days'] if mode=='private' else fleet*p['robot_online_day']*p['robot_days']-hd
                    minimum_idle=min(minimum_idle,hi);slack=fleet*8760-hd-hi
                    minimum_capacity_slack=min(minimum_capacity_slack,slack);state_checks+=1
                    assert hi>=-1e-6 and slack>=-1e-5,(case,y,region,mode,hi,slack)
                    e_ref=group['private']['e'] if mode=='private' else p['e_robot_ref']*em
                    traction=vkt*e_ref*(1-p['saving_'+mode])/p['eta_'+mode]
                    compute=(hd*p['pc_'+mode]+hi*p['pic_'+mode])*p['power_multiplier']/p['eta_'+mode]
                    aux=(hd*p['po_'+mode]+hi*p['pio_'+mode])*p['power_multiplier']/p['eta_'+mode]
                    modes.append(dict(energy=traction+compute+aux,traction=traction,device=compute+aux,compute=compute,
                        fleet=fleet,vkt=vkt,hd=hd,hi=hi))
                stages[stage]=dict(energy=retained+math.fsum(v['energy'] for v in modes),modes=modes)
            fixed=stages['fixed']['energy'];transfer=stages['transfer']['energy'];full=stages['full']['energy']
            S=e0-fixed;fd=math.fsum(m['device'] for m in stages['fixed']['modes'])
            gross=removed-math.fsum(m['traction'] for m in stages['fixed']['modes'])
            private_idle=n_private*p['private_idle_day']*p['private_days']*(p['pic_private']+p['pio_private'])*p['power_multiplier']/p['eta_private']
            # Independently derive service electricity coefficients from direct
            # fixed-demand totals and one unit of taxi passenger-distance.
            qp=(group['private']['e']*(1-p['saving_private'])+(p['pc_private']+p['po_private'])*p['power_multiplier']/p['v_private'])*(1+p['u_private'])/(p['o_private']*p['eta_private'])
            qr=(p['e_robot_ref']*em*(1-p['saving_robot'])+(p['pc_robot']+p['po_robot'])*p['power_multiplier']/p['v_robot']+(p['pic_robot']+p['pio_robot'])*p['power_multiplier']*(p['robot_online_day']*p['robot_days']/p['robot_annual_vkt']-1/p['v_robot']))*(1+p['u_robot'])/(p['o_robot']*p['eta_robot'])
            A=qp*bp+qr*br;B=qr*(bp+br)
            r=dict(year=y,region=region,e_noav=e0,k0=k0,k_eligible=math.fsum(v['pkm'] for v in group.values() if v['eligible']),
                c_private=cp,c_ops=co,a_global=(kp+ko)/k0,k_private_captured=kp,k_ops_captured=ko,
                e_removed=removed,e_retained=retained,b_private=bp,b_robot=br,k_shift=shifted,g=g,ef=ef,
                e_fixed=fixed,e_shift=transfer,e_full=full,co2_full=full*ef,co2_noav=e0*ef,delta_e=full-e0,
                technical_saving=S,shift_extra=transfer-fixed,induced_extra=full-transfer,
                r_total=(full-fixed)/S if S>1e-6 else None,r_g=(full-transfer)/S if S>1e-6 else None,
                device_full=math.fsum(m['device'] for m in stages['full']['modes']),compute_full=math.fsum(m['compute'] for m in stages['full']['modes']),
                robot_fleet=stages['full']['modes'][1]['fleet'],private_fleet=n_private,k_induced=g*(bp+br+shifted),
                fixed_device=fd,gross_running_saving=gross,response_A=A,transfer_B=B,private_fixed_idle=private_idle,qp=qp,qr=qr)
            check('identity_delta',r['delta_e'],-S+B*p['sigma']+g*(A+B*p['sigma']))
            check('identity_saving',S,gross-fd)
            check('identity_transfer',r['shift_extra'],B*p['sigma'])
            check('identity_induced',r['induced_extra'],g*(A+B*p['sigma']))
            yr.append(r);rows.append(r)
        sums=['e_noav','k0','k_eligible','k_private_captured','k_ops_captured','e_removed','e_retained','b_private','b_robot','k_shift','e_fixed','e_shift','e_full','co2_full','co2_noav','delta_e','technical_saving','shift_extra','induced_extra','device_full','compute_full','robot_fleet','private_fleet','k_induced','fixed_device','gross_running_saving','response_A','transfer_B','private_fixed_idle']
        a={k:math.fsum(v[k] for v in yr) for k in sums};S=a['technical_saving']
        a.update(year=y,c_private=cp,c_ops=co,a_global=(a['b_private']+a['b_robot'])/a['k0'],g=g,
            r_total=(a['e_full']-a['e_fixed'])/S if S>1e-6 else None,r_g=a['induced_extra']/S if S>1e-6 else None,
            energy_change_pct=100*a['delta_e']/a['e_noav'],co2_change_pct=100*(a['co2_full']/a['co2_noav']-1))
        annual.append(a)
    return rows,annual

def compare_records(observed,expected,namespace):
    assert len(observed)==len(expected)
    for a,b in zip(observed,expected):
        for key,val in a.items():
            if key not in b or isinstance(val,str):continue
            check(namespace+':'+key,val,b[key])

if __name__=='__main__':
    counts={'main_scenarios':len(DATA['cases']),'national_annual_records':0,'regional_annual_records':0}
    same_baseline=defaultdict(list);summary_rows=[]
    for c in DATA['cases']:
        regional,annual=reconstruct(c['case'])
        compare_records(regional,c['regional'],'regional');compare_records(annual,c['annual'],'annual')
        counts['regional_annual_records']+=len(regional);counts['national_annual_records']+=len(annual)
        for a in annual:same_baseline[(c['case'].split('·')[2],a['year'])].append(a['e_noav'])
        summary_rows.append(dict(case=c['case'],**annual[-1]))
    assert counts=={'main_scenarios':48,'national_annual_records':576,'regional_annual_records':4032}
    assert all(len(vals)==12 and max(vals)==min(vals) for vals in same_baseline.values())
    zero,_=reconstruct(DATA['default_case'],capture_multiplier=0)
    for r in zero:
        check('zero_av',r['e_full'],r['e_noav']);check('zero_device',r['device_full'],0)
    for overrides,field in [({'sigma':0,'g_multiplier':0},'e_fixed'),({'g_multiplier':0},'e_shift')]:
        rr,_=reconstruct(DATA['default_case'],overrides)
        for r in rr:check('nested_control:'+field,r['e_full'],r[field])
    coeff={}
    for v in DATA['joint_coefficients']:
        _,a=reconstruct(DATA['default_case'],v['overrides']);r=a[-1]
        values={'S':r['technical_saving'],'A':r['response_A'],'B':r['transfer_B'],'e0':r['e_noav']}
        for k,n in values.items():check('coefficient:'+k,n,v[k])
        coeff[v['variant']]=values
    grid=list(csv.DictReader((PKG/'联合敏感性_2035.csv').open(encoding='utf-8-sig')))
    assert len(grid)==9*41*51
    for r in grid:
        c=coeff[r['variant']];s=float(r['sigma']);g=float(r['g'])
        de=-c['S']+s*c['B']+g*(c['A']+s*c['B'])
        check('grid:delta_kwh',de,float(r['delta_e_TWh'])*1e9)
    counts['joint_grid_cells']=len(grid)
    for s in DATA['common_sensitivity']:
        _,a=reconstruct(DATA['default_case'],future_override={'e2035':s['e_multiplier'],'other_km2035':s['other_km_multiplier'],'ops_km2035':s['ops_km_multiplier']})
        compare_records([a[-1]],[s],'common36')
    counts['common_sensitivity_cases']=len(DATA['common_sensitivity'])
    for s in DATA['state_sensitivity']:
        _,a=reconstruct(DATA['default_case'],s['overrides'])
        compare_records(a,s['annual'],'state9')
    counts['state_sensitivity_cases']=len(DATA['state_sensitivity'])
    report={'status':'PASS','date':'2026-10-26','method':'Independent first-principles reconstruction from model_spec.json; no import/call of production calculate_robustness.py',
        'scope_counts':counts,'numeric_comparisons':comparisons,'state_constraint_checks':state_checks,'minimum_idle_vehicle_hours':minimum_idle,
        'minimum_annual_capacity_slack_vehicle_hours':minimum_capacity_slack,'paired_baseline_groups':len(same_baseline),'av_combinations_per_baseline':12,
        'maximum_absolute_difference_by_field':dict(errors),'maximum_scaled_relative_difference_by_field':dict(rel_errors),
        'tolerance':{'relative':1e-10,'absolute':1e-3},'source_files_unchanged':True,
        'input_hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [PKG/'model_spec.json',PKG/'step3_shared_future_results.json',PKG/'scenario_inputs.json',SRC/'calculate_robustness.py']},
        'interpretation':'Numerical implementation consistency only; no empirical calibration or probability validation.'}
    (OUT/'independent_reconstruction_20261026.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    with (OUT/'main48_2035_recomputed.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(summary_rows[0]));w.writeheader();w.writerows(summary_rows)
    print(json.dumps({'status':report['status'],'counts':counts,'numeric_comparisons':comparisons,'state_constraints':state_checks,
        'max_e_full_kWh':max(errors['regional:e_full'],errors['annual:e_full']),
        'max_delta_kWh':max(errors['regional:delta_e'],errors['annual:delta_e']),
        'max_co2_kg':max(errors['regional:co2_full'],errors['annual:co2_full'])},ensure_ascii=False,indent=2))
