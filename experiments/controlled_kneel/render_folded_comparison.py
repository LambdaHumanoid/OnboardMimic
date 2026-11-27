"""Render saved native-executor simulations; never connect to robot hardware."""
import argparse
import json
import subprocess
from pathlib import Path

import mujoco
import numpy as np

from experiments.controlled_kneel.simulate import setup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, help='Explicit offline report directories for each two- or three-column comparison')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    model, data, *_ = setup()
    renderer = mujoco.Renderer(model, height=384, width=512)
    camera = mujoco.MjvCamera()
    camera.lookat[:] = [0, 0, .42]
    camera.distance = 2.3
    camera.azimuth = 90
    camera.elevation = -12
    cases = [('nominal', 'v0.1_bias0.35', .35, '轻微前移'),
             ('stationary', 'v0_bias0', 0., '近似静止'),
             ('backward_02', 'v-0.2_bias0', 0., '正在后退')]
    entries=[]
    for case, ablation, bias, label in cases:
        entries.append(dict(id=case,label=label,bias=bias,paths=[str(args.results/'validation/original'/case),str(args.results/'validation/folded_slow_sit'/case),str(args.results/'bias/low7_sit_hold'/ablation)]))
    if args.manifest:
        entries=json.loads(args.manifest.read_text())
    document = {'cases': [], 'base_vx': -.00090341, 'hardware_changed': False}
    try:
        for entry in entries:
            case,label,bias=entry['id'],entry['label'],entry.get('bias',0.)
            paths = [Path(p) for p in entry['paths']]
            if len(paths) not in (2,3):raise ValueError('Two or three saved trajectories required')
            poses = [np.load(p/'qpos.npy') for p in paths]
            rows = [dict(report=json.loads((p/'report.json').read_text()),
                         geometry=json.loads((p/'geometry.json').read_text())) for p in paths]
            vf = "drawtext=text='t=%{pts\\:flt} s | 1x simulation':x=12:y=12:fontsize=20:fontcolor=white:box=1:boxcolor=black@0.6"
            command = ['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo',
                       '-pix_fmt', 'rgb24', '-s', str(512*len(paths))+'x384', '-r', '25', '-i', '-',
                       '-vf', vf, '-an', '-c:v', 'libx264', '-crf', '20',
                       '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
                       str(args.output/(case+'.mp4'))]
            process = subprocess.Popen(command, stdin=subprocess.PIPE)
            try:
                for index in range(0, len(poses[0]), 2):
                    frames = []
                    for trajectory in poses:
                        data.qpos[:] = trajectory[index]
                        mujoco.mj_forward(model, data)
                        renderer.update_scene(data, camera=camera)
                        frames.append(renderer.render().copy())
                    process.stdin.write(np.concatenate(frames, axis=1).tobytes())
            finally:
                process.stdin.close()
                process.wait()
            if process.returncode:
                raise RuntimeError('ffmpeg failed')
            document['cases'].append(dict(id=case, label=label, bias=bias, columns=rows))
            print('Rendered', case, flush=True)
    finally:
        renderer.close()
    (args.output/'data.json').write_text(json.dumps(document, ensure_ascii=False))


if __name__ == '__main__':
    main()
