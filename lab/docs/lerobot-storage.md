# LeRobot v2.1 physical recordings

New SONIC recordings append to one LeRobot v2.1 dataset at the collection root.
There is no legacy recording replay fallback.

    data/chunk-000/episode_000000.parquet
    videos/chunk-000/observation.images.ego_view/episode_000000.mp4
    meta/info.json
    meta/tasks.jsonl
    meta/episodes.jsonl
    meta/episodes_stats.jsonl

LeRobot episode_index is globally contiguous. task_index maps task descriptions;
each task can have many episodes. Task-local display numbers and collector names
remain in OM metadata. episodes.jsonl carries an om extension for capture IDs,
model/config identity, alignment segments, hashes and stats. Official readers
ignore this extension.

## Content

- observation.state: float32 state32 (29 absolute body joints + gravity3).
- action: float32 motion token64 actually consumed by SONIC.
- Named body position/velocity/gravity/token fields and valid masks.
- Actual source indices, realtime/monotonic/receipt times, camera sequence,
  causal camera age and segment_index.
- RGB video: center crop to4:3, 640x480, H.264 CRF18.
- Standard frame/episode/task/global indices, task metadata, feature definitions
  and episode stats. Image stats use spatial stride4; numeric stats use all rows.

This follows OpenLambda's robot observation/action fields and human collector's
LeRobot v2.1 organization. Hands and raw root quaternion are unavailable in the
current capture interface: no fabricated columns or hand-equipped44D/76D claim.
Joint positions use loaded default angles and mapping. Velocities retain their
native post-observation-guard semantics.

No permanent encoder/decoder inputs, history windows, raw policy output,
per-frame protected-output dump, raw video transport, JPEGs or diagnostic logs.
Controller identity/layout metadata remains for replay; it contains no recorded
historical tensors.

## Time

Dataset timestamp and MP4 PTS use frame_index/50, matching sequential token
replay. This is the dataset/video timeline, not physical acquisition time.
Measured clocks remain unchanged in source.robot_*_ns. Camera matching stays
causal within75ms, with repeated images when required.

Source gaps and segment_index remain visible. OM rejects replay across segments.
Training pipelines with temporal windows must respect segment_index for gapped
recordings; LeRobot's generic loader does not interpret this custom field.

## Durability and execution

Capture writes temporary streams under episodes/<capture-id>. The background
worker converts them, pausing during new capture. Source integrity/consumption
and decoded video count are verified before committing typed Parquet and MP4.
Derived metadata precedes the atomic episodes.jsonl commit. Originals are
removed only after committed output hashes and row counts pass. Failed export
retains originals. Writer-lock recovery restores committed metadata and removes
uncommitted next-index files. Video encoding does not block the next capture.

Replay uses reference-plan v2 with sample_profile=lerobot_body_v21: tokens,
current body state and source clocks, never fake historical tensors. Model/config,
continuity, shape, finite-value, duration and gravity checks remain. Live feedback
still builds WBC history. OM owns timing, exclusive control, the protected bridge and native watchdog.
Normal completion and user stop return to Planner; execution faults request
protected exit. No browser lease is required. Progress reference IDs are explicit Parquet row locators.
Legacy plans are rejected. Diagnostic simulation requiring discarded histories
returns an explicit unavailable error; the interface remains.

Install capture extras (NumPy/PyArrow/PyAV) before deployment. Recording preflight
rejects missing export dependencies. No native SONIC rebuild is required.

## Official reader

Validated with LeRobot v0.3.3 / dataset version v2.1:

    ds = LeRobotDataset("om/local", root="/path/to/data", video_backend="pyav")
    frame = ds[0]

Offline OM commands:

    onboardmimic-lab inspect /path/to/data --episode-index 0
    onboardmimic-lab replay-plan /path/to/data --episode-index 0

Real replay is an explicitly started console operation; these commands never
send robot actions. Old recordings are not silently converted.

### Confirmed output sequence

New recordings preserve the inference index in `source.robot_index` and store
`source.consumption_sequence` and `source.consumption_segment` separately.
`quality.superseded_attempts` counts intermediate inference attempts proven to have
been superseded before protected output. Such attempts have no fabricated
training rows. Original timestamps and the token of each confirmed sample stay
unchanged. The compact replay plan uses the consumption sequence as `sample.index`
and preserves the original source index as `sample.inference_index`.

A recording only stays in one segment across a source-index jump when OM provides
complete non-commit evidence. Missing consumed publications, unknown gaps,
changed protected authority, camera rejection and gaps over the existing 60 ms
limit still break continuity. Playback remains sequential 50 Hz over recorded
confirmed tokens; original timing is retained for analysis, not reconstructed
from counters. Previously saved episodes are not silently reclassified.
