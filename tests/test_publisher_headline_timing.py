from fractions import Fraction
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from src.publisher.headline_renderer import HeadlineRenderer, init_artifacts
from src.publisher.headline_timing import FrameTimeline, frame_timeline, validate_frame_timeline
from src.publisher.headline_tools import RenderError, run, resolve_ffmpeg
from tests.test_publisher_headline_renderer import FONT, ROOT, snapshot


def timeline(points, tick='1/90000', duration=3000):
    return frame_timeline([{'best_effort_timestamp':p,'pkt_duration':duration}
                           for p in points], tick)


class PresentationTimelineTests(unittest.TestCase):
    def test_cfr_and_normalized_start(self):
        validate_frame_timeline(timeline([0,3000,6000]),timeline([120,3120,6120]))

    def test_vfr(self):
        validate_frame_timeline(timeline([0,3000,7350,9000]),timeline([90,3090,7440,9090]))

    def test_exact_time_base_rescale(self):
        validate_frame_timeline(timeline([0,3000,6000]),timeline([0,1000,2000],'1/30000',1000))

    def test_packetization_is_not_frame_authority(self):
        source={'frames':[{'best_effort_timestamp':p,'pkt_duration':3000} for p in (0,3000,6000)],
                'packets':[{'pts':0},{'pts':3000},{'pts':6000}]}
        output={'frames':source['frames'],'packets':[{'pts':6000},{'pts':0},{'pts':None},{'pts':3000}]}
        validate_frame_timeline(frame_timeline(source['frames'],'1/90000'),
                                frame_timeline(output['frames'],'1/90000'))

    def test_dropped_and_added_frame_rejected(self):
        for pts in ([0,6000],[0,1500,3000,6000]):
            with self.subTest(pts=pts),self.assertRaisesRegex(RenderError,'CADENCE_INVALID'):
                validate_frame_timeline(timeline([0,3000,6000]),timeline(pts))

    def test_duplicate_presentation_instant_rejected(self):
        with self.assertRaisesRegex(RenderError,'TIMING_UNSUPPORTED'):
            timeline([0,3000,3000])

    def test_true_drift_rejected(self):
        with self.assertRaisesRegex(RenderError,'CADENCE_INVALID'):
            validate_frame_timeline(timeline([0,3000,6000]),timeline([0,3180,6360]))

    def test_truncated_extent_rejected(self):
        source=timeline([0,3000,6000])
        output=FrameTimeline(source.times,Fraction(3000,90000),source.time_base)
        with self.assertRaisesRegex(RenderError,'CADENCE_INVALID'):
            validate_frame_timeline(source,output)

    def test_quantization_tolerance_bounded_by_frame_interval(self):
        with self.assertRaisesRegex(RenderError,'CADENCE_INVALID'):
            validate_frame_timeline(timeline([0,1,2],'1/30',1),timeline([0,2,3],'1/30',1))

    def test_missing_timestamp_and_invalid_timebase_fail_closed(self):
        for frames,tick in (([], '1/30'),([{}],'1/30'),([{'best_effort_timestamp':0}],'0')):
            with self.subTest(frames=frames,tick=tick),self.assertRaises(RenderError):
                frame_timeline(frames,tick)


class RealVideoCadenceRegressionTests(unittest.TestCase):
    def test_small_vfr_discontinuity_like_canary_preserved(self):
        with tempfile.TemporaryDirectory(prefix='publisher-cadence-') as directory:
            root=Path(directory); inputs=root/'inputs';inputs.mkdir()
            source=inputs/'small-vfr-jumps.mp4';old=root/'old-default-timebase.mp4'
            ffmpeg=resolve_ffmpeg(ROOT)
            run([ffmpeg,'-v','error','-y','-f','lavfi','-i','testsrc2=size=360x640:rate=30',
                 '-frames:v','36','-vf','settb=1/15360,setpts=N*512+if(gte(N\\,12)\\,215\\,0)+if(gte(N\\,24)\\,61\\,0)',
                 '-vsync','0','-enc_time_base:v','1/15360','-c:v','libx264',source])
            run([ffmpeg,'-v','error','-y','-i',source,'-vsync','0','-c:v','libx264',old])
            renderer=HeadlineRenderer(ROOT,inputs,root/'artifacts',{'fixture':FONT})
            before=renderer.presentation_timeline(source); rounded=renderer.presentation_timeline(old)
            with self.assertRaisesRegex(RenderError,'CADENCE_INVALID'):
                validate_frame_timeline(before,rounded)
            # This is the previous packet-level acceptance rule, applied to the
            # previous default-timebase render, not an operational video fixture.
            def packets(path):
                data=json.loads(run([renderer.ffprobe,'-v','error','-select_streams','v:0',
                    '-show_packets','-show_entries','packet=pts_time','-of','json',path]))
                values=sorted(Fraction(p['pts_time']) for p in data['packets'])
                return [p-values[0] for p in values]
            first,second=packets(source),packets(old)
            self.assertTrue(len(first)!=len(second) or any(abs(a-b)>Fraction(2,15360) for a,b in zip(first,second)))
            conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row
            try:
                init_artifacts(conn)
                result=renderer.render(conn,1,snapshot(),source)
                self.assertEqual(result['state'],'READY')
                validate_frame_timeline(before,renderer.presentation_timeline(result['local_path']))
            finally:
                conn.close()
