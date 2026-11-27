"""Offline stage calibration from a simulated trajectory, never a hardware permit."""
import numpy as np

def calibrated_stages(trace, depths=(.03,.20,.40)):
    rows=np.asarray(trace,dtype=float)
    if rows.ndim!=2 or rows.shape[1]<4 or not np.isfinite(rows).all():
        raise ValueError('Invalid calibration trace')
    # Keep first observation at each progress value; do not calibrate on later hold drift.
    progress,idx=np.unique(rows[:,3],return_index=True)
    height=rows[idx,1];depth=np.maximum.accumulate(height[0]-height)
    requested=np.asarray(depths,dtype=float)
    if requested.ndim!=1 or not len(requested) or not np.isfinite(requested).all() or np.any(requested<=0) or np.any(np.diff(requested)<=0):
        raise ValueError('Depths must be finite, positive and increasing')
    if requested[-1]>depth[-1]:raise ValueError('Requested depth exceeds calibrated trajectory')
    return [dict(depth_m=float(d),progress_s=float(np.interp(d,depth,progress)),height_m=float(height[0]-d)) for d in requested]
