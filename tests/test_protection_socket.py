import fcntl
from pathlib import Path
import socket
import tempfile
import unittest
from onboardmimic.robots.unitree_g1.bridge.protection_socket import cleanup

class SocketRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.path=self.root/'control.sock'
        self.locks=[str(self.root/n) for n in ('current.lock','compat.lock')]
    def endpoint(self, listen=False):
        s=socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET);s.bind(str(self.path))
        if listen:s.listen(1)
        return s
    def test_absent_and_abandoned(self):
        self.assertFalse(cleanup(self.path,self.locks))
        self.endpoint().close()
        self.assertTrue(cleanup(self.path,self.locks));self.assertFalse(self.path.exists())
    def test_live_listener_preserved(self):
        with self.endpoint(True):
            with self.assertRaisesRegex(RuntimeError,'live listener'):cleanup(self.path,self.locks)
            self.assertTrue(self.path.exists())
    def test_either_writer_lock_blocks_cleanup(self):
        self.endpoint().close()
        for lock in self.locks:
            with open(lock,'w') as f:
                fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaises(BlockingIOError):cleanup(self.path,self.locks)
                self.assertTrue(self.path.exists())
    def test_regular_file_and_symlink_preserved(self):
        self.path.write_text('keep')
        with self.assertRaises(RuntimeError):cleanup(self.path,self.locks)
        self.assertEqual(self.path.read_text(),'keep');self.path.unlink()
        target=self.root/'target';target.write_text('keep');self.path.symlink_to(target)
        with self.assertRaises(RuntimeError):cleanup(self.path,self.locks)
        self.assertTrue(self.path.is_symlink());self.assertEqual(target.read_text(),'keep')
    def test_public_directory_rejected(self):
        self.root.chmod(0o755)
        with self.assertRaises(RuntimeError):cleanup(self.path,self.locks)
