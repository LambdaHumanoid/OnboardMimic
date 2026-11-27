"""Prove skipped attempts were not committed; missing evidence stays a gap.

Only bounded metadata is retained, never tensors. Transport publication counters
cover both input sockets. A receiver restart cannot continue a previous proof.
"""
from collections import OrderedDict


class ConsumptionContinuity:
    def __init__(self):
        self.sources = {}
        self.epoch = 0
        self.previous = None
        self.sequence = 0
        self.segment = 0
        self.attempts = OrderedDict()
        self.mappings = OrderedDict()
        self.commits = OrderedDict()
        self.watermark = None
        self.unselected = OrderedDict()

    def observe(self, source, event):
        publication = event.get('publication_sequence')
        old = self.sources.get(source)
        counters = tuple(event.get(k, old[2][i] if old else 0)
                         for i, k in enumerate(('queue_drops', 'serialization_drops')))
        state = (event['native_session'], publication, counters)
        if (type(publication) is not int or publication < 1 or old is None or
                old[0] != state[0] or type(old[1]) is not int or publication != old[1]+1 or old[2] != counters):
            self.epoch += 1
        self.sources[source] = state
        kind = event['kind']
        if kind == 0:
            self.attempts[event['index']] = (event['generation'], event['inference_ok'] is True and event['command_ok'] is True)
            if not self.attempts[event['index']][1]: self.epoch += 1
            replacement = event.get('buffer_replacement', {})
            if (replacement.get('boundary') == 'serialized_writer_selection'
                and replacement.get('selected') is False
                and type(replacement.get('index')) is int
                and 0 < replacement['index'] < event['index']
                and type(replacement.get('generation')) is int
                and replacement['generation'] == event['generation']):
                self.unselected[replacement['index']] = (replacement['generation'], self.epoch)
        elif kind == 1:
            self.mappings[(event['stream'], event['seq'])] = (event['index'], event['generation'])
        elif kind == 2:
            key = (event['stream'], event['seq'])
            self.commits[key] = True
            if event['authority'] is not True: self.epoch += 1
            if self.watermark and (key[0] != self.watermark[0] or key[1] < self.watermark[1]): self.epoch += 1
            self.watermark = key
        for values in (self.attempts, self.mappings, self.commits, self.unselected):
            while len(values) > 4096: values.popitem(last=False)

    def annotate(self, sample, generation):
        prev = self.previous
        index = sample['index']; commit = sample['output_commit']
        reason = 'start'; skipped = 0; diagnostic = None
        if prev:
            old, epoch = prev
            reason = 'adjacent' if index == old['index']+1 else 'unresolved'
            same = (sample['session_id'] == old['session_id']
                    and commit['stream'] == old['output_commit']['stream']
                    and commit['generation'] == old['output_commit']['generation'])
            if not same: reason = 'evidence_discontinuity'
            elif epoch == self.epoch and 1 < index-old['index'] <= 64:
                missing = set(range(old['index']+1, index))
                mapped = {i: [] for i in missing}
                for key, (i, gen) in self.mappings.items():
                    if i in missing: mapped[i].append((key, gen))
                diagnostic = dict(epoch_unchanged=True, sources_loss_free=all(v[2]==(0,0) for v in self.sources.values()),
                    attempts=[dict(index=i, inference=self.attempts.get(i), sends=[dict(stream=k[0],sequence=k[1],generation=g,committed=k in self.commits) for k,g in mapped[i]]) for i in sorted(missing)])
                # Require actual inference AND send evidence for every skipped
                # attempt. No send, truncated history or out-of-order coverage
                # cannot be converted into a claim of non-execution.
                # Drop counters are sampled at publication, not at production.
                # A drop can therefore be reported on an older queued record.
                # Never prove absence after any producer loss in this session.
                loss_free = all(v[2] == (0, 0) for v in self.sources.values())
                # Absence of send events alone is never proof. An atomic buffer
                # replacement can prove the sole writer never selected this item.
                never_selected = lambda i: not mapped[i] and self.unselected.get(i) == (generation, self.epoch)
                proven = loss_free and all(self.attempts.get(i) == (generation, True)
                    and (mapped[i] or never_selected(i)) for i in missing)
                proven = proven and all(gen == generation and key[0] == commit['stream']
                    and old['output_commit']['sequence'] < key[1] < commit['sequence']
                    and key not in self.commits for entries in mapped.values() for key, gen in entries)
                # The next native mapping and protected commit form watermarks;
                # per-socket publication order must have remained complete.
                if proven:
                    reason = 'superseded_before_commit'; skipped = len(missing)
            if reason not in ('adjacent', 'superseded_before_commit'): self.segment += 1
        self.sequence += 1
        sample['consumption'] = dict(schema=1, sequence=self.sequence, segment=self.segment,
            previous_inference_index=prev[0]['index'] if prev else None,
            reason=reason, skipped_attempts=skipped)
        if reason=='unresolved':
            sample['consumption']['diagnostic']=diagnostic or dict(epoch_unchanged=prev[1]==self.epoch,
                index_delta=index-prev[0]['index'],sources_loss_free=all(v[2]==(0,0) for v in self.sources.values()))
        self.previous = (dict(index=index,session_id=sample['session_id'],output_commit=dict(commit)), self.epoch)
        return sample['consumption']
