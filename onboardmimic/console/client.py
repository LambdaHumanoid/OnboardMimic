"""Select a real-control backend once at web startup; never fall back on errors."""


class OnboardBackend:
    def __init__(self, client, robot_id=None, backend_id=None):
        self.client = client
        self.expected_robot, self.expected_backend = robot_id, backend_id

    def status(self):
        try:
            status = self.client.status()
            if status.get('mode') != 'onboard_real':
                raise RuntimeError('机载地址未提供真机控制接口')
            if ((self.expected_robot and status.get('robot_id') != self.expected_robot) or
                    (self.expected_backend and status.get('backend') != self.expected_backend)):
                raise RuntimeError('Runtime robot/backend does not match console selection')
            return status
        except Exception as exc:
            return dict(state='error', mode='onboard_unavailable', robot='—',
                        pids=[], pose_ready=False, prepare_available=False,
                        control_transferred=False, motion_enabled=False,
                        telemetry={'fresh':False},
                        workflow_error='机载连接未就绪：'+str(exc))

    def command(self, action):
        # Estop must still be attempted when status is unavailable.
        if action != 'estop' and self.status()['mode'] != 'onboard_real':
            raise RuntimeError('机载接口未就绪，未发送运动请求')
        return self.client.command(action)

    def control(self, command):
        if (self.expected_robot or self.expected_backend) and not command.get("stop"):
            if self.status()["mode"] != "onboard_real":
                raise RuntimeError("Runtime robot/backend is unavailable or mismatched")
        return self.client.control(command)

