import tempfile
from pathlib import Path
import unittest
from onboardmimic_lab.replay import validate_plan, prepare_plan


class RemovedLegacyReplayTest(unittest.TestCase):
    def test_legacy_plan_and_directory_are_refused(self):
        with self.assertRaises(ValueError):
            validate_plan(dict(schema='onboardmimic.lab.replay-plan',schema_version=1))
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'manifest.json').write_text('{"format":"onboardmimic_lab_multimodal_v2","status":"saved"}')
            with self.assertRaises(ValueError):prepare_plan(root)


class SimulationObservationTest(unittest.TestCase):
    def test_history_order_joint_mapping_and_previous_action(self):
        import numpy as np
        from collections import deque
        from onboardmimic_lab.simulation import Decoder
        decoder=Decoder.__new__(Decoder);decoder.np=np
        policy_to_body=np.array([0,6,12,1,7,13,2,8,14,3,9,15,22,4,10,16,23,5,11,17,24,18,25,19,26,20,27,21,28])
        decoder.params=dict(default_angles=np.ones(29),mujoco_to_isaaclab=policy_to_body,
                            isaaclab_to_mujoco=np.argsort(policy_to_body),g1_action_scale=np.ones(29)*.5)
        decoder.history=deque(maxlen=4);decoder.last_action=np.zeros(29)
        q=np.arange(29.)+1;dq=np.arange(29.)+100
        decoder.observe(q,dq,np.array([1.,2.,3.]),np.array([0.,0.,-1.]))
        self.assertEqual(decoder.history[0]['body_joint_positions'][:6].tolist(),[0,6,12,1,7,13])
        decoder.last_action=np.arange(29.)
        decoder.observe(q+10,dq+10,np.array([4.,5.,6.]),np.array([0.,0.,-1.]))
        decoder.fields=[('token',1),('base_angular_velocity',4),('last_actions',4)]
        captured={}
        class Session:
            def run(self,names,inputs):
                captured.update(inputs);return [np.arange(29,dtype=np.float32).reshape(1,29)]
        decoder.session=Session();decoder.input_name='obs';decoder.output_name='action'
        target=decoder.step([.25]*64)
        obs=captured['obs'][0]
        self.assertEqual(obs[64:76].tolist(),[1,2,3,1,2,3,1,2,3,4,5,6])
        np.testing.assert_array_equal(obs[-29:],np.arange(29))
        self.assertEqual(target[6],1.5)  # hardware right hip pitch <- policy index 1
        self.assertEqual(target[1],2.5)  # hardware left hip roll <- policy index 3

    def test_parameter_constants_match_known_g1_semantics(self):
        # Pure local fixture tests arithmetic and inverse map handling without
        # making the test depend on an installed runtime source tree.
        from onboardmimic_lab.sonic_parameters import read_parameters
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'params.hpp'
            arrays={'default_angles':['-0.312']*29,'g1_action_scale':['.25 * F / K']*29,
                    'kps':['K']*29,'kds':['2 * A * W']*29,
                    'isaaclab_to_mujoco':[str(i) for i in range(29)],'mujoco_to_isaaclab':[str(i) for i in range(29)]}
            text='const double A = .01; const double W = 60; const double K = A*W*W; const double F = 88;\n'
            text+='\n'.join('const std::array<double,29> '+k+' = {'+','.join(v)+'};' for k,v in arrays.items())
            p.write_text(text);v=read_parameters(p)
            self.assertAlmostEqual(v['g1_action_scale'][0],22/36)
            self.assertAlmostEqual(v['kds'][0],1.2)
            p.write_text(text.replace('isaaclab_to_mujoco = {0,1,','isaaclab_to_mujoco = {0,0,'))
            with self.assertRaises(ValueError):read_parameters(p)
