"""Single-owner Planner requests with a finite speed and expiring lease."""
import math


def validate_speed(speed, limit=5.):
    if type(speed) not in (int, float) or not math.isfinite(speed) or not 0 <= speed <= limit:
        raise ValueError('目标速度必须在 0..%g m/s 之间' % limit)
    return float(speed)


class PlannerLease:
    def __init__(self, default_speed=.5, max_speed=5.):
        self.max_speed = max_speed
        self.default_speed = validate_speed(default_speed, max_speed)
        self.heading = 0.
        self.owner = None
        self.seq = {}
        self.deadline = 0.
        self.move = self.turn = ''
        self.last = None
        self.speed = self.default_speed

    def stop(self):
        self.owner = None
        self.deadline = 0.
        self.move = self.turn = ''
        self.speed = self.default_speed

    def accept(self, x, now):
        allowed = {'client', 'seq', 'epoch', 'ticket', 'move', 'turn', 'speed', 'stop'}
        if set(x) - allowed:
            raise ValueError('不支持的 Planner 参数')
        client, seq = x.get('client'), x.get('seq')
        move, turn = x.get('move', ''), x.get('turn', '')
        if not isinstance(client, str) or not 1 <= len(client) <= 80 or type(seq) is not int or seq < 0:
            raise ValueError('无效控制会话')
        if move not in ('', 'forward', 'backward', 'left', 'right') or turn not in ('', 'left', 'right'):
            raise ValueError('无效行走方向')
        if type(x.get('stop', False)) is not bool:
            raise ValueError('无效停止参数')
        speed = validate_speed(x.get('speed', self.default_speed), self.max_speed)
        if seq <= self.seq.get(client, -1):
            raise ValueError('过期控制请求')
        if self.owner and now < self.deadline and self.owner != client and not x.get('stop'):
            raise ValueError('另一控制台正在操控')
        if len(self.seq) >= 512 and client not in self.seq:
            raise ValueError('控制会话过多，请重启网关')
        self.seq[client] = seq
        if x.get('stop') or not (move or turn):
            self.stop()
            return
        self.owner, self.deadline = client, now + .55
        self.move, self.turn, self.speed = move, turn, speed

    def sample(self, now, safe=True):
        dt = 0 if self.last is None else min(max(now-self.last, 0), .1)
        self.last = now
        if not safe or now >= self.deadline:
            self.stop()
        self.heading += (1 if self.turn == 'left' else -1 if self.turn == 'right' else 0)*math.radians(30)*dt
        c, s = math.cos(self.heading), math.sin(self.heading)
        x, y = {'':(0,0), 'forward':(1,0), 'backward':(-1,0), 'left':(0,1), 'right':(0,-1)}[self.move]
        return (1 if self.move or self.turn else 0, [x*c-y*s, x*s+y*c, 0], [c,s,0], self.speed if self.move else 0.)
