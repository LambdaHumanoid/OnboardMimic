import math

def feedback(latest, target, now):
    try:
        body_time, body = latest['rt/lowstate']
        imu_time, imu = latest['rt/secondary_imu']
        age = max(now - body_time, now - imu_time)
        if not 0 <= age <= .5:
            return {'fresh': False, 'age_ms': round(age * 1000)}
        motors = body['motor_state'][:29]
        q = [m['q'] for m in motors]
        dq = [m['dq'] for m in motors]
        quat = imu['quaternion']
        valid = (len(q) == 29 and len(quat) == 4 and
                 all(type(v) in (int, float) and math.isfinite(v) for v in q + dq + quat)
                 and .9 < sum(v*v for v in quat) < 1.1)
        if not valid:
            return {'fresh': True, 'valid': False}
        return dict(fresh=True, valid=True, age_ms=round(age*1000), q=q, dq=dq,
                    faults=[i for i, m in enumerate(motors) if m.get('motorstate', 0)],
                    max_speed_rad_s=max(map(abs, dq)),
                    max_error_rad=max(abs(a-b) for a, b in zip(q, target)),
                    upright=1-2*(quat[1]**2+quat[2]**2)/sum(v*v for v in quat) > math.cos(.35))
    except (KeyError, TypeError, ValueError, OverflowError):
        return {'fresh': False, 'valid': False}


def safe(t):
    return bool(t.get('fresh') and t.get('valid') and not t.get('faults')
                and t.get('max_speed_rad_s', 99) <= 12.5)
