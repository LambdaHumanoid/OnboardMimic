"""Native collection configuration and LeRobot v2.1 inspection/replay preparation."""
import argparse
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    native=sub.add_parser('configure-sonic')
    native.add_argument('--spec',type=Path,required=True)
    native.add_argument('--output',type=Path,required=True)
    native.add_argument('--data-root',type=Path,default=Path('data'))
    native.add_argument('--gateway',default='http://127.0.0.1:18921')
    native.add_argument('--telemetry-endpoint',default='tcp://127.0.0.1:16691')
    native.add_argument('--camera-port',type=int,default=18554)
    validate=sub.add_parser('validate-collection')
    validate.add_argument('--config',type=Path,required=True)
    for name in ('inspect','replay-plan','replay-sim'):
        command=sub.add_parser(name)
        command.add_argument('episode',type=Path,help='dataset root or OM episode locator')
        command.add_argument('--episode-index',type=int,help='LeRobot episode index within the dataset')
        command.add_argument('--segment',type=int)
        command.add_argument('--output',type=Path)
        if name=='replay-sim':
            command.add_argument('--config',type=Path,required=True)
            command.add_argument('--no-video',action='store_true')
    args=parser.parse_args()
    try:
        if args.command=='configure-sonic':
            from .collection import validate_config
            spec=json.loads(args.spec.read_text())
            config=validate_config(dict(capture_adapter='sonic-native-v2',controller_spec=spec,
                root=str(args.data_root.resolve()),same_host=True,gateway=args.gateway,
                telemetry_endpoint=args.telemetry_endpoint,camera_port=args.camera_port))
            with args.output.open('x') as stream:json.dump(config,stream,indent=2,allow_nan=False)
            result=dict(config_file=str(args.output.resolve()),live_verified=False)
        elif args.command=='validate-collection':
            from .collection import load_config
            config=load_config(args.config)
            result=dict(valid=True,capture_adapter=config.get('capture_adapter'),live_verified=False)
        else:
            from .lerobot_storage import entries
            if (args.episode/'meta/info.json').exists():
                available=entries(args.episode)
                index=args.episode_index
                if index is None:
                    if len(available)!=1:raise ValueError('Select --episode-index from the LeRobot dataset')
                    index=0
                selected=next((e for e in available if e['episode_index']==index),None)
                if selected is None:raise ValueError('Unknown LeRobot episode index')
                args.episode=args.episode/'episodes'/selected['om']['id']
            from .replay import prepare_plan,summary
            if args.command=='inspect':
                from .lerobot_dataset import verify
                result=verify(args.episode)
            elif args.command=='replay-plan':
                plan=prepare_plan(args.episode,args.segment);result=summary(plan)
                if args.output:
                    with args.output.open('x') as stream:json.dump(plan,stream,indent=2,allow_nan=False)
            else:
                raise ValueError('Compact LeRobot recordings omit decoder history; diagnostic simulation is unavailable')
        print(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False))
    except (ValueError,KeyError,OSError,ImportError,RuntimeError) as error:
        parser.exit(1,str(error)+'\n')
