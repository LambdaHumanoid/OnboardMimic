"""Offline inverse-dynamics calculation along a prescribed path, not simulation."""
import argparse,json
from pathlib import Path
import numpy as np
import mujoco


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('model');p.add_argument('plan');p.add_argument('output');a=p.parse_args()
    root=Path(__file__).parent;spec=json.loads((root/'model.json').read_text());profile=json.loads(Path('/home/oem/jiangyiming/OnboardMimic_builds/controlled-stop-20261116/official-effort-comparison.json').read_text())
    limits=np.array([min(x['urdf_effort_nm'],x['simulation_effort_nm']) for x in profile['joints']])
    m=mujoco.MjModel.from_xml_path(a.model);d=mujoco.MjData(m);plan=np.load(a.plan)
    joints=[m.joint(n+'_joint').id for n in spec['joint_names']];qa=m.jnt_qposadr[joints];va=m.jnt_dofadr[joints]
    feet=[m.body(n+'_ankle_roll_link').id for n in ('left','right')];order=np.r_[np.arange(6),va]
    full=np.zeros((len(plan['time']),m.nq));full[:,:3]=plan['base_translation']+np.array([0,0,1.]);full[:,3:7]=plan['quaternion'];full[:,qa]=plan['q']
    velocities=np.zeros((len(full),m.nv))
    for i in range(1,len(full)-1):mujoco.mj_differentiatePos(m,velocities[i],float(plan['time'][i+1]-plan['time'][i-1]),full[i-1],full[i+1])
    velocities[:,va]=plan['dq'];acceleration=np.gradient(velocities,plan['time'],axis=0);acceleration[:,va]=plan['ddq']
    max_tau=np.zeros(29);max_inertial=np.zeros(29);max_residual=0.;min_fz=float('inf');max_friction=0.;min_cop_margin=float('inf')
    for i in range(0,len(full),5):
        d.qpos[:]=full[i];d.qvel[:]=velocities[i];mujoco.mj_forward(m,d)
        jac=[]
        for foot in feet:
            jp=np.zeros((3,m.nv));jr=np.zeros((3,m.nv));mujoco.mj_jacBody(m,d,jp,jr,foot);jac.extend((jp,jr))
        J=np.vstack(jac)[:,order];A=J[:,:6].T;B=J[:,6:].T
        bias=d.qfrc_bias[order].copy();d.qacc[:]=acceleration[i];force=np.zeros(m.nv);mujoco.mj_rne(m,d,1,force);force=force[order]
        wrench=np.linalg.lstsq(A,force[:6],rcond=None)[0];torque=force[6:]-B@wrench
        baseline=bias[6:]-B@np.linalg.lstsq(A,bias[:6],rcond=None)[0]
        max_tau=np.maximum(max_tau,abs(torque));max_inertial=np.maximum(max_inertial,abs(torque-baseline));max_residual=max(max_residual,float(np.max(abs(A@wrench-force[:6]))))
        for j in range(2):
            f=wrench[j*6:j*6+3];min_fz=min(min_fz,float(f[2]));max_friction=max(max_friction,float(np.linalg.norm(f[:2])/max(f[2],1e-9)))
            R=d.xmat[feet[j]].reshape(3,3);local_f=R.T@f;moment=R.T@wrench[j*6+3:j*6+6]-np.cross([0,0,-.035],local_f)
            copx=-moment[1]/local_f[2];copy=moment[0]/local_f[2]
            min_cop_margin=min(min_cop_margin,float(min(copx+.05,.12-copx,copy+.025,.025-copy)))
    report=dict(scope='prescribed-path inverse dynamics; unconstrained least-norm contact allocation, not balance or contact-feasibility proof',
        max_required_joint_torque_nm=float(max(max_tau)),max_extra_acceleration_torque_nm=float(max(max_inertial)),
        max_model_effort_ratio=float(max(max_tau/limits)),min_world_vertical_contact_force_n=min_fz,max_horizontal_to_vertical_force_ratio=max_friction,
        min_assumed_sole_cop_margin_m=min_cop_margin,
        max_floating_base_balance_residual=max_residual,joints=[dict(name=n,peak_required_nm=float(v),peak_acceleration_contribution_nm=float(dv),model_bound_nm=float(l)) for n,v,dv,l in zip(spec['joint_names'],max_tau,max_inertial,limits)],hardware_ready=False)
    Path(a.output).write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='joints'},indent=2))


if __name__=='__main__':main()
