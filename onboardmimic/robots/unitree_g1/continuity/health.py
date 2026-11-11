import math

class FeedbackHealth:
    def __init__(self):
        self.status = None
        self.changed_at = None
        self.retired = set()

    def ingest(self, value, now):
        try:
            valid = (value['protocol'] == 1 and isinstance(value['session'], str) and
                     0 < len(value['session']) <= 64 and value['session'] not in self.retired and
                     type(value['sequence']) is int and value['sequence'] > 0 and
                     type(value['generation']) is int and value['generation'] >= 0 and
                     type(value['at']) in (int, float) and math.isfinite(value['at']) and
                     0 <= now-value['at'] <= .2 and value['source'] in ('primary', 'standby', None))
            if not valid:
                return False
            old = self.status
            if old and old['session'] == value['session']:
                if (value['sequence'] <= old['sequence'] or value['at'] <= old['at'] or
                        value['generation'] < old['generation']):
                    return False
            elif old:
                self.retired.add(old['session'])
            changed = old is None or any(old[k] != value[k] for k in ('session', 'generation', 'source'))
            self.status = dict(value)
            if changed:
                self.changed_at = now
            return changed
        except (KeyError, TypeError, ValueError, OverflowError):
            return False

    def ready(self, now, latest):
        return bool(self.status and self.status['source'] and
                    0 <= now-self.status['at'] <= .3 and self.changed_at is not None and
                    now-self.changed_at >= .04 and
                    all(t in latest and 0 <= now-latest[t][0] <= .05
                        and latest[t][0] > self.changed_at
                        for t in ('rt/lowstate', 'rt/secondary_imu')))
