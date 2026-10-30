//! End to end through a fake duplex device: one frame counter for input and output, input and
//! output latency, callbacks on their own thread up to a block late, an xrun mid-take. The
//! singer is a loopback: the mic hears what the speaker plays, so a take aligned right is the
//! beat itself, frame for frame.

use redline_rec::engine::{engine, Config};
use redline_rec::protocol::Cause;
use redline_rec::wav::read;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

const RATE: u32 = 48000;
const BLOCK: usize = 64;
const LAT_IN: usize = 100;
const LAT_OUT: usize = 150;
const SKIPPED: usize = 2;

fn noise(n: usize) -> Vec<f32> {
    (0..n)
        .map(|i| (((i as f64 * 12.9898).sin() * 43758.5453).fract() as f32) * 0.5)
        .collect()
}

/// The lag of `take` behind `beat` over `range` of the beat that correlates best.
fn lag(take: &[f32], beat: &[f32], range: std::ops::Range<usize>) -> i64 {
    (-400i64..=400)
        .max_by(|&a, &b| {
            let score = |l: i64| -> f64 {
                range
                    .clone()
                    .filter_map(|i| {
                        let j = i as i64 + l;
                        (j >= 0 && (j as usize) < take.len())
                            .then(|| take[j as usize] as f64 * beat[i] as f64)
                    })
                    .sum()
            };
            score(a).total_cmp(&score(b))
        })
        .unwrap()
}

#[test]
fn a_take_through_a_device_with_latency_jitter_and_an_xrun_lands_on_beat_frame_zero() {
    let beat_l = noise(RATE as usize);
    let beat: Vec<[f32; 2]> = beat_l.iter().map(|&v| [v, -v]).collect();
    let (mut eng, mut control) = engine(Config::new(RATE, beat)).unwrap();
    // the monitor all but muted, so the loopback does not ring
    control
        .set_rack_json(
            r#"{"effects": [{"effect": "gain", "params": {"db": -60}}]}"#,
            None,
        )
        .unwrap();
    let xruns = eng.xrun_reporter();
    let started = Arc::new(AtomicBool::new(false));
    let quit = Arc::new(AtomicBool::new(false));

    let audio = {
        let started = started.clone();
        let quit = quit.clone();
        std::thread::spawn(move || {
            // indexed by device time; the counter is device time plus a base
            let base = 7_000_000u64;
            let mut speaker = vec![0.0f32; 1 << 20];
            let mut t = 0usize;
            let mut seed = 1u64;
            let mut xrun_in: Option<usize> = None;
            let mut mic = [0.0f32; BLOCK];
            let mut l = [0.0f32; BLOCK];
            let mut r = [0.0f32; BLOCK];
            while !quit.load(Ordering::Acquire) {
                seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1);
                let late = (seed >> 33) % 1334;
                std::thread::sleep(Duration::from_micros(late));
                if xrun_in.is_none() && started.load(Ordering::Acquire) {
                    xrun_in = Some(200);
                }
                if xrun_in == Some(0) {
                    // whole periods the device ran without a callback: the speaker stays
                    // silent and the input is lost
                    t += SKIPPED * BLOCK;
                    xruns.report();
                    xrun_in = Some(usize::MAX);
                } else if let Some(k) = xrun_in.as_mut().filter(|k| **k != usize::MAX) {
                    *k -= 1;
                }
                for (i, m) in mic.iter_mut().enumerate() {
                    // the cup at the mic: what the speaker played, heard LAT_IN later
                    *m = (t + i).checked_sub(LAT_IN).map_or(0.0, |p| speaker[p]);
                }
                eng.cycle(&mic, &mut l, &mut r, base + t as u64);
                for i in 0..BLOCK {
                    speaker[t + i + LAT_OUT] = l[i];
                }
                t += BLOCK;
                assert!(t + BLOCK + LAT_OUT < speaker.len());
            }
        })
    };

    std::thread::sleep(Duration::from_millis(30));
    let path = PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join("fake-take.wav");
    let _ = std::fs::remove_file(&path);
    control.start(&path, (LAT_IN + LAT_OUT) as u64).unwrap();
    started.store(true, Ordering::Release);
    while control.meters().take_frames < RATE as u64 + 2000 {
        std::thread::sleep(Duration::from_millis(5));
    }
    control.request_stop().unwrap();
    let report = control.wait_take(Duration::from_secs(10)).unwrap();
    quit.store(true, Ordering::Release);
    audio.join().unwrap();
    let take = read(&path).unwrap().samples;
    std::fs::remove_file(&path).unwrap();

    assert_eq!(take.len() as u64, report.frames);
    assert!(take.len() > beat_l.len());
    assert_eq!(report.dropouts.len(), 1, "{:?}", report.dropouts);
    let gap = &report.dropouts[0];
    assert_eq!(gap.frames, (SKIPPED * BLOCK) as u64);
    assert_eq!(gap.causes, [Cause::Xrun, Cause::CounterGap]);
    let d = gap.start as usize;
    assert!(
        d > 2000 && d + 2000 < beat_l.len(),
        "the xrun fell mid-beat: {d}"
    );
    assert!(take[d..d + SKIPPED * BLOCK].iter().all(|&v| v == 0.0));

    // within 1 ms of beat frame 0, before the xrun and after it
    let before = lag(&take, &beat_l, 500..d - 500);
    let after = lag(&take, &beat_l, d + 1000..beat_l.len() - 500);
    assert!(before.abs() <= 48 && after.abs() <= 48, "{before} {after}");
    // the round trip is known exactly here, so the take is the beat as heard, to the frame,
    // but for the -60 dB monitor heard along with it
    let unheard = d + LAT_IN + LAT_OUT;
    for (i, &b) in beat_l.iter().enumerate() {
        if (d..d + SKIPPED * BLOCK).contains(&i)
            || (unheard..unheard + SKIPPED * BLOCK).contains(&i)
        {
            continue;
        }
        assert!(
            (take[i] - b).abs() < 2e-3,
            "frame {i}: {} against {b}",
            take[i]
        );
    }
    // the periods the device skipped were never played, so the singer never heard them
    assert!(take[unheard..unheard + SKIPPED * BLOCK]
        .iter()
        .all(|v| v.abs() < 2e-3));
}
