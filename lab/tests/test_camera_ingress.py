import socket,tempfile,time,unittest
from pathlib import Path
from onboardmimic_lab.camera_ingress import CameraIngress
from onboardmimic_lab.video_timing import MpegTsPtsExtractor
from test_collection import make_video

class CameraIngressTest(unittest.TestCase):
    def test_idle_monitor_record_delivery_and_disconnect(self):
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as reserve:
            reserve.bind(('127.0.0.1',0));port=reserve.getsockname()[1]
        with tempfile.TemporaryDirectory() as d:
            video=make_video(Path(d)/'video.ts')
            camera=CameraIngress(port);udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
            try:
                self.assertFalse(camera.status()['ready'])
                deadline=time.monotonic()+3
                while not camera.status()['ready'] and time.monotonic()<deadline:
                    for i in range(0,len(video),1316):udp.sendto(video[i:i+1316],('127.0.0.1',port))
                    time.sleep(.02)
                self.assertTrue(camera.status()['ready'],camera.status())
                self.assertEqual(camera.drain(),[])  # idle observation retains no video
                camera.begin();before=time.monotonic_ns()
                udp.sendto(video[:1316],('127.0.0.1',port));packets=[]
                deadline=time.monotonic()+1
                while not packets and time.monotonic()<deadline:
                    time.sleep(.01);packets=camera.drain()
                self.assertEqual(packets[0][0],video[:1316]);self.assertGreaterEqual(packets[0][1],before)
                camera.end();time.sleep(.55)
                self.assertFalse(camera.status()['ready'])
            finally:camera.close();udp.close()

    def test_busy_port_is_reported_not_reused(self):
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as owner:
            owner.bind(('127.0.0.1',0));camera=CameraIngress(owner.getsockname()[1])
            try:
                deadline=time.monotonic()+1
                while not camera.status()['error'] and time.monotonic()<deadline:time.sleep(.01)
                self.assertFalse(camera.status()['ready']);self.assertTrue(camera.status()['error'])
                with self.assertRaises(ValueError):camera.begin()
            finally:camera.close()

    def test_split_packets_pts_and_receive_clocks_survive_recording_restart(self):
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as reserve:
            reserve.bind(('127.0.0.1',0));port=reserve.getsockname()[1]
        with tempfile.TemporaryDirectory() as d:
            video=make_video(Path(d)/'video.ts')
            camera=CameraIngress(port);udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
            try:
                # Seed an idle partial packet; each new capture must start fresh.
                time.sleep(.05)
                for _ in range(2):
                    with camera.lock:camera.parser.feed(video[:93])
                    camera.begin();before=time.monotonic_ns()
                    chunks=[video[i:i+997] for i in range(0,len(video),997)]
                    for chunk in chunks:udp.sendto(chunk,('127.0.0.1',port))
                    packets=[];deadline=time.monotonic()+2
                    while len(packets)<len(chunks) and time.monotonic()<deadline:
                        packets.extend(camera.drain());time.sleep(.01)
                    self.assertEqual([p[0] for p in packets],chunks)
                    expected=MpegTsPtsExtractor()
                    for payload,mono,wall,pts in packets:
                        self.assertGreaterEqual(mono,before)
                        self.assertLessEqual(mono,time.monotonic_ns())
                        self.assertGreater(wall,0)
                        self.assertEqual(pts,expected.feed(payload))
                    self.assertTrue(any(p[3] for p in packets))
                    camera.end()
            finally:camera.close();udp.close()
