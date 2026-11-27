"""Six-second, continuous standing-to-20-cm candidate. Offline generation only."""
import argparse,json
from pathlib import Path
import numpy as np
from scipy.interpolate import BSpline,make_interp_spline
from scipy.optimize import lsq_linear
from coordinated_plan import generate,quat_wxyz,ry
from model import rotation
from native_model import NativeModel
from standing_plan import rotation_error


def fit_path(values,phase,lower,upper,ncoeff=40,regularization=.005):
    degree=5
    knots=np.r_[np.zeros(degree+1),np.linspace(0,1,ncoeff-degree+1)[1:-1],np.ones(degree+1)]
    basis=BSpline(knots,np.eye(ncoeff),degree)
    B=basis(phase);D=np.diff(np.eye(ncoeff),n=3,axis=0)
    A=np.vstack((B,np.sqrt(regularization)*D));Y=np.vstack((values,np.zeros((len(D),values.shape[1]))))
    # Clamped quintic with three equal endpoint coefficients gives exactly zero
    # velocity and acceleration at both ends, not just small finite differences.
    fixed=np.r_[np.arange(3),np.arange(ncoeff-3,ncoeff)];free=np.arange(3,ncoeff-3)
    coef=np.zeros((ncoeff,values.shape[1]));coef[:3]=values[0];coef[-3:]=values[-1]
    rhs=Y-A[:,fixed]@coef[fixed]
    for j in range(values.shape[1]):
        if np.ptp(values[:,j])<1e-10:coef[:,j]=values[0,j];continue
        fit=lsq_linear(A[:,free],rhs[:,j],bounds=(lower[j],upper[j]),tol=1e-10,max_iter=200)
        if not fit.success:raise ValueError('Spline fit failed for channel %d'%j)
        coef[free,j]=fit.x
    return knots,coef,degree


def generate_direct(model,q,quat):
    path,_=generate(model,q,quat,depth=.20,duration=30.,count=201,geometry_weight=10.,relative_pitch_limit=1.)
    R0=rotation(quat)
    pitch=np.array([np.arctan2((R0.T@rotation(v))[0,2],(R0.T@rotation(v))[2,2]) for v in path['quaternion']])
    values=np.column_stack((path['q'],path['base_translation'],pitch))
    limits=np.asarray(model.spec['limits']);lower=np.r_[limits[:,0]+.01,values[:,29:].min(axis=0)-1e-10];upper=np.r_[limits[:,1]-.01,values[:,29:].max(axis=0)+1e-10]
    lower[[4,10]]=np.maximum(lower[[4,10]],-.75)
    zeros=np.zeros(values.shape[1])
    spline=make_interp_spline(path['time']/path['time'][-1],values,k=5,bc_type=([(1,zeros),(2,zeros)],[(1,zeros),(2,zeros)]))
    # Redistribute time along the path: slow down where curvature demands high
    # acceleration, rather than multiplying all waypoint speeds by one factor.
    grid=np.linspace(0,1,401);ds=np.diff(grid)
    qs=spline.derivative(1)(grid)[:,:29];qss=spline.derivative(2)(grid)[:,:29]
    vmax=1.15;amax=6.
    speed=np.minimum(np.min(vmax/np.maximum(abs(qs),1e-7),axis=1),np.sqrt(np.min((amax*.5)/np.maximum(abs(qss),1e-7),axis=1)))
    accel=np.min((amax*.5)/np.maximum(abs(qs),1e-7),axis=1)
    speed=np.minimum(speed,2.);speed[0]=speed[-1]=0.
    for i in range(1,len(grid)):speed[i]=min(speed[i],np.sqrt(speed[i-1]**2+2*min(accel[i-1],accel[i])*ds[i-1]))
    for i in range(len(grid)-2,-1,-1):speed[i]=min(speed[i],np.sqrt(speed[i+1]**2+2*min(accel[i+1],accel[i])*ds[i]))
    raw_time=np.r_[0,np.cumsum(2*ds/(speed[:-1]+speed[1:]))]
    if raw_time[-1]>6:raise ValueError('Six seconds is shorter than this path timing bound: %.3f s'%raw_time[-1])
    stretch=6/raw_time[-1];clock_time=raw_time*stretch;speed/=stretch
    # Fit a smooth monotone clock rather than making the robot accelerate and
    # decelerate at every discretization point. Geometry stays on the same path.
    ct,cc,ck=fit_path(grid[:,None],clock_time/6,np.array([0.]),np.array([1.]),ncoeff=32,regularization=.05)
    clock=BSpline(ct*6,cc[:,0],ck)
    times=np.linspace(0,6,1501);phase=clock(times);sd=clock.derivative(1)(times);sdd=clock.derivative(2)(times)
    sddd=clock.derivative(3)(times)
    fitted=spline(phase);d1=spline.derivative(1)(phase);d2=spline.derivative(2)(phase);d3=spline.derivative(3)(phase)
    vel=d1*sd[:,None];acc=d2*sd[:,None]**2+d1*sdd[:,None]
    jerk=d3*sd[:,None]**3+3*d2*(sd*sdd)[:,None]+d1*sddd[:,None]
    initial=model.calculate(q,np.zeros(29),quat,np.zeros(3));max_pe=max_re=max_ce=0.
    quats=[];max_ff=0.
    for row,v in zip(fitted,vel):
        base=row[29:32];quaternion=quat_wxyz(R0@ry(row[32]));quats.append(quaternion)
        result=model.calculate(row[:29],v[:29],quaternion,np.array([0.,v[32],0.]))
        max_pe=max(max_pe,float(np.max(abs(result['feet']+base-initial['feet']))))
        max_re=max(max_re,max(float(np.max(abs(rotation_error(a,b)))) for a,b in zip(initial['foot_rotations'],result['foot_rotations'])))
        max_ce=max(max_ce,float(np.max(abs((result['com']+base-initial['com'])[:2]))))
        max_ff=max(max_ff,float(np.max(abs(result['feedforward']))))
    speed=float(np.max(abs(vel[:,:29])));accel=float(np.max(abs(acc[:,:29])));j=float(np.max(abs(jerk[:,:29])))
    if np.min(sd)<-1e-8 or np.min(phase)<-1e-8 or np.max(phase)>1+1e-8:raise ValueError('Nonmonotone trajectory clock')
    if speed>1.2 or accel>4. or j>80.:raise ValueError('Six-second candidate exceeds velocity/acceleration/jerk admission bounds')
    if max_pe>.001 or max_re>.005 or max_ce>.001:raise ValueError('Interpolated path exceeds geometric tolerance')
    if np.any(fitted[:,:29]<limits[:,0]+.005) or np.any(fitted[:,:29]>limits[:,1]-.005) or np.min(fitted[:,[4,10]])<-.755:raise ValueError('Interpolated joint path exceeds margins')
    quats=np.asarray(quats)
    if np.min(1-2*(quats[:,1]**2+quats[:,2]**2))<np.cos(np.radians(30)):raise ValueError('Interpolated pelvis tilt exceeds candidate bound')
    report=dict(duration_s=6.,depth_m=.20,max_target_speed_rad_s=speed,max_target_acceleration_rad_s2=accel,max_target_jerk_rad_s3=j,
        max_foot_position_error_m=max_pe,max_foot_rotation_error=max_re,max_com_xy_error_m=max_ce,
        max_bias_feedforward_nm=max_ff,endpoint_speed_rad_s=float(np.max(abs(vel[[0,-1],:29]))),
        endpoint_acceleration_rad_s2=float(np.max(abs(acc[[0,-1],:29]))),
        scope='kinematic_and_bias_checks_no_acceleration_force_or_physical_balance_validation',hardware_ready=False)
    plan=dict(q=fitted[:,:29],dq=vel[:,:29],ddq=acc[:,:29],time=times,quaternion=quats,base_translation=fitted[:,29:32],depth_m=np.array(.20),
        base_velocity=vel[:,29:32],base_acceleration=acc[:,29:32],pitch_velocity=vel[:,32],pitch_acceleration=acc[:,32],
        spline_knots=spline.t,spline_coefficients=spline.c,spline_degree=np.array(spline.k),
        clock_knots=clock.t,clock_coefficients=clock.c,clock_degree=np.array(clock.k),initial_quaternion=np.asarray(quat)/np.linalg.norm(quat))
    return plan,report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('sample',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    source=json.loads(a.sample.read_text());f=source['raw_feedback'];q=[x['q'] for x in f['motor_state'][:29]]
    plan,report=generate_direct(NativeModel(Path(__file__).with_name('model.json')),q,f['imu_state']['quaternion'])
    a.output.parent.mkdir(parents=True,exist_ok=True);np.savez(a.output,**plan);a.output.with_suffix('.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
