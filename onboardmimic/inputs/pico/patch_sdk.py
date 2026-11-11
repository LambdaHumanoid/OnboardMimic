"""Expand the pinned upstream independent-tracker decoder from 3 to 16 entries."""
import argparse
import hashlib
import re
from pathlib import Path

PIN='8941e1dbbf4313fe83846f82984eaa8c992690bf'

def patch(source):
    marker='// OnboardMimic: independent tracker capacity 16; receive-only adaptation.'
    # Recognize an existing patch by its invariant description, including older branding.
    description = marker.split(': ', 1)[1]
    source = re.sub(r'^// [^\n]+: ' + re.escape(description) + r'$', lambda _: marker, source, flags=re.MULTILINE)
    if marker in source:return patch_body_clock(source)
    replacements={
        'bool BodyDataAvailable = false;':'int64_t HeadTimeStampNs = 0;\nbool BodyDataAvailable = false;',
        'HeadsetPose = stringToPoseArray(headset["pose"].get<std::string>());':'HeadsetPose = stringToPoseArray(headset["pose"].get<std::string>());\n                        HeadTimeStampNs = value.value("timeStampNs", int64_t{0});',
        'void init() {':'int64_t getHeadTimeStampNs() { std::lock_guard<std::mutex> lock(headsetPoseMutex); return HeadTimeStampNs; }\n\nvoid init() {',
        '    XRT_FUNCTION("get_time_stamp_ns",':'    XRT_FUNCTION("get_head_timestamp_ns", getHeadTimeStampNs, "int", "Head-only timestamp; other packets do not renew it."),\n    XRT_FUNCTION("get_time_stamp_ns",',
        'std::array<std::array<double, 7>, 3> MotionTrackerPose':'std::array<std::array<double, 7>, 16> MotionTrackerPose',
        'std::array<std::array<double, 6>, 3> MotionTrackerVelocity':'std::array<std::array<double, 6>, 16> MotionTrackerVelocity',
        'std::array<std::array<double, 6>, 3> MotionTrackerAcceleration':'std::array<std::array<double, 6>, 16> MotionTrackerAcceleration',
        'std::array<std::string, 3> MotionTrackerSerialNumbers':'std::array<std::string, 16> MotionTrackerSerialNumbers',
        'NumMotionDataAvailable = std::min(static_cast<int>(joints.size()), 3);':'NumMotionDataAvailable = std::min(static_cast<int>(joints.size()), 16);'}
    for old,new in replacements.items():
        if source.count(old)!=1:raise ValueError('SDK source changed: '+old)
        source=source.replace(old,new)
    return patch_body_clock(marker+'\n'+source.replace('(3 trackers,','(up to 16 trackers,'))

def patch_body_clock(source):
    marker='// OnboardMimic: body clock from complete body packet, never unrelated head updates.'
    # Recognize an existing patch by its invariant description, including older branding.
    description = marker.split(': ', 1)[1]
    source = re.sub(r'^// [^\n]+: ' + re.escape(description) + r'$', lambda _: marker, source, flags=re.MULTILINE)
    if marker in source:return source
    old='''                        if (body.contains("timeStampNs")) {
                            BodyTimeStampNs = body["timeStampNs"].get<int64_t>();
                        }
                        
                        if (body.contains("joints") && body["joints"].is_array()) {'''
    new='''                        // Clear availability before parsing: malformed/partial body
                        // packets must not expose cached joints as a fresh skeleton.
                        BodyDataAvailable = false;
                        BodyTimeStampNs = 0;
                        if (body.contains("joints") && body["joints"].is_array() &&
                            body["joints"].size() == 24) {
                            for (const auto& joint : body["joints"]) {
                                if (!joint.contains("p") || !joint["p"].is_string())
                                    return; // Reject this packet without escaping the C callback.
                            }'''
    if source.count(old)!=1:raise ValueError('SDK body clock layout changed')
    source=source.replace(old,new)
    old='                            BodyDataAvailable = true;'
    new='''                            // Some Unity clients omit Body.timeStampNs and send
                            // only the containing value.timeStampNs. This measures
                            // body packet freshness, not sensor acquisition latency.
                            int64_t stamp = body.value("timeStampNs", int64_t{0});
                            if (stamp <= 0) stamp = value.value("timeStampNs", int64_t{0});
                            BodyTimeStampNs = stamp;
                            BodyDataAvailable = stamp > 0;'''
    if source.count(old)!=1:raise ValueError('SDK body availability layout changed')
    return marker+'\n'+source.replace(old,new)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);a=p.parse_args()
    a.source.write_text(patch(a.source.read_text()))
if __name__=='__main__':main()
