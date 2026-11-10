"""Separated AV deployment, exact response thresholds and shared BEV futures."""
import copy
import csv
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PACKAGE = ROOT / 'package'
PACKAGE.mkdir(parents=True, exist_ok=True)
OLD = json.loads((ROOT.parent/'round10/package/av_model_spec.json').read_text(encoding='utf-8'))
OLD_RESULTS = json.loads((ROOT.parent/'round10/package/av_calculated_results.json').read_text(encoding='utf-8'))
YEARS = OLD['model_years']
REGIONS = OLD['regions']
PARAMS = {p['key']: p['value'] for p in OLD['parameters']}
DEPLOYMENT = {
    'Robotaxi先行': {'private': {2024:0., 2026:0., 2030:.01, 2035:.10}, 'ops': {2024:0., 2026:.005, 2030:.10, 2035:.40}},
    '同步推广': {'private': {2024:0., 2026:.001, 2030:.03, 2035:.15}, 'ops': {2024:0., 2026:.005, 2030:.10, 2035:.40}},
    '私人延后': {'private': {2024:0., 2026:0., 2030:0., 2035:.03}, 'ops': {2024:0., 2026:.005, 2030:.10, 2035:.40}},
    '仅Robotaxi': {'private': {2024:0., 2026:0., 2030:0., 2035:0.}, 'ops': {2024:0., 2026:.005, 2030:.10, 2035:.40}},
}
FUTURES = {
    '维持电耗里程': {'e2035':1., 'other_km2035':1., 'ops_km2035':1.},
    '温和电耗改进': {'e2035':.85, 'other_km2035':1., 'ops_km2035':1.},
    '改进且低里程': {'e2035':.75, 'other_km2035':.85, 'ops_km2035':.90},
    '改进且高里程': {'e2035':.85, 'other_km2035':1.15, 'ops_km2035':1.10},
}
RESPONSES = OLD['induced_levels']
DEFAULT = 'Robotaxi先行·中诱发·温和电耗改进'
CASES = [f'{d}·{g}·{f}' for d in DEPLOYMENT for g in RESPONSES for f in FUTURES]
VARIANTS = [
    ('中心技术与运行', {}),
    ('新增功率减半', {'power_multiplier':.5}),
    ('新增功率加倍', {'power_multiplier':2.}),
    ('运行改善5%', {'saving_private':.05, 'saving_robot':.05}),
    ('运行改善25%', {'saving_private':.25, 'saving_robot':.25}),
    ('私人空驶比0.1', {'u_private':.10}),
    ('私人空驶比0.3', {'u_private':.30}),
    ('Robotaxi空驶比0.5', {'u_robot':.50}),
    ('Robotaxi年里程6万', {'robot_annual_vkt':60000.}),
]

# Persist the authoritative numerical scenario inputs, separate from saved results.
INPUT_FILE=PACKAGE/'scenario_inputs.json'
if not INPUT_FILE.exists():
    initial=dict(default_case=DEFAULT,deployment_nodes=DEPLOYMENT,future_cases=FUTURES,
        induced_levels=RESPONSES,parameters=PARAMS,stock_end_nodes=OLD['stock_end_nodes'],
        ordinary_taxi_mean_nodes=OLD['ordinary_taxi_mean_nodes'],ordinary_ride_mean_nodes=OLD['ordinary_ride_mean_nodes'],
        grid_multiplier_nodes=OLD['grid_multiplier_nodes'])
    INPUT_FILE.write_text(json.dumps(initial,ensure_ascii=False,indent=2),encoding='utf-8')
INPUTS=json.loads(INPUT_FILE.read_text(encoding='utf-8'))
DEFAULT=INPUTS['default_case'];DEPLOYMENT=INPUTS['deployment_nodes'];FUTURES=INPUTS['future_cases']
RESPONSES=INPUTS['induced_levels'];PARAMS=INPUTS['parameters']
for key in ['stock_end_nodes','ordinary_taxi_mean_nodes','ordinary_ride_mean_nodes','grid_multiplier_nodes']:
    OLD[key]=INPUTS[key]
CASES=[f'{d}·{g}·{f}' for d in DEPLOYMENT for g in RESPONSES for f in FUTURES]
assert DEFAULT in CASES

def interpolate(nodes, year):
    nodes = {int(k):v for k,v in nodes.items()}
    if year in nodes:
        return nodes[year]
    for left, right in zip(sorted(nodes), sorted(nodes)[1:]):
        if left < year < right:
            return nodes[left]+(nodes[right]-nodes[left])*(year-left)/(right-left)
    raise ValueError(f'Year {year} outside supplied nodes')

def future_inputs(future):
    fv = FUTURES[future]
    series=[]
    for year in YEARS:
        n=(interpolate(OLD['stock_end_nodes'],year-1)+interpolate(OLD['stock_end_nodes'],year))/2
        nt=interpolate(OLD['ordinary_taxi_mean_nodes'],year)
        nr=interpolate(OLD['ordinary_ride_mean_nodes'],year)
        r=dict(year=year, mean_national_stock=n, mean_taxi=nt, mean_ride=nr, mean_other=n-nt-nr,
               grid_multiplier=interpolate(OLD['grid_multiplier_nodes'],year))
        for key,target in [('e_multiplier','e2035'),('other_km_multiplier','other_km2035'),('ops_km_multiplier','ops_km2035')]:
            r[key]=interpolate({2024:1.,2035:fv[target]},year)
        series.append(r)
    return series

def validate(p):
    assert p['sigma']>=0 and p['g_multiplier']>=0
    assert 0<=p['private_to_robot']<=1
    assert all(0<=p[k]<=1 for k in ['saving_private','saving_robot'])
    assert all(p[k]>0 for k in ['v_private','v_robot','robot_annual_vkt','eta_private','eta_robot'])
    assert p['eta_private']<=1 and p['eta_robot']<=1
    assert all(p[k]>=0 for k in ['u_private','u_robot','pc_private','po_private','pc_robot','po_robot','pic_private','pio_private','pic_robot','pio_robot','power_multiplier'])
    assert p['o_private']>=1 and p['o_robot']>=1
    assert 0<=p['private_idle_day']*p['private_days']<=8760
    assert p['robot_annual_vkt']/p['v_robot']<=p['robot_online_day']*p['robot_days']<=8760

def calculate(case=DEFAULT, overrides=None, capture_multiplier=1.):
    deployment,response,future=case.split('·')
    p=PARAMS.copy();p.update(overrides or {});validate(p)
    annual=[];rows=[];base_rows=[];activity=[]
    for f in future_inputs(future):
        y=f['year']; cp=interpolate(DEPLOYMENT[deployment]['private'],y)*capture_multiplier
        co=interpolate(DEPLOYMENT[deployment]['ops'],y)*capture_multiplier
        assert 0<=cp<=1 and 0<=co<=1
        g=0. if y==2024 else RESPONSES[response]*p['g_multiplier']
        for region in REGIONS:
            mix=OLD['other_internal_mix']['private']
            fo=mix*OLD['spatial_proxy']['private']['fractions'][region]+(1-mix)*OLD['spatial_proxy']['civil']['fractions'][region]
            ef=next(x['value'] for x in OLD['region_ef_2023_carried_to_2024'] if x['region']==region)*f['grid_multiplier']
            br={}
            for group in OLD['use_groups']:
                key=group['key']; other=key in OLD['other_internal_mix']
                national=f['mean_other']*OLD['other_internal_mix'][key] if other else f['mean_taxi' if key=='taxi' else 'mean_ride']
                fraction=fo if other else OLD['spatial_proxy']['civil']['fractions'][region]
                n=national*fraction
                l=group['annual_km']*f['other_km_multiplier' if other else 'ops_km_multiplier']
                e=group['e']*f['e_multiplier'];d=n*l;k=d/(1+group['u'])*group['o'];energy=d*e/group['eta']
                r=dict(year=y,region=region,use_group=key,national_use_vehicles=national,region_fraction=fraction,n_vehicles=n,
                       annual_km=l,vehicle_km=d,u=group['u'],o=group['o'],person_km=k,e_battery=e,eta=group['eta'],
                       grid_kwh=energy,ef=ef,co2_kg=energy*ef,eligible=group['eligible'])
                br[key]=r;base_rows.append(r)
            e0=sum(z['grid_kwh'] for z in br.values());k0=sum(z['person_km'] for z in br.values())
            eligible=sum(z['person_km'] for z in br.values() if z['eligible'])
            kp=cp*br['private']['person_km'];ko=co*(br['taxi']['person_km']+br['ride']['person_km'])
            removed=cp*br['private']['grid_kwh']+co*(br['taxi']['grid_kwh']+br['ride']['grid_kwh'])
            retained=e0-removed
            bp=kp*(1-p['private_to_robot']);bot=ko+kp*p['private_to_robot'];transfer=p['sigma']*(bp+bot)
            np=cp*br['private']['n_vehicles']*(1-p['private_to_robot'])
            local=[]
            for stage in ['固定需求','仅方式转入','完整响应']:
                for mode in ['私人AV','Robotaxi']:
                    private=mode=='私人AV';b=bp if private else bot
                    ks=transfer if not private and stage!='固定需求' else 0.
                    gs=g if stage=='完整响应' else 0.
                    k=(b+ks)*(1+gs);u=p['u_private' if private else 'u_robot'];o=p['o_private' if private else 'o_robot']
                    d=k*(1+u)/o;n=np if private else d/p['robot_annual_vkt'];v=p['v_private' if private else 'v_robot']
                    hd=d/v;hi=n*p['private_idle_day']*p['private_days'] if private else n*p['robot_online_day']*p['robot_days']-hd
                    assert hi>=-1e-6 and hd+hi<=n*8760+1e-6
                    eta=p['eta_private' if private else 'eta_robot']
                    eref=br['private']['e_battery'] if private else p['e_robot_ref']*f['e_multiplier']
                    ed=eref*(1-p['saving_private' if private else 'saving_robot'])
                    pc=p['pc_private' if private else 'pc_robot']*p['power_multiplier']
                    po=p['po_private' if private else 'po_robot']*p['power_multiplier']
                    pic=p['pic_private' if private else 'pic_robot']*p['power_multiplier']
                    pio=p['pio_private' if private else 'pio_robot']*p['power_multiplier']
                    comp=(hd*pc+hi*pic)/eta;other=(hd*po+hi*pio)/eta;traction=d*ed/eta
                    z=dict(year=y,region=region,stage=stage,mode=mode,base_person_km=b,shift_person_km=ks,g=gs,person_km=k,u=u,o=o,
                           vehicle_km=d,fleet_vehicles=n,v=v,drive_vehicle_hours=hd,idle_vehicle_hours=hi,pc=pc,po=po,pic=pic,pio=pio,
                           compute_grid_kwh=comp,other_device_grid_kwh=other,e_drive_battery=ed,eta=eta,drive_grid_kwh=traction,
                           grid_kwh=traction+comp+other,ef=ef,co2_kg=(traction+comp+other)*ef)
                    local.append(z);activity.append(z)
            energies={s:retained+sum(z['grid_kwh'] for z in local if z['stage']==s) for s in ['固定需求','仅方式转入','完整响应']}
            fixed=energies['固定需求'];shift=energies['仅方式转入'];full=energies['完整响应'];saving=e0-fixed
            complete=[z for z in local if z['stage']=='完整响应'];fixed_details=[z for z in local if z['stage']=='固定需求']
            fd=sum(z['compute_grid_kwh']+z['other_device_grid_kwh'] for z in fixed_details)
            gross=removed-sum(z['drive_grid_kwh'] for z in fixed_details)
            qp=(1+p['u_private'])/p['o_private']/p['eta_private']*(br['private']['e_battery']*(1-p['saving_private'])+(p['pc_private']+p['po_private'])*p['power_multiplier']/p['v_private'])
            qr=(1+p['u_robot'])/p['o_robot']/p['eta_robot']*(p['e_robot_ref']*f['e_multiplier']*(1-p['saving_robot'])+(p['pc_robot']+p['po_robot'])*p['power_multiplier']/p['v_robot']+(p['pic_robot']+p['pio_robot'])*p['power_multiplier']*(p['robot_online_day']*p['robot_days']/p['robot_annual_vkt']-1/p['v_robot']))
            acoef=qp*bp+qr*bot;bcoef=qr*(bp+bot)
            idle_private=np*p['private_idle_day']*p['private_days']*(p['pic_private']+p['pio_private'])*p['power_multiplier']/p['eta_private']
            assert math.isclose(acoef+idle_private, fixed-retained,rel_tol=1e-11,abs_tol=.0001)
            r=dict(year=y,region=region,e_noav=e0,k0=k0,k_eligible=eligible,c_private=cp,c_ops=co,a_global=(kp+ko)/k0,
                   a_eligible=(kp+ko)/eligible,k_private_captured=kp,k_ops_captured=ko,e_removed=removed,e_retained=retained,
                   b_private=bp,b_robot=bot,k_shift=transfer,g=g,ef=ef,e_fixed=fixed,e_shift=shift,e_full=full,
                   co2_full=full*ef,co2_noav=e0*ef,delta_e=full-e0,technical_saving=saving,
                   shift_extra=shift-fixed,induced_extra=full-shift,r_total=(full-fixed)/saving if saving>1e-6 else None,
                   r_g=(full-shift)/saving if saving>1e-6 else None,device_full=sum(z['compute_grid_kwh']+z['other_device_grid_kwh'] for z in complete),
                   compute_full=sum(z['compute_grid_kwh'] for z in complete),robot_fleet=next(z['fleet_vehicles'] for z in complete if z['mode']=='Robotaxi'),
                   private_fleet=np,k_induced=g*(bp+bot+transfer),fixed_device=fd,gross_running_saving=gross,
                   response_A=acoef,transfer_B=bcoef,private_fixed_idle=idle_private,qp=qp,qr=qr)
            assert math.isclose(r['delta_e'],-saving+r['shift_extra']+r['induced_extra'],rel_tol=1e-10,abs_tol=.001)
            assert math.isclose(r['delta_e'],-saving+bcoef*p['sigma']+g*(acoef+bcoef*p['sigma']),rel_tol=1e-10,abs_tol=.001)
            assert math.isclose(saving,gross-fd,rel_tol=1e-10,abs_tol=.001)
            rows.append(r)
        yr=[z for z in rows if z['year']==y]
        additive=['e_noav','k0','k_eligible','k_private_captured','k_ops_captured','e_removed','e_retained','b_private','b_robot','k_shift','e_fixed','e_shift','e_full','co2_full','co2_noav','delta_e','technical_saving','shift_extra','induced_extra','device_full','compute_full','robot_fleet','private_fleet','k_induced','fixed_device','gross_running_saving','response_A','transfer_B','private_fixed_idle']
        t={k:sum(z[k] for z in yr) for k in additive};s=t['technical_saving']
        t.update(year=y,c_private=cp,c_ops=co,a_global=(t['b_private']+t['b_robot'])/t['k0'],g=g,
                 r_total=(t['e_full']-t['e_fixed'])/s if s>1e-6 else None,r_g=t['induced_extra']/s if s>1e-6 else None,
                 energy_change_pct=100*t['delta_e']/t['e_noav'],co2_change_pct=100*(t['co2_full']/t['co2_noav']-1))
        annual.append(t)
    return dict(case=case,annual=annual,regional=rows,baseline_detail=base_rows,activity_detail=activity)

def coefficients(case, overrides=None, year=2035):
    r=next(x for x in calculate(case,overrides)['annual'] if x['year']==year)
    return dict(case=case,year=year,S=r['technical_saving'],A=r['response_A'],B=r['transfer_B'],e0=r['e_noav'],a=r['a_global'])

def delta(coeff,sigma,g):
    return -coeff['S']+coeff['B']*sigma+g*(coeff['A']+coeff['B']*sigma)

def write_json(file,data):
    (PACKAGE/file).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')

def csv_rows(file,rows):
    with (PACKAGE/file).open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

def run():
    # Stage 1 keeps the preceding ordinary-BEV assumptions to isolate deployment.
    stage1=[calculate(f'{d}·中诱发·维持电耗里程') for d in DEPLOYMENT]
    write_json('step1_separated_deployment.json',[{k:v for k,v in r.items() if k in ['case','annual','regional']} for r in stage1])
    print('Step 1 complete: four independently captured deployment paths.')
    # Stage 2 grid is exact under the stated fixed fleet-state parameter assumptions.
    frozen='Robotaxi先行·中诱发·维持电耗里程'
    stage2=[]
    for name,overrides in VARIANTS:
        c=coefficients(frozen,overrides)
        stage2.append(dict(variant=name,overrides=overrides,**c))
    write_json('step2_joint_coefficients_frozen_baseline.json',stage2)
    print('Step 2 complete: derived exact sigma/g response boundaries.')
    # Stage 3 introduces ordinary efficiency and distance in BOTH counterparts.
    all_cases=[calculate(case) for case in CASES]
    central=next(r for r in all_cases if r['case']==DEFAULT)
    for row,old in zip(central['regional'][:7],OLD_RESULTS['central_detail']['regional'][:7]):
        assert row['region']==old['region']
        for key in ['e_noav','k0']:
            assert math.isclose(row[key],old[key],rel_tol=1e-12)
        assert row['e_full']==row['e_noav']
    zero=calculate(capture_multiplier=0.)
    assert all(math.isclose(r['e_full'],r['e_noav'],rel_tol=1e-12) and r['device_full']==0 for r in zero['annual'])
    fixed=calculate(overrides={'sigma':0.,'g_multiplier':0.})
    assert all(math.isclose(r['e_full'],r['e_fixed'],rel_tol=1e-12) for r in fixed['annual'])
    shift=calculate(overrides={'g_multiplier':0.})
    assert all(math.isclose(r['e_full'],r['e_shift'],rel_tol=1e-12) for r in shift['annual'])
    joint=[];grid=[];boundary=[]
    for name,overrides in VARIANTS:
        c=coefficients(DEFAULT,overrides);joint.append(dict(variant=name,overrides=overrides,**c))
        for si in range(41):
            sigma=si/100
            gc=(c['S']-c['B']*sigma)/(c['A']+c['B']*sigma)
            boundary.append(dict(variant=name,sigma=sigma,g_zero_delta=gc,nonnegative_boundary=gc if gc>=0 else None))
            for gi in range(51):
                g=gi/100;de=delta(c,sigma,g)
                grid.append(dict(variant=name,sigma=sigma,g=g,delta_e_TWh=de/1e9,energy_change_pct=100*de/c['e0'],r_total=(de+c['S'])/c['S'] if c['S']>0 else None))
        # Validate a zero boundary and several full model calculations, not only the grid formula.
        if c['S']>0:
            for sigma in [0,c['S']/c['B']/2,c['S']/c['B']]:
                gc=(c['S']-c['B']*sigma)/(c['A']+c['B']*sigma)
                assert abs(delta(c,sigma,gc))<.001
        for sigma,g in [(0,0),(.15,.15),(.4,.5)]:
            full=calculate(DEFAULT,{**overrides,'sigma':sigma,'g_multiplier':g/.15})['annual'][-1]
            assert math.isclose(full['delta_e'],delta(c,sigma,g),rel_tol=1e-10,abs_tol=.001)
    # Joint ordinary efficiency/mileage tests, one common set of inputs for both comparisons.
    common_sens=[]
    for e in [1.10,1.,.85,.75]:
        for lo in [.85,1.,1.15]:
            for lop in [.80,1.,1.20]:
                label=f'e{e:.2f}_Lother{lo:.2f}_Lops{lop:.2f}'
                FUTURES[label]={'e2035':e,'other_km2035':lo,'ops_km2035':lop}
                r=calculate(f'Robotaxi先行·中诱发·{label}')['annual'][-1]
                common_sens.append(dict(e_multiplier=e,other_km_multiplier=lo,ops_km_multiplier=lop,**r))
                del FUTURES[label]
    csv_rows('普通BEV电耗里程_36组联合检验.csv',common_sens)
    state_tests=[('Robotaxi在线12小时',{'robot_online_day':12.}),('Robotaxi在线18小时',{'robot_online_day':18.}),
        ('Robotaxi在线24小时',{'robot_online_day':24.}),('私人候用0小时',{'private_idle_day':0.}),
        ('私人候用2小时',{'private_idle_day':2.}),('Robotaxi待机功率0.20kW',{'pic_robot':.14,'pio_robot':.06}),
        ('Robotaxi待机保持行驶功率',{'pic_robot':.35,'pio_robot':.15}),
        ('两类待机均保持行驶功率',{'pic_private':.20,'pio_private':.10,'pic_robot':.35,'pio_robot':.15}),
        ('全部设备功率4倍',{'power_multiplier':4.})]
    state_results=[dict(test=name,overrides=overrides,annual=calculate(DEFAULT,overrides)['annual']) for name,overrides in state_tests]
    csv_rows('设备工作时间与待机功率_9组检验.csv',[dict(test=x['test'],**x['annual'][-1]) for x in state_results])
    write_json('step3_shared_future_results.json',dict(default_case=DEFAULT,cases=[{k:v for k,v in c.items() if k in ['case','annual','regional']} for c in all_cases],central_detail=central,joint_coefficients=joint,common_sensitivity=common_sens,state_sensitivity=state_results))
    write_json('common_inputs.json',{k:future_inputs(k) for k in FUTURES})
    spec=copy.deepcopy(OLD)
    for parameter in spec['parameters']:
        parameter['value']=PARAMS[parameter['key']]
    spec.update(default_case=DEFAULT,case_names=CASES,deployment_nodes=DEPLOYMENT,future_cases=FUTURES,induced_levels=RESPONSES,
                case_registry=[dict(name=c,deployment_index=list(DEPLOYMENT).index(c.split('·')[0])+1,response_index=list(RESPONSES).index(c.split('·')[1])+1,future_index=list(FUTURES).index(c.split('·')[2])+1) for c in CASES])
    spec['scope_notes']=[
        '七个电网区域；排除西藏、港澳台；只计BEV乘用车网侧充电电量及电力CO2。',
        '私人原BEV客公里捕获率cprivate和原运营BEV捕获率cops独立，总体a从捕获结果计算。',
        '私人AV车队=cprivate×原私人BEV车队×(1-beta)，新增g增加原车队里程；Robotaxi按需求扩容。',
        '公务与剩余非网约租赁不转为AV；原方式转入全部进入Robotaxi，原方式能耗变化不纳入。',
        '普通BEV电耗、年度里程、车量及电网在AV/noAV同年比较中完全共享。',
        '四条落地路径与普通BEV改进范围是研究者情景，不是中国实测预测或概率区间。',
        'g为当年AV承接与转入之后的相对新增客公里，非逐年复利增长率。',
        '设备按净电池侧功率和行驶/候用车队小时计算；计算用电属于设备总用电。',
        '解析联合阈值依赖固定速度/功率/人数/空驶比/单车年里程和按需求扩容，不代表容量约束模型。',
    ]
    write_json('model_spec.json',spec)
    csv_rows('48组情景_年度结果.csv',[dict(case=c['case'],**r) for c in all_cases for r in c['annual']])
    csv_rows('48组情景_七区域结果.csv',[dict(case=c['case'],**r) for c in all_cases for r in c['regional']])
    csv_rows('联合敏感性_2035.csv',grid)
    csv_rows('联合零增量边界_2035.csv',boundary)
    write_json('validation.json',dict(baseline_2024_preserved=True,zero_av_restores_noav=True,fixed_and_shift_controls=True,
                additive_energy_decomposition=True,analytic_threshold_matches_full_model=True,cases=48,grid_points=len(grid),common_joint_cases=36))
    print('Step 3 complete: 48 main cases, 36 joint ordinary-BEV futures, 18819 response grid points.')
    print(json.dumps({c['case']:{k:c['annual'][-1][k] for k in ['a_global','e_noav','e_full','delta_e','technical_saving','r_total']} for c in all_cases if c['case'].split('·')[1]=='中诱发'},ensure_ascii=True,indent=2))

if __name__=='__main__':
    run()
