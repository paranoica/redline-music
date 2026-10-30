//! The audio side's cycle must not allocate or free, through rack swaps, gaps and overflows.

use redline_rec::engine::{engine, Config};
use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::Cell;
use std::time::Duration;

struct Counting;

thread_local! {
    // per thread: the writer thread allocates as it likes
    static ALLOCATIONS: Cell<usize> = const { Cell::new(0) };
}

fn count() {
    let _ = ALLOCATIONS.try_with(|c| c.set(c.get() + 1));
}

unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        count();
        unsafe { System.alloc(layout) }
    }

    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        count();
        unsafe { System.alloc_zeroed(layout) }
    }

    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        count();
        unsafe { System.realloc(ptr, layout, new_size) }
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        count();
        unsafe { System.dealloc(ptr, layout) }
    }
}

#[global_allocator]
static COUNTING: Counting = Counting;

fn allocations(f: impl FnOnce()) -> usize {
    let before = ALLOCATIONS.with(Cell::get);
    f();
    ALLOCATIONS.with(Cell::get) - before
}

#[test]
fn the_counter_sees_an_allocation_and_a_free() {
    assert!(allocations(|| drop(std::hint::black_box(vec![0u8; 16]))) >= 2);
}

const RACK: &str = r#"{"effects": [
    {"effect": "eq"},
    {"effect": "compressor"},
    {"effect": "limiter", "params": {"lookahead": 1}},
    {"effect": "delay"}
]}"#;

#[test]
fn a_cycle_allocates_nothing_through_swaps_gaps_overflows_and_a_whole_take() {
    let beat = vec![[0.1f32, -0.1]; 48000];
    let mut config = Config::new(48000, beat);
    config.ring_frames = 4096;
    let (mut e, mut c) = engine(config).unwrap();
    let xruns = e.xrun_reporter();
    let path = std::path::PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join("realtime-take.wav");
    let _ = std::fs::remove_file(&path);
    let mic: Vec<f32> = (0..8192).map(|i| 0.3 * (i as f32 * 0.05).sin()).collect();
    let mut l = vec![0.0f32; 8192];
    let mut r = vec![0.0f32; 8192];
    let mut frame = 0u64;
    let mut total = 0;
    let mut returned = 0;
    for k in 0..200 {
        match k {
            5 => c.set_rack_json(RACK, None).unwrap().0,
            10 => {
                c.start(&path, 100).unwrap();
                0
            }
            // a swap while one still fades
            20 | 22 => c.set_rack_json(RACK, Some(48)).unwrap().0,
            40 => {
                xruns.report();
                frame += 256;
                0
            }
            // past the end of the beat
            60 => {
                frame += 48000;
                0
            }
            150 => {
                c.request_stop().unwrap();
                0
            }
            _ => 0,
        };
        // longer than the engine's chunk, and longer than the ring
        let n = match k {
            50 => 8192,
            51 => 1000,
            _ => 64,
        };
        let block = &mic[..n];
        let (out_l, out_r) = (&mut l[..n], &mut r[..n]);
        total += allocations(|| e.cycle(block, out_l, out_r, frame));
        frame += n as u64;
        if k == 100 {
            // let the writer drain, then racks come back
            std::thread::sleep(Duration::from_millis(50));
            returned += c.collect();
        }
    }
    let take = c.wait_take(Duration::from_secs(10)).unwrap();
    std::fs::remove_file(&path).unwrap();
    assert_eq!(total, 0);
    // the first rack, the one set at 5 and the one at 20, each after the fade to the next
    assert_eq!(returned + c.collect(), 3);
    assert!(take.dropouts.len() >= 2, "{:?}", take.dropouts);
    assert!(c.meters().overflows >= 1);
}
